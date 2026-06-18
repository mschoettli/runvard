"""Netzwerk: Interfaces anzeigen, Bond erstellen, Firewall."""
import os
import json
import ipaddress
import re
import socket
import subprocess

try:
    import psutil
except ImportError:
    psutil = None

from modules import validators


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def _valid_ip(value: str, label: str = "IP"):
    try:
        ipaddress.IPv4Address(str(value))
        return str(value)
    except ValueError:
        raise ValueError(f"Ungueltige {label}")


def _valid_prefix(value):
    try:
        prefix = int(value)
    except (TypeError, ValueError):
        raise ValueError("Ungueltige Netzmaske")
    if not (0 <= prefix <= 32):
        raise ValueError("Ungueltige Netzmaske")
    return str(prefix)


def _valid_dns_servers(value: str):
    value = str(value or "").strip()
    if not value:
        return ""
    servers = [s for s in re.split(r"[\s,]+", value) if s]
    return " ".join(_valid_ip(server, "DNS-Adresse") for server in servers)


def list_interfaces():
    """Alle Netzwerk-Interfaces mit IP, Status, Speed."""
    if psutil is None:
        return {"ok": False, "interfaces": [], "stderr": "psutil nicht installiert"}
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
    except Exception as exc:
        return {"ok": False, "interfaces": [], "stderr": str(exc)}

    interfaces = []
    try:
        for name, addr_list in addrs.items():
            if name == "lo":
                continue
            ipv4 = next((a.address for a in addr_list if a.family == socket.AF_INET), None)
            mac_families = [
                family
                for family in (
                    getattr(socket, "AF_PACKET", None),
                    getattr(socket, "AF_LINK", None),
                )
                if family is not None
            ]
            mac = next((a.address for a in addr_list if a.family in mac_families), None)
            st = stats.get(name)
            ctr = counters.get(name)
            interfaces.append({
                "name": name,
                "ipv4": ipv4,
                "mac": mac,
                "up": st.isup if st else False,
                "speed": st.speed if st else 0,
                "mtu": st.mtu if st else 0,
                "is_bond": name.startswith("bond"),
                "bytes_sent": ctr.bytes_sent if ctr else 0,
                "bytes_recv": ctr.bytes_recv if ctr else 0,
            })
    except Exception as exc:
        return {"ok": False, "interfaces": [], "stderr": str(exc)}
    return {"ok": True, "interfaces": interfaces}


def bond_members(bond_name: str):
    """Member-Interfaces eines Bonds."""
    bond_name = validators.require_netdev(bond_name, "bond name")
    path = f"/sys/class/net/{bond_name}/bonding/slaves"
    try:
        with open(path) as f:
            return f.read().split()
    except OSError:
        return []


def create_bond(bond_name: str, members: list, mode="802.3ad"):
    """Bond-Interface anlegen.

    mode: balance-rr, active-backup, 802.3ad (LACP)
    Schreibt nach /etc/network/interfaces.d/ für Persistenz.
    """
    bond_name = validators.require_netdev(bond_name, "bond name")
    members = [validators.require_netdev(m.strip()) for m in members if m.strip()]
    if not members:
        return {"ok": False, "stderr": "Mindestens ein Interface erforderlich"}
    if mode not in ("balance-rr", "active-backup", "802.3ad"):
        return {"ok": False, "stderr": "Ungueltiger Bond-Modus"}

    # Laufzeit-Erstellung via ip
    cmds = [
        ["ip", "link", "add", bond_name, "type", "bond", "mode", mode],
    ]
    for m in members:
        cmds.append(["ip", "link", "set", m, "down"])
        cmds.append(["ip", "link", "set", m, "master", bond_name])
    cmds.append(["ip", "link", "set", bond_name, "up"])

    results = []
    for cmd in cmds:
        result = _run(cmd)
        results.append(result)
        if not result["ok"]:
            return {"ok": False, "stderr": result.get("stderr", ""), "steps": results}

    # Persistenz
    cfg = f"""auto {bond_name}
iface {bond_name} inet dhcp
    bond-mode {mode}
    bond-slaves {' '.join(members)}
    bond-miimon 100
"""
    try:
        with open(f"/etc/network/interfaces.d/{bond_name}", "w") as f:
            f.write(cfg)
    except OSError as e:
        return {"ok": False, "error": str(e), "steps": results}

    return {"ok": all(r["ok"] for r in results), "steps": results}


def delete_bond(bond_name: str):
    bond_name = validators.require_netdev(bond_name, "bond name")
    _run(["ip", "link", "set", bond_name, "down"])
    r = _run(["ip", "link", "delete", bond_name])
    import os
    try:
        os.remove(f"/etc/network/interfaces.d/{bond_name}")
    except OSError:
        pass
    return r


def set_static_ip(iface: str, ip: str, netmask: str, gateway: str):
    """Statische IP setzen (Laufzeit + Persistenz)."""
    iface = validators.require_netdev(iface)
    ip = _valid_ip(ip)
    netmask = _valid_prefix(netmask)
    if gateway:
        gateway = _valid_ip(gateway, "Gateway")
    r = _run(["ip", "addr", "flush", "dev", iface])
    if not r["ok"]:
        return r
    r = _run(["ip", "addr", "add", f"{ip}/{netmask}", "dev", iface])
    if not r["ok"]:
        return r
    if gateway:
        r = _run(["ip", "route", "add", "default", "via", gateway])
    return r


def configure_ip(iface, mode="static", ip="", netmask="24", gateway="", dns="", persist=False):
    """Interface-IP konfigurieren: Laufzeit + optional persistent (Debian/ifupdown)."""
    iface = validators.require_netdev(iface)
    if mode not in ("static", "dhcp"):
        return {"ok": False, "stderr": "Ungueltiger Modus"}
    if mode == "dhcp":
        r = _run(["ip", "addr", "flush", "dev", iface])
        if not r["ok"]:
            return r
        r = _run(["dhclient", "-1", iface])
    else:
        if not ip:
            return {"ok": False, "stderr": "IP erforderlich"}
        try:
            ip = _valid_ip(ip)
            netmask = _valid_prefix(netmask)
            gateway = _valid_ip(gateway, "Gateway") if gateway else ""
            dns = _valid_dns_servers(dns)
        except ValueError as exc:
            return {"ok": False, "stderr": str(exc)}
        r = _run(["ip", "addr", "flush", "dev", iface])
        if not r["ok"]:
            return r
        r = _run(["ip", "addr", "add", f"{ip}/{netmask}", "dev", iface])
        if not r["ok"]:
            return r
        r = _run(["ip", "link", "set", iface, "up"])
        if not r["ok"]:
            return r
        if gateway:
            r = _run(["ip", "route", "replace", "default", "via", gateway])
            if not r["ok"]:
                return r
    runtime_ok = r["ok"]
    if persist:
        try:
            os.makedirs("/etc/network/interfaces.d", exist_ok=True)
            cfg = f"auto {iface}\n"
            if mode == "dhcp":
                cfg += f"iface {iface} inet dhcp\n"
            else:
                cfg += f"iface {iface} inet static\n    address {ip}/{netmask}\n"
                if gateway:
                    cfg += f"    gateway {gateway}\n"
                if dns:
                    cfg += f"    dns-nameservers {dns}\n"
            with open(f"/etc/network/interfaces.d/runvard-{iface}.cfg", "w") as f:
                f.write(cfg)
        except OSError as e:
            return {"ok": runtime_ok, "stderr": f"Laufzeit gesetzt, Persistenz fehlgeschlagen: {e}"}
    return {"ok": runtime_ok, "stderr": r.get("stderr", "")}


def create_bridge(name, members):
    name = validators.require_netdev(name, "bridge name")
    members = [validators.require_netdev(m.strip()) for m in members if m.strip()]
    r = _run(["ip", "link", "add", "name", name, "type", "bridge"])
    if not r["ok"]:
        return r
    r = _run(["ip", "link", "set", name, "up"])
    if not r["ok"]:
        return r
    for m in members:
        if m:
            r = _run(["ip", "link", "set", m, "master", name])
            if not r["ok"]:
                return r
            r = _run(["ip", "link", "set", m, "up"])
            if not r["ok"]:
                return r
    return {"ok": True}


def create_vlan(parent, vlan_id, name=""):
    parent = validators.require_netdev(parent, "parent interface")
    try:
        vid = int(vlan_id)
        if not (1 <= vid <= 4094):
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltige VLAN-ID"}
    name = validators.require_netdev(name or f"{parent}.{vid}", "VLAN name")
    r = _run(["ip", "link", "add", "link", parent, "name", name,
              "type", "vlan", "id", str(vid)])
    if not r["ok"]:
        return r
    return _run(["ip", "link", "set", name, "up"])


def delete_link(name):
    name = validators.require_netdev(name, "link name")
    return _run(["ip", "link", "delete", name])


# --- Firewall (nftables/iptables via ufw wenn vorhanden) ---

def firewall_status():
    r = _run(["ufw", "status", "numbered"])
    if r["ok"]:
        return {"backend": "ufw", "status": r["stdout"]}
    r = _run(["iptables", "-L", "-n", "--line-numbers"])
    return {"backend": "iptables", "status": r["stdout"]}


def firewall_add_rule(port, proto="tcp", action="allow"):
    try:
        port = validators.require_int_range(port, 1, 65535, "port")
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltiger Port"}
    if proto not in ("tcp", "udp"):
        return {"ok": False, "stderr": "Ungueltiges Protokoll"}
    if action not in ("allow", "deny", "reject", "limit"):
        return {"ok": False, "stderr": "Ungueltige Aktion"}
    return _run(["ufw", action, f"{port}/{proto}"])


def firewall_rules():
    """Strukturierte UFW-Regelliste fuer die UI."""
    status = _run(["ufw", "status", "numbered"])
    if not status["ok"]:
        ipt = _run(["iptables", "-L", "-n", "--line-numbers"])
        return {"backend": "iptables", "active": False, "manageable": False,
                "rules": [], "raw": ipt["stdout"]}
    out = status["stdout"]
    active = "Status: active" in out
    rules = []
    for line in out.splitlines():
        m = re.match(r"^\[\s*(\d+)\]\s+(.*\S)\s*$", line)
        if not m:
            continue
        rest = re.split(r"\s{2,}", m.group(2).strip())
        rules.append({
            "num": int(m.group(1)),
            "to": rest[0] if len(rest) > 0 else "",
            "action": rest[1] if len(rest) > 1 else "",
            "from": rest[2] if len(rest) > 2 else "",
        })
    return {"backend": "ufw", "active": active, "manageable": True, "rules": rules}


def firewall_remove_rule(num):
    try:
        num = validators.require_int_range(num, 1, 99999, "rule number")
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltige Regelnummer"}
    return _run(["ufw", "--force", "delete", str(num)])
