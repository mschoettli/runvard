"""VMs: KVM/QEMU über libvirt verwalten - inkl. erstellen/löschen."""
import json
import os
import subprocess
import tempfile
import time

try:
    import libvirt
    HAS_LIBVIRT = True
except ImportError:
    HAS_LIBVIRT = False

import xml.etree.ElementTree as ET

_conn = None
ISO_DIR = "/var/lib/libvirt/images"
LIBVIRT_URI = "qemu:///system"


def _connect():
    global _conn
    if not HAS_LIBVIRT:
        raise RuntimeError("libvirt-python nicht installiert")
    if _conn is None or not _conn.isAlive():
        _conn = libvirt.open(LIBVIRT_URI)
    return _conn


def available():
    if HAS_LIBVIRT:
        try:
            _connect()
            return True
        except Exception:
            pass
    return _virsh(["list", "--all"], timeout=15)["ok"]


_STATES = {
    0: "no state", 1: "running", 2: "blocked", 3: "paused",
    4: "shutting down", 5: "shut off", 6: "crashed", 7: "suspended",
}


def list_vms():
    if HAS_LIBVIRT:
        try:
            vms = _list_vms_libvirt()
            if vms:
                return vms
        except Exception:
            pass
    return _list_vms_virsh()


def diagnostics():
    """Return VM subsystem facts without changing host state."""
    libvirt_error = ""
    libvirt_domains = []
    if HAS_LIBVIRT:
        try:
            libvirt_domains = _list_vms_libvirt()
        except Exception as e:
            libvirt_error = str(e)
    else:
        libvirt_error = "libvirt-python nicht installiert"

    virsh_list = _virsh(["list", "--all"], timeout=15)
    networks = _virsh(["net-list", "--all"], timeout=15)
    pools = _virsh(["pool-list", "--all"], timeout=15)
    images = []
    try:
        if os.path.isdir(ISO_DIR):
            images = sorted(os.listdir(ISO_DIR))
    except Exception as e:
        images = [f"error: {e}"]

    return {
        "uri": LIBVIRT_URI,
        "has_libvirt_python": HAS_LIBVIRT,
        "libvirt_error": libvirt_error,
        "libvirt_domains": libvirt_domains,
        "virsh": {
            "list_all": virsh_list,
            "net_list_all": networks,
            "pool_list_all": pools,
        },
        "parsed_vms": list_vms(),
        "iso_dir": ISO_DIR,
        "iso_dir_exists": os.path.isdir(ISO_DIR),
        "iso_dir_entries": images[:100],
    }


def _list_vms_libvirt():
    conn = _connect()
    vms = []
    for dom in conn.listAllDomains():
        row = _domain_summary_libvirt(dom)
        if row:
            vms.append(row)
    return vms


def _domain_summary_libvirt(dom):
    try:
        name = dom.name()
    except Exception:
        return None
    row = {
        "name": name,
        "uuid": "",
        "state": "unknown",
        "active": False,
        "autostart": False,
        "max_mem": 0,
        "mem": 0,
        "vcpus": 0,
    }
    try:
        state, _ = dom.state()
        row["state"] = _STATES.get(state, "unknown")
    except Exception:
        pass
    try:
        info = dom.info()
        row["max_mem"] = info[1] * 1024
        row["mem"] = info[2] * 1024
        row["vcpus"] = info[3]
    except Exception:
        pass
    try:
        row["uuid"] = dom.UUIDString()
    except Exception:
        pass
    try:
        row["active"] = dom.isActive() == 1
    except Exception:
        row["active"] = row["state"] == "running"
    try:
        row["autostart"] = dom.autostart() == 1
    except Exception:
        pass
    config = _domain_config_resources(name, dom)
    if config:
        row["max_mem"] = config.get("max_mem") or row["max_mem"]
        row["vcpus"] = config.get("vcpus") or row["vcpus"]
    return row


def _list_vms_virsh():
    r = _virsh(["list", "--all"], timeout=15)
    if not r["ok"]:
        return []
    vms = []
    for line in r["stdout"].splitlines()[2:]:
        line = line.strip()
        if not line or set(line) == {"-"}:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        name = parts[1]
        state = parts[2] if len(parts) >= 3 else "unknown"
        vms.append(_domain_summary_virsh(name, state))
    return vms


def _domain_summary_virsh(name, state="unknown"):
    info = _virsh(["dominfo", name], timeout=15)
    data = _parse_dominfo(info["stdout"]) if info["ok"] else {}
    state = data.get("State") or state
    row = {
        "name": name,
        "uuid": data.get("UUID", ""),
        "state": state,
        "active": state.lower() not in {"shut off", "crashed", "unknown"},
        "autostart": data.get("Autostart", "").lower() in {"enable", "enabled", "yes"},
        "max_mem": _parse_virsh_memory(data.get("Max memory", "")),
        "mem": _parse_virsh_memory(data.get("Used memory", "")),
        "vcpus": _parse_int(data.get("CPU(s)", "0")),
    }
    config = _domain_config_resources(name)
    if config:
        row["max_mem"] = config.get("max_mem") or row["max_mem"]
        row["vcpus"] = config.get("vcpus") or row["vcpus"]
    return row


def _xml_memory_bytes(node):
    if node is None or not (node.text or "").strip():
        return 0
    try:
        value = int(node.text.strip())
    except Exception:
        return 0
    unit = (node.get("unit") or "KiB").lower()
    if unit in {"b", "bytes"}:
        return value
    if unit in {"kb", "kib"}:
        return value * 1024
    if unit in {"mb", "mib"}:
        return value * 1024 * 1024
    if unit in {"gb", "gib"}:
        return value * 1024 * 1024 * 1024
    return value * 1024


def _domain_config_resources(name, dom=None):
    xml = ""
    dumped = _virsh(["dumpxml", "--inactive", name], timeout=15)
    if dumped["ok"]:
        xml = dumped["stdout"]
    if not xml and dom is not None and hasattr(dom, "XMLDesc"):
        try:
            flags = getattr(libvirt, "VIR_DOMAIN_XML_INACTIVE", 0) if HAS_LIBVIRT else 0
            xml = dom.XMLDesc(flags)
        except Exception:
            xml = ""
    if not xml:
        return {}
    try:
        root = ET.fromstring(xml)
        return {
            "max_mem": _xml_memory_bytes(root.find("./memory")),
            "vcpus": _parse_int((root.findtext("./vcpu") or "0").strip()),
        }
    except Exception:
        return {}


def _parse_dominfo(text):
    data = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _parse_virsh_memory(value):
    parts = value.split()
    if not parts:
        return 0
    try:
        number = float(parts[0])
    except ValueError:
        return 0
    unit = parts[1].lower() if len(parts) > 1 else "b"
    factor = {
        "b": 1,
        "bytes": 1,
        "kib": 1024,
        "kb": 1024,
        "mib": 1024**2,
        "mb": 1024**2,
        "gib": 1024**3,
        "gb": 1024**3,
    }.get(unit, 1)
    return int(number * factor)


def _parse_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


_VIRSH_VM_ACTIONS = {
    "start": lambda name: ["start", name],
    "shutdown": lambda name: ["shutdown", name, "--mode", "acpi"],
    "reboot": lambda name: ["reboot", name],
    "force-off": lambda name: ["destroy", name],
    "autostart-on": lambda name: ["autostart", name],
    "autostart-off": lambda name: ["autostart", name, "--disable"],
}


def _vm_action_libvirt(name, action):
    conn = _connect()
    dom = conn.lookupByName(name)
    if action == "start":
        dom.create()
    elif action == "shutdown":
        dom.shutdown()
    elif action == "reboot":
        dom.reboot()
    elif action == "force-off":
        dom.destroy()
    elif action == "autostart-on":
        dom.setAutostart(1)
    elif action == "autostart-off":
        dom.setAutostart(0)
    elif action == "delete":
        if dom.isActive():
            dom.destroy()
        dom.undefineFlags(
            getattr(libvirt, "VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA", 0))
        return {"ok": True, "deleted": True}
    else:
        raise ValueError("Unbekannte Aktion")
    return {"ok": True}


def _vm_action_virsh(name, action):
    if action == "delete":
        state = _virsh(["domstate", name], timeout=15)
        if state["ok"] and "running" in state["stdout"].lower():
            stopped = _virsh(["destroy", name], timeout=30)
            if not stopped["ok"]:
                return stopped
        return _virsh(["undefine", name, "--snapshots-metadata"], timeout=30)
    command = _VIRSH_VM_ACTIONS.get(action)
    if command is None:
        return {"ok": False, "stderr": "Unbekannte Aktion"}
    return _virsh(command(name), timeout=30)


def vm_action(name, action):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    try:
        return _vm_action_libvirt(name, action)
    except Exception:
        fallback = _vm_action_virsh(name, action)
        if fallback["ok"]:
            result = {"ok": True}
            if action == "delete":
                result["deleted"] = True
            return result
        return fallback


def list_isos():
    """Verfügbare ISO-Dateien im Image-Verzeichnis."""
    isos = []
    if os.path.isdir(ISO_DIR):
        for f in os.listdir(ISO_DIR):
            if f.lower().endswith(".iso"):
                isos.append(f)
    return isos


def create_vm(name, memory_mb, vcpus, disk_gb, iso, network="default"):
    """Neue VM deterministisch via qemu-img + virsh define erstellen.

    memory_mb: RAM in MB
    disk_gb:   Disk-Größe in GB
    iso:       ISO-Dateiname aus ISO_DIR (oder leer für PXE/Netzwerk)
    """
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    try:
        memory_mb = int(memory_mb)
        vcpus = int(vcpus)
        disk_gb = int(disk_gb)
        if not (256 <= memory_mb <= 1048576 and 1 <= vcpus <= 256 and 1 <= disk_gb <= 65536):
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltige VM-Ressourcen"}
    if not _valid_vm(network or "default"):
        return {"ok": False, "stderr": "Ungueltiges Netzwerk"}

    network = network or "default"
    iso_path = ""
    if iso:
        if os.path.basename(iso) != iso or not iso.lower().endswith(".iso"):
            return {"ok": False, "stderr": "Ungueltiges ISO"}
        iso_path = os.path.join(ISO_DIR, iso)
        if not os.path.isfile(iso_path):
            return {"ok": False, "stderr": f"ISO nicht gefunden: {iso}"}

    if _domain_exists(name):
        return {"ok": False, "stderr": f"VM existiert bereits: {name}"}

    try:
        os.makedirs(ISO_DIR, exist_ok=True)
    except OSError as e:
        return {"ok": False, "stderr": f"VM-Image-Verzeichnis nicht nutzbar: {e}"}
    disk_path = os.path.join(ISO_DIR, f"{name}.qcow2")
    if os.path.exists(disk_path):
        return {"ok": False, "stderr": f"Disk existiert bereits: {disk_path}"}

    net = _ensure_network(network)
    if not net["ok"]:
        return net
    network = net.get("network", network)
    network_warning = net.get("warning", "")

    disk = _run(["qemu-img", "create", "-f", "qcow2", disk_path, f"{disk_gb}G"], timeout=300)
    if not disk["ok"]:
        return {"ok": False, "stderr": disk["stderr"] or disk["stdout"] or "qemu-img fehlgeschlagen"}

    xml = _vm_domain_xml(name, memory_mb, vcpus, disk_path, iso_path, network)
    xml_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as f:
            f.write(xml)
            xml_path = f.name
        defined = _virsh(["define", xml_path], timeout=30)
        if not defined["ok"]:
            _remove_created_disk(disk_path)
            return {"ok": False, "stderr": defined["stderr"] or defined["stdout"] or "virsh define fehlgeschlagen"}
    finally:
        if xml_path:
            try:
                os.unlink(xml_path)
            except OSError:
                pass

    started = _virsh(["start", name], timeout=60)
    visible = _wait_for_domain(name)
    output = "\n".join(x for x in [
        disk["stdout"], disk["stderr"], started["stdout"], started["stderr"]
    ] if x)
    return {
        "ok": True,
        "output": output,
        "visible": visible,
        "started": started["ok"],
        "stderr": "" if started["ok"] else (started["stderr"] or started["stdout"]),
        "network": network or "",
        "warning": network_warning,
    }


def _run(args, timeout=120):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"{args[0]} nicht installiert"}
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + f"\n{args[0]} timed out",
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def _domain_exists(name):
    if HAS_LIBVIRT:
        try:
            _connect().lookupByName(name)
            return True
        except Exception:
            pass
    return _virsh(["dominfo", name], timeout=10)["ok"]


def _ensure_network(network):
    info = _virsh(["net-info", network], timeout=15)
    if info["ok"]:
        data = _parse_dominfo(info["stdout"])
        if data.get("Active", "").lower() == "yes":
            return {"ok": True, "network": network}
        started = _virsh(["net-start", network], timeout=30)
        if started["ok"]:
            return {"ok": True, "network": network}
        if network == "default" and _dnsmasq_bind_conflict(started):
            return _networkless_vm_fallback()
        if network != "default":
            return {"ok": False, "stderr": started["stderr"] or started["stdout"] or f"Netzwerk konnte nicht gestartet werden: {network}"}
    elif network != "default":
        return {"ok": False, "stderr": f"Libvirt-Netzwerk nicht gefunden: {network}"}

    fallback = _ensure_runvard_network()
    if fallback["ok"]:
        fallback["warning"] = "Libvirt default network unavailable; using runvard fallback network"
        return fallback
    detail = ""
    if info["ok"]:
        detail = started["stderr"] or started["stdout"]
    else:
        detail = info["stderr"] or info["stdout"]
    return {"ok": False, "stderr": detail or "Libvirt default network unavailable and fallback network could not be created"}


def _ensure_runvard_network():
    for subnet in range(123, 240):
        name = f"runvard{subnet}"
        existing = _virsh(["net-info", name], timeout=10)
        if existing["ok"]:
            data = _parse_dominfo(existing["stdout"])
            if data.get("Active", "").lower() == "yes":
                return {"ok": True, "network": name}
            started = _virsh(["net-start", name], timeout=30)
            if started["ok"]:
                _virsh(["net-autostart", name], timeout=10)
                return {"ok": True, "network": name}
            if _dnsmasq_bind_conflict(started):
                return _networkless_vm_fallback()
            continue

        xml = _network_xml(
            name,
            f"rvbr{subnet}",
            f"192.168.{subnet}.1",
            f"192.168.{subnet}.2",
            f"192.168.{subnet}.254",
        )
        xml_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".xml", delete=False) as f:
                f.write(xml)
                xml_path = f.name
            defined = _virsh(["net-define", xml_path], timeout=20)
            if not defined["ok"]:
                continue
            started = _virsh(["net-start", name], timeout=30)
            if started["ok"]:
                _virsh(["net-autostart", name], timeout=10)
                return {"ok": True, "network": name}
            if _dnsmasq_bind_conflict(started):
                _virsh(["net-undefine", name], timeout=20)
                return _networkless_vm_fallback()
            _virsh(["net-undefine", name], timeout=20)
        finally:
            if xml_path:
                try:
                    os.unlink(xml_path)
                except OSError:
                    pass
    return {"ok": False, "stderr": "Kein freies Runvard-VM-Netzwerk gefunden"}


def _dnsmasq_bind_conflict(result):
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    return (
        "failed to create listening socket" in text
        or "address already in use" in text
        or "adresse wird bereits verwendet" in text
        or "adresse bereits in benutzung" in text
    )


def _networkless_vm_fallback():
    return {
        "ok": True,
        "network": "",
        "warning": (
            "Libvirt DNS/DHCP network unavailable; creating VM without network. "
            "Free port 53 on the host to enable VM networking."
        ),
    }


def _network_xml(name, bridge, address, dhcp_start, dhcp_end):
    network = ET.Element("network")
    ET.SubElement(network, "name").text = name
    ET.SubElement(network, "bridge", {"name": bridge, "stp": "on", "delay": "0"})
    ET.SubElement(network, "forward", {"mode": "nat"})
    ip = ET.SubElement(network, "ip", {"address": address, "netmask": "255.255.255.0"})
    dhcp = ET.SubElement(ip, "dhcp")
    ET.SubElement(dhcp, "range", {"start": dhcp_start, "end": dhcp_end})
    return ET.tostring(network, encoding="unicode")


def _remove_created_disk(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _vm_domain_xml(name, memory_mb, vcpus, disk_path, iso_path, network):
    domain = ET.Element("domain", {"type": "kvm"})
    ET.SubElement(domain, "name").text = name
    ET.SubElement(domain, "memory", {"unit": "MiB"}).text = str(memory_mb)
    ET.SubElement(domain, "currentMemory", {"unit": "MiB"}).text = str(memory_mb)
    ET.SubElement(domain, "vcpu", {"placement": "static"}).text = str(vcpus)

    os_node = ET.SubElement(domain, "os")
    ET.SubElement(os_node, "type", {"arch": "x86_64"}).text = "hvm"
    if iso_path:
        ET.SubElement(os_node, "boot", {"dev": "cdrom"})
    elif network:
        ET.SubElement(os_node, "boot", {"dev": "network"})
    ET.SubElement(os_node, "boot", {"dev": "hd"})

    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    ET.SubElement(domain, "clock", {"offset": "utc"})
    ET.SubElement(domain, "on_poweroff").text = "destroy"
    ET.SubElement(domain, "on_reboot").text = "restart"
    ET.SubElement(domain, "on_crash").text = "destroy"

    devices = ET.SubElement(domain, "devices")
    disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
    ET.SubElement(disk, "driver", {"name": "qemu", "type": "qcow2"})
    ET.SubElement(disk, "source", {"file": disk_path})
    ET.SubElement(disk, "target", {"dev": "vda", "bus": "virtio"})

    if iso_path:
        cdrom = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
        ET.SubElement(cdrom, "driver", {"name": "qemu", "type": "raw"})
        ET.SubElement(cdrom, "source", {"file": iso_path})
        ET.SubElement(cdrom, "target", {"dev": "sda", "bus": "sata"})
        ET.SubElement(cdrom, "readonly")

    if network:
        iface = ET.SubElement(devices, "interface", {"type": "network"})
        ET.SubElement(iface, "source", {"network": network})
        ET.SubElement(iface, "model", {"type": "virtio"})
    ET.SubElement(devices, "input", {"type": "tablet", "bus": "usb"})
    ET.SubElement(devices, "graphics", {"type": "vnc", "port": "-1", "autoport": "yes", "listen": "0.0.0.0"})
    video = ET.SubElement(devices, "video")
    ET.SubElement(video, "model", {"type": "qxl"})
    ET.SubElement(devices, "console", {"type": "pty"})
    return ET.tostring(domain, encoding="unicode")


def _wait_for_domain(name, timeout=10):
    """Wait until libvirt exposes a newly created domain to list/detail calls."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if HAS_LIBVIRT:
                conn = _connect()
                conn.lookupByName(name)
                return True
        except Exception:
            pass
        if _virsh(["dominfo", name], timeout=5)["ok"]:
            return True
        time.sleep(0.25)
    return False


def list_snapshots(name):
    conn = _connect()
    dom = conn.lookupByName(name)
    return [{"name": s.getName()} for s in dom.listAllSnapshots()]


def create_snapshot(name, snap_name):
    conn = _connect()
    dom = conn.lookupByName(name)
    xml = f"<domainsnapshot><name>{snap_name}</name></domainsnapshot>"
    dom.snapshotCreateXML(xml, 0)
    return {"ok": True}


def snapshot_action(name, snap_name, action):
    conn = _connect()
    dom = conn.lookupByName(name)
    snap = dom.snapshotLookupByName(snap_name)
    if action == "revert":
        dom.revertToSnapshot(snap)
    elif action == "delete":
        snap.delete()
    else:
        raise ValueError("Unbekannte Aktion")
    return {"ok": True}


def get_vnc_port(name):
    conn = _connect()
    dom = conn.lookupByName(name)
    root = ET.fromstring(dom.XMLDesc())
    graphics = root.find(".//graphics[@type='vnc']")
    if graphics is not None:
        return {"port": graphics.get("port"), "listen": graphics.get("listen")}
    return {"port": None}


def _virsh(args, timeout=120):
    try:
        r = subprocess.run(["virsh", "-c", LIBVIRT_URI] + args, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def list_networks():
    r = _virsh(["net-list", "--all"])
    nets = []
    for line in r["stdout"].splitlines()[2:]:
        p = line.split()
        if len(p) >= 3:
            nets.append({"name": p[0], "state": p[1], "autostart": p[2]})
    return {"networks": nets}


def list_pools():
    r = _virsh(["pool-list", "--all"])
    pools = []
    for line in r["stdout"].splitlines()[2:]:
        p = line.split()
        if len(p) >= 2:
            pools.append({"name": p[0], "state": p[1]})
    return {"pools": pools}


def clone_vm(name, newname):
    try:
        r = subprocess.run(
            ["virt-clone", "--original", name, "--name", newname, "--auto-clone"],
            capture_output=True, text=True, timeout=600)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
    except FileNotFoundError:
        return {"ok": False, "output": "virt-clone nicht installiert (Paket virtinst)"}
    except Exception as e:
        return {"ok": False, "output": str(e)}


def change_cdrom(name, iso):
    conn = _connect()
    dom = conn.lookupByName(name)
    root = ET.fromstring(dom.XMLDesc())
    target = root.find(".//disk[@device='cdrom']/target")
    if target is None:
        return {"ok": False, "stderr": "Kein CD-ROM-Laufwerk vorhanden"}
    dev = target.get("dev")
    args = ["change-media", name, dev]
    if iso:
        args += [os.path.join(ISO_DIR, iso), "--update"]
    else:
        args += ["--eject", "--force"]
    return _virsh(args)


# --- Phase 5: Hot-Edit (Disks/NICs) + Storage-Pools ---

import re as _re

_VM_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_VOL_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TARGET_RE = _re.compile(r"^[a-z]{2,4}[a-z0-9]{0,4}$")
_MAC_RE = _re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_IFACE_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,31}$")


def _valid_vm(name):
    return bool(_VM_NAME_RE.fullmatch(name or ""))


def list_hardware(name):
    """Disks und Netzwerkkarten einer Domain aus der Domain-XML auslesen."""
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name", "disks": [], "nics": []}
    try:
        conn = _connect()
        dom = conn.lookupByName(name)
        active = dom.isActive() == 1
        info = dom.info()
        root = ET.fromstring(dom.XMLDesc())
    except Exception as e:
        return {"ok": False, "stderr": str(e), "disks": [], "nics": []}
    disks = []
    for d in root.findall(".//disk"):
        dev = d.get("device")  # disk | cdrom
        src = d.find("source")
        tgt = d.find("target")
        drv = d.find("driver")
        disks.append({
            "device": dev,
            "target": tgt.get("dev") if tgt is not None else "",
            "bus": tgt.get("bus") if tgt is not None else "",
            "source": (src.get("file") or src.get("dev") or "") if src is not None else "",
            "format": drv.get("type") if drv is not None else "",
        })
    nics = []
    for n in root.findall(".//interface"):
        itype = n.get("type")  # network | bridge | direct ...
        mac = n.find("mac")
        src = n.find("source")
        model = n.find("model")
        srcval = ""
        if src is not None:
            srcval = src.get("network") or src.get("bridge") or src.get("dev") or ""
        nics.append({
            "type": itype,
            "mac": mac.get("address") if mac is not None else "",
            "source": srcval,
            "model": model.get("type") if model is not None else "",
        })
    config = _domain_config_resources(name, dom)
    memory_mb = int((config.get("max_mem") or (info[1] * 1024)) / 1024 / 1024)
    vcpus = int(config.get("vcpus") or info[3])
    return {
        "ok": True,
        "active": active,
        "memory_mb": memory_mb,
        "current_memory_mb": int(info[2] / 1024),
        "vcpus": vcpus,
        "disks": disks,
        "nics": nics,
    }


def _domain_active(name):
    try:
        conn = _connect()
        return conn.lookupByName(name).isActive() == 1
    except Exception:
        state = _virsh(["domstate", name], timeout=10)
        return state["ok"] and "running" in state["stdout"].lower()


def _define_resources(name, memory_mb, vcpus):
    dumped = _virsh(["dumpxml", "--inactive", name], timeout=30)
    if not dumped["ok"]:
        return dumped
    try:
        root = ET.fromstring(dumped["stdout"])
        memory = root.find("./memory")
        if memory is None:
            memory = ET.SubElement(root, "memory", {"unit": "MiB"})
        memory.set("unit", "MiB")
        memory.text = str(memory_mb)
        current = root.find("./currentMemory")
        if current is None:
            current = ET.SubElement(root, "currentMemory", {"unit": "MiB"})
        current.set("unit", "MiB")
        current.text = str(memory_mb)
        vcpu_node = root.find("./vcpu")
        if vcpu_node is None:
            vcpu_node = ET.SubElement(root, "vcpu", {"placement": "static"})
        vcpu_node.text = str(vcpus)
        xml = ET.tostring(root, encoding="unicode")
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".xml") as f:
        f.write(xml)
        xml_path = f.name
    try:
        return _virsh(["define", xml_path], timeout=30)
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            pass


def update_resources(name, memory_mb, vcpus):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    try:
        memory_mb = int(memory_mb)
        vcpus = int(vcpus)
        if not (256 <= memory_mb <= 1048576 and 1 <= vcpus <= 256):
            raise ValueError()
    except Exception:
        return {"ok": False, "stderr": "Ungueltige VM-Ressourcen"}

    defined = _define_resources(name, memory_mb, vcpus)
    if not defined["ok"]:
        return {"ok": False, "stderr": defined["stderr"] or defined["stdout"] or "VM-Definition konnte nicht gespeichert werden"}

    warnings = []
    if _domain_active(name):
        for args in (["setmem", name, f"{memory_mb}M", "--live"],
                     ["setvcpus", name, str(vcpus), "--live"]):
            result = _virsh(args, timeout=30)
            if not result["ok"]:
                warnings.append(result["stderr"] or result["stdout"] or "Live-Aenderung nicht moeglich")

    return {
        "ok": True,
        "memory_mb": memory_mb,
        "vcpus": vcpus,
        "warning": " ".join(warnings),
    }


def _disk_source_for_target(name, target):
    conn = _connect()
    dom = conn.lookupByName(name)
    root = ET.fromstring(dom.XMLDesc())
    for disk in root.findall(".//disk[@device='disk']"):
        tgt = disk.find("target")
        src = disk.find("source")
        if tgt is not None and tgt.get("dev") == target and src is not None:
            return src.get("file") or ""
    return ""


def resize_disk(name, target, size_gb):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    if not _TARGET_RE.fullmatch(target or ""):
        return {"ok": False, "stderr": "Ungueltiges Target"}
    try:
        size_gb = int(size_gb)
        if not (1 <= size_gb <= 65536):
            raise ValueError()
    except Exception:
        return {"ok": False, "stderr": "Ungueltige Groesse"}
    try:
        source = _disk_source_for_target(name, target)
    except Exception as e:
        return {"ok": False, "stderr": str(e)}
    if not source:
        return {"ok": False, "stderr": "Disk-Image nicht gefunden oder nicht dateibasiert"}

    info = _run(["qemu-img", "info", "--output=json", source], timeout=60)
    if info["ok"]:
        try:
            current = int(json.loads(info["stdout"]).get("virtual-size") or 0)
            requested = size_gb * 1024 * 1024 * 1024
            if current and requested <= current:
                return {"ok": False, "stderr": "Neue Groesse muss groesser als die aktuelle Disk sein"}
        except Exception:
            pass
    resized = _run(["qemu-img", "resize", source, f"{size_gb}G"], timeout=300)
    if not resized["ok"]:
        return {"ok": False, "stderr": resized["stderr"] or resized["stdout"] or "qemu-img resize fehlgeschlagen"}
    return {
        "ok": True,
        "target": target,
        "source": source,
        "size_gb": size_gb,
        "warning": "Guest partition/filesystem may need to be extended inside the VM.",
    }


def _scope_flags(name):
    """--config (+ --live falls aktiv), damit Änderungen persistent sind."""
    try:
        conn = _connect()
        active = conn.lookupByName(name).isActive() == 1
    except Exception:
        active = False
    return ["--config", "--live"] if active else ["--config"]


def attach_disk(name, source, target, bus="virtio"):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    if not _TARGET_RE.fullmatch(target or ""):
        return {"ok": False, "stderr": "Ungueltiges Target (z. B. vdb)"}
    if not source or "\n" in source or source.startswith("-"):
        return {"ok": False, "stderr": "Ungueltige Quelle"}
    if bus not in ("virtio", "sata", "scsi", "ide"):
        return {"ok": False, "stderr": "Ungueltiger Bus"}
    args = ["attach-disk", name, source, target, "--targetbus", bus,
            "--driver", "qemu", "--subdriver", "qcow2"] + _scope_flags(name)
    return _virsh(args)


def detach_disk(name, target):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    if not _TARGET_RE.fullmatch(target or ""):
        return {"ok": False, "stderr": "Ungueltiges Target"}
    return _virsh(["detach-disk", name, target] + _scope_flags(name))


def attach_nic(name, network, model="virtio", source_type="network"):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    if source_type not in ("network", "bridge"):
        return {"ok": False, "stderr": "Ungueltiger Interface-Typ"}
    if source_type == "network":
        if not _VM_NAME_RE.fullmatch(network or ""):
            return {"ok": False, "stderr": "Ungueltiges Netzwerk"}
        ensured = _ensure_network(network)
        if not ensured.get("ok"):
            return ensured
        network = ensured.get("network") or ""
        if not network:
            return {
                "ok": False,
                "stderr": ensured.get("warning") or "Libvirt-Netzwerk nicht verfuegbar",
            }
    elif not _IFACE_RE.fullmatch(network or ""):
        return {"ok": False, "stderr": "Ungueltige Bridge"}
    if model not in ("virtio", "e1000", "rtl8139"):
        return {"ok": False, "stderr": "Ungueltiges Modell"}
    args = ["attach-interface", name, source_type, network,
            "--model", model] + _scope_flags(name)
    return _virsh(args)


def detach_nic(name, itype, mac):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    if itype not in ("network", "bridge", "direct"):
        return {"ok": False, "stderr": "Ungueltiger Interface-Typ"}
    if not _MAC_RE.fullmatch(mac or ""):
        return {"ok": False, "stderr": "Ungueltige MAC-Adresse"}
    return _virsh(["detach-interface", name, itype, "--mac", mac] + _scope_flags(name))


# --- Storage-Pools ---

def pool_details():
    """Pools mit Kapazität/Belegung und Autostart-Status."""
    base = list_pools()["pools"]
    pools = []
    for p in base:
        name = p["name"]
        info = _virsh(["pool-info", name])
        cap = alloc = avail = 0
        for line in info["stdout"].splitlines():
            low = line.lower()
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            val = parts[1].strip()
            num = _bytes_from_virsh(val)
            if low.startswith("capacity"):
                cap = num
            elif low.startswith("allocation"):
                alloc = num
            elif low.startswith("available"):
                avail = num
        auto = _virsh(["pool-info", name]).get("stdout", "")
        autostart = "yes" in [l.split(":")[1].strip().lower()
                              for l in auto.splitlines()
                              if l.lower().startswith("autostart")] if auto else False
        pools.append({
            "name": name, "state": p.get("state", ""),
            "capacity": cap, "allocation": alloc, "available": avail,
            "autostart": autostart,
        })
    return {"pools": pools}


def _bytes_from_virsh(val):
    """'10.00 GiB' -> Bytes (int)."""
    m = _re.match(r"([\d.]+)\s*([KMGTP]?i?B)?", val)
    if not m:
        return 0
    num = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    factor = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3,
              "TIB": 1024**4, "PIB": 1024**5, "KB": 1000, "MB": 1000**2,
              "GB": 1000**3, "TB": 1000**4}.get(unit, 1)
    return int(num * factor)


def pool_create(name, ptype, target):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger Pool-Name"}
    if ptype not in ("dir", "fs"):
        return {"ok": False, "stderr": "Nur dir/fs werden unterstützt"}
    if not target or not target.startswith("/") or "\n" in target:
        return {"ok": False, "stderr": "Ungueltiges Zielverzeichnis"}
    r = _virsh(["pool-define-as", name, ptype, "--target", target])
    if not r["ok"]:
        return r
    _virsh(["pool-build", name])
    start = _virsh(["pool-start", name])
    _virsh(["pool-autostart", name])
    return start if not start["ok"] else {"ok": True}


def pool_action(name, action):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger Pool-Name"}
    if action == "start":
        return _virsh(["pool-start", name])
    if action == "stop":
        return _virsh(["pool-destroy", name])
    if action == "autostart-on":
        return _virsh(["pool-autostart", name])
    if action == "autostart-off":
        return _virsh(["pool-autostart", name, "--disable"])
    if action == "delete":
        _virsh(["pool-destroy", name])
        return _virsh(["pool-undefine", name])
    return {"ok": False, "stderr": "Unbekannte Aktion"}


def pool_volumes(pool):
    if not _valid_vm(pool):
        return {"volumes": []}
    r = _virsh(["vol-list", pool, "--details"])
    vols = []
    lines = r["stdout"].splitlines()
    for line in lines[2:]:
        p = line.split()
        if len(p) >= 2 and not p[0].startswith("-"):
            vols.append({"name": p[0], "path": p[1] if p[1].startswith("/") else ""})
    return {"volumes": vols}


def vol_create(pool, name, size_gb, fmt="qcow2"):
    if not _valid_vm(pool) or not _VOL_NAME_RE.fullmatch(name or ""):
        return {"ok": False, "stderr": "Ungueltiger Pool-/Volume-Name"}
    if fmt not in ("qcow2", "raw"):
        return {"ok": False, "stderr": "Ungueltiges Format"}
    try:
        size = int(size_gb)
        if not (1 <= size <= 65536):
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltige Größe"}
    vol = name if name.endswith((".qcow2", ".raw", ".img")) else f"{name}.{fmt}"
    return _virsh(["vol-create-as", pool, vol, f"{size}G", "--format", fmt])


def vol_delete(pool, vol):
    if not _valid_vm(pool) or not _VOL_NAME_RE.fullmatch(vol or ""):
        return {"ok": False, "stderr": "Ungueltiger Pool-/Volume-Name"}
    return _virsh(["vol-delete", vol, "--pool", pool])
