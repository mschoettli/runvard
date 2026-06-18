"""VMs: KVM/QEMU über libvirt verwalten - inkl. erstellen/löschen."""
import os
import subprocess

from modules import validators

try:
    import libvirt
    HAS_LIBVIRT = True
except ImportError:
    HAS_LIBVIRT = False

import xml.etree.ElementTree as ET

_conn = None
ISO_DIR = "/var/lib/libvirt/images"
VM_MEMORY_MIN_MB = 256
VM_MEMORY_MAX_MB = 1024 * 1024
VM_VCPU_MIN = 1
VM_VCPU_MAX = 256
VM_DISK_MIN_GB = 1
VM_DISK_MAX_GB = 65536


def _connect():
    global _conn
    if not HAS_LIBVIRT:
        raise RuntimeError("libvirt-python nicht installiert")
    if _conn is None or not _conn.isAlive():
        _conn = libvirt.open("qemu:///system")
    return _conn


def available():
    if not HAS_LIBVIRT:
        return False
    try:
        _connect()
        return True
    except Exception:
        return False


_STATES = {
    0: "no state", 1: "running", 2: "blocked", 3: "paused",
    4: "shutting down", 5: "shut off", 6: "crashed", 7: "suspended",
}


def list_vms():
    if not available():
        return {"ok": False, "vms": [], "stderr": "libvirt unavailable"}
    try:
        conn = _connect()
    except Exception as exc:
        return {"ok": False, "vms": [], "stderr": str(exc)}
    vms = []
    try:
        for dom in conn.listAllDomains():
            state, _ = dom.state()
            info = dom.info()
            vms.append({
                "name": dom.name(),
                "uuid": dom.UUIDString(),
                "state": _STATES.get(state, "unknown"),
                "active": dom.isActive() == 1,
                "autostart": dom.autostart() == 1,
                "max_mem": info[1] * 1024,
                "mem": info[2] * 1024,
                "vcpus": info[3],
            })
    except Exception as exc:
        return {"ok": False, "vms": [], "stderr": str(exc)}
    return {"ok": True, "vms": vms}


def vm_action(name, action):
    _require_vm_name(name)
    try:
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
    except ValueError:
        raise
    except Exception as exc:
        return {"ok": False, "stderr": str(exc)}


def list_isos():
    """Verfügbare ISO-Dateien im Image-Verzeichnis."""
    isos = []
    if os.path.isdir(ISO_DIR):
        for f in os.listdir(ISO_DIR):
            if f.lower().endswith(".iso"):
                isos.append(f)
    return isos


def create_vm(name, memory_mb, vcpus, disk_gb, iso, network="default"):
    """Neue VM via virt-install erstellen.

    memory_mb: RAM in MB
    disk_gb:   Disk-Größe in GB
    iso:       ISO-Dateiname aus ISO_DIR (oder leer für PXE/Netzwerk)
    """
    _require_vm_name(name)
    _require_vm_name(network, "network")
    memory_mb = _bounded_int(memory_mb, VM_MEMORY_MIN_MB, VM_MEMORY_MAX_MB, "RAM")
    vcpus = _bounded_int(vcpus, VM_VCPU_MIN, VM_VCPU_MAX, "vCPUs")
    disk_gb = _bounded_int(disk_gb, VM_DISK_MIN_GB, VM_DISK_MAX_GB, "Disk size")
    iso_path = _iso_path(iso) if iso else ""
    disk_path = os.path.join(ISO_DIR, f"{name}.qcow2")
    cmd = [
        "virt-install",
        "--name", name,
        "--memory", str(memory_mb),
        "--vcpus", str(vcpus),
        "--disk", f"path={disk_path},size={disk_gb},format=qcow2",
        "--network", f"network={network}",
        "--graphics", "vnc,listen=0.0.0.0",
        "--noautoconsole",
        "--osinfo", "detect=on,require=off",
    ]
    if iso:
        cmd += ["--cdrom", iso_path]
    else:
        cmd += ["--pxe"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
    except FileNotFoundError:
        return {"ok": False, "output": "virt-install nicht installiert (Paket virtinst)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "virt-install timed out"}
    except Exception as exc:
        return {"ok": False, "output": str(exc)}


def list_snapshots(name):
    _require_vm_name(name)
    conn = _connect()
    dom = conn.lookupByName(name)
    return [{"name": s.getName()} for s in dom.listAllSnapshots()]


def create_snapshot(name, snap_name):
    _require_vm_name(name)
    _require_vm_name(snap_name, "snapshot")
    try:
        conn = _connect()
        dom = conn.lookupByName(name)
        root = ET.Element("domainsnapshot")
        ET.SubElement(root, "name").text = snap_name
        xml = ET.tostring(root, encoding="unicode")
        dom.snapshotCreateXML(xml, 0)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "stderr": str(exc)}


def snapshot_action(name, snap_name, action):
    _require_vm_name(name)
    _require_vm_name(snap_name, "snapshot")
    if action not in ("revert", "delete"):
        raise ValueError("Unbekannte Aktion")
    try:
        conn = _connect()
        dom = conn.lookupByName(name)
        snap = dom.snapshotLookupByName(snap_name)
        if action == "revert":
            dom.revertToSnapshot(snap)
        else:
            snap.delete()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "stderr": str(exc)}


def get_vnc_port(name):
    _require_vm_name(name)
    conn = _connect()
    dom = conn.lookupByName(name)
    root = ET.fromstring(dom.XMLDesc())
    graphics = root.find(".//graphics[@type='vnc']")
    if graphics is not None:
        return {"port": graphics.get("port"), "listen": graphics.get("listen")}
    return {"port": None}


def _virsh(args, timeout=120):
    try:
        r = subprocess.run(["virsh"] + args, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def list_networks():
    r = _virsh(["net-list", "--all"])
    if not r["ok"]:
        return {"ok": False, "networks": [], "error": r.get("stderr", "")}
    nets = []
    for line in r["stdout"].splitlines()[2:]:
        p = line.split()
        if len(p) >= 3:
            nets.append({"name": p[0], "state": p[1], "autostart": p[2]})
    return {"ok": True, "networks": nets}


def list_pools():
    r = _virsh(["pool-list", "--all"])
    if not r["ok"]:
        return {"ok": False, "pools": [], "error": r.get("stderr", "")}
    pools = []
    for line in r["stdout"].splitlines()[2:]:
        p = line.split()
        if len(p) >= 2:
            pools.append({"name": p[0], "state": p[1]})
    return {"ok": True, "pools": pools}


def clone_vm(name, newname):
    _require_vm_name(name)
    _require_vm_name(newname, "new VM name")
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
    _require_vm_name(name)
    iso_path = _iso_path(iso) if iso else ""
    try:
        conn = _connect()
        dom = conn.lookupByName(name)
        root = ET.fromstring(dom.XMLDesc())
        target = root.find(".//disk[@device='cdrom']/target")
        if target is None:
            return {"ok": False, "stderr": "Kein CD-ROM-Laufwerk vorhanden"}
        dev = target.get("dev")
        args = ["change-media", name, dev]
        if iso:
            args += [iso_path, "--update"]
        else:
            args += ["--eject", "--force"]
        return _virsh(args)
    except Exception as exc:
        return {"ok": False, "stderr": str(exc)}


# --- Phase 5: Hot-Edit (Disks/NICs) + Storage-Pools ---

import re as _re

_VOL_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TARGET_RE = _re.compile(r"^[a-z]{2,4}[a-z0-9]{0,4}$")
_MAC_RE = _re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _valid_vm(name):
    try:
        validators.require_vm_name(name)
        return True
    except ValueError:
        return False


def _require_vm_name(name, label="VM name"):
    return validators.require_vm_name(name, label)


def _bounded_int(value, minimum, maximum, label):
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Ungueltige {label}") from None
    if not (minimum <= num <= maximum):
        raise ValueError(f"Ungueltige {label}")
    return num


def _iso_path(iso):
    if not iso or "/" in iso or "\\" in iso or iso in {".", ".."}:
        raise ValueError("Ungueltige ISO-Datei")
    if not iso.lower().endswith(".iso"):
        raise ValueError("Ungueltige ISO-Datei")
    path = os.path.realpath(os.path.join(ISO_DIR, iso))
    root = os.path.realpath(ISO_DIR)
    if not path.startswith(root + os.sep):
        raise ValueError("Ungueltige ISO-Datei")
    return path


def _disk_source_path(source):
    if not source or "\n" in source or "\r" in source or source.startswith("-"):
        raise ValueError("Ungueltige Quelle")
    if not source.startswith("/"):
        raise ValueError("Ungueltige Quelle")
    path = validators.guard_read_path(source)
    if validators.is_readonly_path(path):
        raise PermissionError("Disk source is protected")
    return path


def list_hardware(name):
    """Disks und Netzwerkkarten einer Domain aus der Domain-XML auslesen."""
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name", "disks": [], "nics": []}
    try:
        conn = _connect()
        dom = conn.lookupByName(name)
        active = dom.isActive() == 1
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
    return {"ok": True, "active": active, "disks": disks, "nics": nics}


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
    try:
        source = _disk_source_path(source)
    except (PermissionError, ValueError) as exc:
        return {"ok": False, "stderr": str(exc)}
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


def attach_nic(name, network, model="virtio"):
    if not _valid_vm(name):
        return {"ok": False, "stderr": "Ungueltiger VM-Name"}
    if not _valid_vm(network):
        return {"ok": False, "stderr": "Ungueltiges Netzwerk"}
    if model not in ("virtio", "e1000", "rtl8139"):
        return {"ok": False, "stderr": "Ungueltiges Modell"}
    args = ["attach-interface", name, "network", network,
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
    try:
        target = validators.guard_mountpoint(target)
    except (PermissionError, ValueError) as exc:
        return {"ok": False, "stderr": str(exc)}
    r = _virsh(["pool-define-as", name, ptype, "--target", target])
    if not r["ok"]:
        return r
    build = _virsh(["pool-build", name])
    if not build["ok"]:
        return build
    start = _virsh(["pool-start", name])
    if not start["ok"]:
        return start
    autostart = _virsh(["pool-autostart", name])
    if not autostart["ok"]:
        return autostart
    return {"ok": True}


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
        destroyed = _virsh(["pool-destroy", name])
        inactive = any(
            marker in (destroyed.get("stderr", "") or "").lower()
            for marker in ("not active", "not running", "inactive")
        )
        if not destroyed["ok"] and not inactive:
            return destroyed
        return _virsh(["pool-undefine", name])
    return {"ok": False, "stderr": "Unbekannte Aktion"}


def pool_volumes(pool):
    if not _valid_vm(pool):
        return {"ok": False, "volumes": [], "error": "Ungueltiger Pool-Name"}
    r = _virsh(["vol-list", pool, "--details"])
    if not r["ok"]:
        return {"ok": False, "volumes": [], "error": r.get("stderr", "")}
    vols = []
    lines = r["stdout"].splitlines()
    for line in lines[2:]:
        p = line.split()
        if len(p) >= 2 and not p[0].startswith("-"):
            vols.append({"name": p[0], "path": p[1] if p[1].startswith("/") else ""})
    return {"ok": True, "volumes": vols}


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
