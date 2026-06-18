"""Speicher: Block-Devices, Partitionierung, Formatierung, Mount, RAID, SMART,
LVM, LUKS, ZFS/Btrfs-Pools, iSCSI-Initiator und Dateisystem-Resize."""
import json
import os
import re
import subprocess
import time


def _run(cmd, timeout=60, input_text=None):
    """Shell-Kommando ausführen, Ergebnis als dict.

    input_text wird – falls gesetzt – über stdin an den Prozess übergeben
    (z. B. LUKS-Passphrasen, die so nicht in der Prozessliste auftauchen).
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=input_text)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Timeout"}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} nicht gefunden"}


def list_block_devices():
    """Alle Block-Devices via lsblk (JSON)."""
    r = _run(["lsblk", "-J", "-b", "-o",
              "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,ROTA,RM"])
    if not r["ok"]:
        return {"devices": [], "error": r["stderr"]}
    data = json.loads(r["stdout"])

    # Welches Device trägt das Root-Filesystem?
    root_dev = _root_device()

    def annotate(node, parent_name=None):
        node["is_root"] = node.get("mountpoint") == "/" or node["name"] == root_dev
        node["protected"] = node["is_root"]
        for child in node.get("children", []):
            annotate(child, node["name"])
            if child.get("is_root"):
                node["protected"] = True
        return node

    devices = [annotate(d) for d in data.get("blockdevices", [])]
    return {"devices": devices, "root_device": root_dev}


def _root_device():
    r = _run(["findmnt", "-n", "-o", "SOURCE", "/"])
    if r["ok"]:
        src = r["stdout"].strip()
        return _base_device_name(src)
    return ""


def _base_device_name(device: str):
    """Return the parent disk name for a block device."""
    name = os.path.basename(device.replace("/dev/", ""))
    if name.startswith("mapper/"):
        return name
    if re.fullmatch(r"(nvme\d+n\d+|mmcblk\d+|loop\d+)p\d+", name):
        return re.sub(r"p\d+$", "", name)
    if re.fullmatch(r"[A-Za-z]+\d+", name):
        return re.sub(r"\d+$", "", name)
    return name


def _partition_name(device: str, number: int = 1):
    """Return the partition name for a disk and partition number."""
    name = os.path.basename(device.replace("/dev/", ""))
    suffix = f"p{number}" if name[-1:].isdigit() else str(number)
    return f"{name}{suffix}"


def _settle_device(device: str, wait_for: str | None = None, timeout: int = 10):
    """Ask the kernel to refresh partition metadata and wait for a device."""
    _run(["partprobe", device])
    _run(["udevadm", "settle"])
    target = wait_for or device
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(target):
            return True
        time.sleep(0.2)
    return os.path.exists(target)


def _first_partition_name(device: str):
    """Return the first child partition name reported by lsblk."""
    dev = f"/dev/{device}" if not device.startswith("/dev/") else device
    r = _run(["lsblk", "-J", "-o", "NAME,TYPE", dev])
    if not r["ok"]:
        return ""
    try:
        data = json.loads(r["stdout"])
    except json.JSONDecodeError:
        return ""
    blockdevices = data.get("blockdevices", [])
    if not blockdevices:
        return ""
    for child in blockdevices[0].get("children", []):
        if child.get("type") == "part" and child.get("name"):
            return child["name"]
    return ""


def smart_data(device: str):
    """SMART-Werte einer Disk via smartctl."""
    dev = f"/dev/{device}" if not device.startswith("/dev/") else device
    r = _run(["smartctl", "-a", "-j", dev])
    if not r["ok"] and not r["stdout"]:
        return {"available": False, "error": r["stderr"]}
    try:
        data = json.loads(r["stdout"])
    except json.JSONDecodeError:
        return {"available": False, "error": "Parse-Fehler"}

    health = data.get("smart_status", {}).get("passed")
    temp = data.get("temperature", {}).get("current")
    hours = data.get("power_on_time", {}).get("hours")
    return {
        "available": True,
        "healthy": health,
        "temperature": temp,
        "power_on_hours": hours,
        "model": data.get("model_name"),
        "serial": data.get("serial_number"),
        "attributes": data.get("ata_smart_attributes", {}).get("table", []),
    }


# --- Destruktive Operationen (nur auf nicht-Root Devices) ---

def _guard(device: str):
    """Sicherheitscheck: Root-Device niemals anfassen."""
    base = _base_device_name(device)
    if base == _root_device():
        raise PermissionError("Root-Device kann nicht verändert werden")


def create_partition_table(device: str, label: str = "gpt"):
    _guard(device)
    dev = f"/dev/{device}" if not device.startswith("/dev/") else device
    _run(["wipefs", "-a", dev])
    res = _run(["parted", "-s", dev, "mklabel", label])
    if res["ok"]:
        _settle_device(dev)
    return res


def create_partition(device: str, start="1MiB", end="100%"):
    _guard(device)
    dev = f"/dev/{device}" if not device.startswith("/dev/") else device
    res = _run(["parted", "-s", "-a", "optimal", dev, "mkpart",
                "primary", start, end])
    part_name = _partition_name(dev)
    part_path = f"/dev/{part_name}"
    if res["ok"]:
        _settle_device(dev, part_path)
        detected = _first_partition_name(dev)
        if detected:
            part_name = detected
            part_path = f"/dev/{part_name}"
        if not os.path.exists(part_path):
            res["ok"] = False
            res["stderr"] = f"Partition {part_path} was not detected"
    res["partition"] = part_name
    return res


def format_partition(partition: str, fstype: str = "ext4"):
    _guard(partition)
    dev = f"/dev/{partition}" if not partition.startswith("/dev/") else partition
    if not os.path.exists(dev):
        return {"ok": False, "stderr": f"{dev} does not exist"}
    _run(["wipefs", "-a", dev])
    mkfs_map = {
        "ext4": ["mkfs.ext4", "-F", dev],
        "xfs": ["mkfs.xfs", "-f", dev],
        "btrfs": ["mkfs.btrfs", "-f", dev],
    }
    if fstype not in mkfs_map:
        return {"ok": False, "stderr": "Unbekanntes Dateisystem"}
    return _run(mkfs_map[fstype], timeout=300)


def mount_device(partition: str, mountpoint: str, persist=False):
    _guard(partition)
    dev = f"/dev/{partition}" if not partition.startswith("/dev/") else partition
    import os
    if not os.path.exists(dev):
        return {"ok": False, "stderr": f"{dev} does not exist"}
    if not mountpoint or not mountpoint.startswith("/"):
        return {"ok": False, "stderr": "Mountpoint must be an absolute path"}
    os.makedirs(mountpoint, exist_ok=True)
    res = _run(["mount", dev, mountpoint])
    if res["ok"] and persist:
        uuid = _run(["blkid", "-s", "UUID", "-o", "value", dev])["stdout"].strip()
        if uuid:
            entry = f"UUID={uuid} {mountpoint} auto defaults 0 2"
            try:
                with open("/etc/fstab") as f:
                    exists = any(
                        line.strip() and not line.lstrip().startswith("#")
                        and line.split()[0] == f"UUID={uuid}"
                        for line in f
                    )
            except OSError:
                exists = False
            if not exists:
                with open("/etc/fstab", "a") as f:
                    f.write(f"\n{entry}\n")
    return res


def unmount_device(mountpoint: str):
    return _run(["umount", mountpoint])


# --- Swap ---

def list_swap():
    """Aktive Swap-Bereiche aus /proc/swaps."""
    entries = []
    try:
        with open("/proc/swaps") as f:
            for line in f.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    entries.append({
                        "name": parts[0],
                        "type": parts[1],
                        "size_kb": int(parts[2]) if parts[2].isdigit() else 0,
                        "used_kb": int(parts[3]) if parts[3].isdigit() else 0,
                    })
    except OSError:
        pass
    return {"swaps": entries}


def create_swapfile(path: str, size_mb, persist=False):
    """Swapfile anlegen, aktivieren, optional in /etc/fstab eintragen."""
    import os
    try:
        size_mb = int(size_mb)
        if not (1 <= size_mb <= 1024 * 1024):
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltige Groesse"}
    if not path or " " in path or not path.startswith("/"):
        return {"ok": False, "stderr": "Ungueltiger Pfad"}
    if os.path.exists(path):
        return {"ok": False, "stderr": "Pfad existiert bereits"}
    r = _run(["fallocate", "-l", f"{size_mb}M", path])
    if not r["ok"]:
        r = _run(["dd", "if=/dev/zero", f"of={path}", "bs=1M",
                  f"count={size_mb}"], timeout=900)
        if not r["ok"]:
            return r
    os.chmod(path, 0o600)
    mk = _run(["mkswap", path])
    if not mk["ok"]:
        return mk
    on = _run(["swapon", path])
    if not on["ok"]:
        return on
    if persist:
        try:
            with open("/etc/fstab", "a") as f:
                f.write(f"\n{path} none swap sw 0 0\n")
        except OSError:
            pass
    return {"ok": True}


def swap_action(target: str, action: str):
    """action: on | off."""
    if action == "on":
        return _run(["swapon", target])
    if action == "off":
        return _run(["swapoff", target])
    return {"ok": False, "stderr": "Unbekannte Aktion"}


# --- RAID via mdadm ---

def list_raid():
    """Aktive RAID-Arrays aus /proc/mdstat."""
    try:
        with open("/proc/mdstat") as f:
            raw = f.read()
    except OSError:
        return {"arrays": [], "raw": ""}
    arrays = []
    for line in raw.splitlines():
        if line.startswith("md"):
            name = line.split(":")[0].strip()
            detail = _run(["mdadm", "--detail", f"/dev/{name}"])
            arrays.append({"name": name, "detail": detail["stdout"]})
    return {"arrays": arrays, "raw": raw}


def create_raid(name: str, level: int, devices: list):
    """RAID-Array erstellen. level: 0,1,5,6,10."""
    for d in devices:
        _guard(d)
    dev_paths = [f"/dev/{d}" if not d.startswith("/dev/") else d for d in devices]
    cmd = ["mdadm", "--create", f"/dev/{name}", "--level", str(level),
           "--raid-devices", str(len(dev_paths))] + dev_paths
    return _run(cmd, timeout=120)


# --- LVM (lvm2) ---

def _lvm_report(cmd, key):
    r = _run(cmd)
    if not r["ok"]:
        return []
    try:
        data = json.loads(r["stdout"])
        return data.get("report", [{}])[0].get(key, [])
    except (json.JSONDecodeError, KeyError, IndexError):
        return []


def lvm_overview():
    """PV/VG/LV-Übersicht via lvm2 JSON-Report."""
    available = _run(["vgs", "--version"])["ok"]
    if not available:
        return {"available": False, "pvs": [], "vgs": [], "lvs": []}
    return {
        "available": True,
        "pvs": _lvm_report(["pvs", "--reportformat", "json", "-o",
                            "pv_name,vg_name,pv_size,pv_free"], "pv"),
        "vgs": _lvm_report(["vgs", "--reportformat", "json", "-o",
                            "vg_name,vg_size,vg_free,pv_count,lv_count"], "vg"),
        "lvs": _lvm_report(["lvs", "--reportformat", "json", "-o",
                            "lv_name,vg_name,lv_size,lv_path"], "lv"),
    }


def vg_create(name, devices):
    """Physical Volumes anlegen und zu einer Volume Group zusammenfassen."""
    for d in devices:
        _guard(d)
    dev_paths = [f"/dev/{d}" if not d.startswith("/dev/") else d for d in devices]
    for dp in dev_paths:
        _run(["pvcreate", "-y", dp])
    return _run(["vgcreate", name] + dev_paths)


def lv_create(vg, name, size):
    """size z.B. '10G' (absolut) oder '100%FREE' (Anteil)."""
    size = str(size)
    flag = "-l" if "%" in size else "-L"
    return _run(["lvcreate", flag, size, "-n", name, vg])


def lv_extend(lv_path, size):
    """size z.B. '+5G' (zusätzlich) oder '20G' (absolut); Dateisystem wird mitgewachsen."""
    return _run(["lvextend", "-L", str(size), "--resizefs", lv_path])


def lv_remove(lv_path):
    return _run(["lvremove", "-y", lv_path])


# --- Gemeinsame Helfer für Phase-5-Storage ---

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PORTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IQN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,255}$")


def _devpath(d):
    return d if d.startswith("/dev/") else f"/dev/{d}"


def _valid_name(name):
    return bool(_NAME_RE.fullmatch(name or ""))


# --- LUKS (cryptsetup) ---

def luks_available():
    return _run(["cryptsetup", "--version"])["ok"]


def luks_list():
    """LUKS-Container ermitteln: alle Block-Devices mit FSTYPE crypto_LUKS,
    inkl. offenem Mapping (falls entsperrt) und dessen Mountpoint."""
    if not luks_available():
        return {"available": False, "containers": []}
    r = _run(["lsblk", "-J", "-b", "-o", "NAME,FSTYPE,TYPE,SIZE,MOUNTPOINT"])
    containers = []
    if r["ok"]:
        try:
            data = json.loads(r["stdout"])
        except json.JSONDecodeError:
            data = {}

        def walk(node):
            if node.get("fstype") == "crypto_LUKS":
                children = node.get("children", [])
                mapper = children[0]["name"] if children else None
                mp = children[0].get("mountpoint") if children else None
                containers.append({
                    "device": _devpath(node.get("name", "")),
                    "name": node.get("name", ""),
                    "size": node.get("size", 0),
                    "open": bool(children),
                    "mapper": mapper,
                    "mountpoint": mp,
                })
            for c in node.get("children", []):
                walk(c)

        for d in data.get("blockdevices", []):
            walk(d)
    return {"available": True, "containers": containers}


def luks_format(device, passphrase):
    """Device als LUKS2 verschlüsseln. Passphrase via stdin (Key-File '-')."""
    _guard(device)
    if not passphrase:
        return {"ok": False, "stderr": "Passphrase darf nicht leer sein"}
    dev = _devpath(device)
    return _run(["cryptsetup", "luksFormat", "-q", "--type", "luks2",
                 "--key-file=-", dev], timeout=120, input_text=passphrase)


def luks_open(device, name, passphrase):
    """LUKS-Container entsperren -> /dev/mapper/<name>."""
    _guard(device)
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Mapper-Name"}
    if not passphrase:
        return {"ok": False, "stderr": "Passphrase darf nicht leer sein"}
    dev = _devpath(device)
    return _run(["cryptsetup", "open", "--key-file=-", dev, name],
                timeout=60, input_text=passphrase)


def luks_close(name):
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Mapper-Name"}
    return _run(["cryptsetup", "close", name])


# --- Dateisystem-Resize (non-LVM) ---

def fs_grow(device="", mountpoint="", size="max"):
    """Dateisystem auf die Größe des darunterliegenden Devices erweitern.

    ext*: resize2fs (offline oder online). xfs/btrfs: online über Mountpoint.
    size 'max' füllt das gesamte Device; alternativ z. B. '20G' (nur ext*).
    """
    src = ""
    fstype = ""
    if mountpoint:
        info = _run(["findmnt", "-n", "-o", "SOURCE,FSTYPE", mountpoint])
        parts = info["stdout"].split()
        if info["ok"] and parts:
            src = parts[0]
            fstype = parts[1] if len(parts) > 1 else ""
    if not fstype and device:
        src = _devpath(device)
        out = _run(["lsblk", "-no", "FSTYPE", src])["stdout"].split()
        fstype = out[0] if out else ""
    if not src and device:
        src = _devpath(device)
    _guard(src or device or "")
    if fstype.startswith("ext"):
        cmd = ["resize2fs", src]
        if size and size != "max":
            cmd.append(size)
        return _run(cmd, timeout=300)
    if fstype == "xfs":
        if not mountpoint:
            return {"ok": False, "stderr": "XFS-Resize benötigt eingehängtes Dateisystem"}
        return _run(["xfs_growfs", mountpoint], timeout=300)
    if fstype == "btrfs":
        if not mountpoint:
            return {"ok": False, "stderr": "Btrfs-Resize benötigt eingehängtes Dateisystem"}
        return _run(["btrfs", "filesystem", "resize", size or "max", mountpoint],
                    timeout=300)
    return {"ok": False, "stderr": f"Resize für '{fstype or 'unbekannt'}' nicht unterstützt"}


# --- ZFS (zfsutils-linux) ---

def zfs_available():
    return _run(["zpool", "version"])["ok"]


def zfs_pools():
    if not zfs_available():
        return {"available": False, "pools": []}
    r = _run(["zpool", "list", "-H", "-o",
              "name,size,alloc,free,cap,frag,health"])
    pools = []
    if r["ok"]:
        for line in r["stdout"].splitlines():
            f = line.split("\t")
            if len(f) >= 7:
                pools.append({"name": f[0], "size": f[1], "alloc": f[2],
                              "free": f[3], "cap": f[4], "frag": f[5],
                              "health": f[6]})
    return {"available": True, "pools": pools}


def zfs_datasets():
    if not zfs_available():
        return {"available": False, "datasets": []}
    r = _run(["zfs", "list", "-H", "-o", "name,used,avail,refer,mountpoint"])
    ds = []
    if r["ok"]:
        for line in r["stdout"].splitlines():
            f = line.split("\t")
            if len(f) >= 5:
                ds.append({"name": f[0], "used": f[1], "avail": f[2],
                           "refer": f[3], "mountpoint": f[4]})
    return {"available": True, "datasets": ds}


def zpool_create(name, raid, devices):
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Pool-Name"}
    if not devices:
        return {"ok": False, "stderr": "Keine Devices angegeben"}
    for d in devices:
        _guard(d)
    dev_paths = [_devpath(d) for d in devices]
    cmd = ["zpool", "create", "-f", name]
    if raid and raid not in ("stripe", ""):
        if raid not in ("mirror", "raidz", "raidz2", "raidz3"):
            return {"ok": False, "stderr": "Unbekanntes RAID-Layout"}
        cmd.append(raid)
    cmd += dev_paths
    return _run(cmd, timeout=120)


def zpool_destroy(name):
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Pool-Name"}
    return _run(["zpool", "destroy", name])


def zpool_scrub(name):
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Pool-Name"}
    return _run(["zpool", "scrub", name])


# --- Btrfs-Pools (btrfs-progs) ---

def btrfs_available():
    return _run(["btrfs", "version"])["ok"]


def btrfs_filesystems():
    if not btrfs_available():
        return {"available": False, "filesystems": []}
    r = _run(["btrfs", "filesystem", "show"])
    fss = []
    cur = None
    if r["ok"]:
        for raw in r["stdout"].splitlines():
            line = raw.strip()
            if line.startswith("Label:"):
                m = re.search(r"Label:\s*(\S+)\s+uuid:\s*(\S+)", line)
                label = m.group(1).strip("'") if m else ""
                cur = {"label": "" if label in ("none", "") else label,
                       "uuid": m.group(2) if m else "", "devices": []}
                fss.append(cur)
            elif line.startswith("devid") and cur is not None:
                cur["devices"].append(line.split()[-1])
    return {"available": True, "filesystems": fss}


def btrfs_create(label, profile, devices):
    if label and not _valid_name(label):
        return {"ok": False, "stderr": "Ungueltiges Label"}
    if not devices:
        return {"ok": False, "stderr": "Keine Devices angegeben"}
    for d in devices:
        _guard(d)
    dev_paths = [_devpath(d) for d in devices]
    cmd = ["mkfs.btrfs", "-f"]
    if label:
        cmd += ["-L", label]
    if profile and profile != "single":
        if profile not in ("raid0", "raid1", "raid10"):
            return {"ok": False, "stderr": "Unbekanntes Profil"}
        cmd += ["-d", profile, "-m", profile]
    cmd += dev_paths
    return _run(cmd, timeout=300)


def btrfs_scrub(mountpoint):
    return _run(["btrfs", "scrub", "start", mountpoint])


# --- iSCSI-Initiator (open-iscsi) ---

def iscsi_available():
    return _run(["iscsiadm", "--version"])["ok"]


def iscsi_sessions():
    if not iscsi_available():
        return {"available": False, "sessions": []}
    r = _run(["iscsiadm", "-m", "session"])
    sessions = []
    if r["ok"]:
        for line in r["stdout"].splitlines():
            parts = line.split()
            # z.B.: tcp: [1] 192.168.1.10:3260,1 iqn.2003-01.org... (non-flash)
            if len(parts) >= 4:
                sessions.append({
                    "proto": parts[0].rstrip(":"),
                    "id": parts[1].strip("[]"),
                    "portal": parts[2].split(",")[0],
                    "target": parts[3],
                })
    return {"available": True, "sessions": sessions}


def iscsi_discover(portal):
    if not _PORTAL_RE.fullmatch(portal or ""):
        return {"ok": False, "targets": [], "stderr": "Ungueltiges Portal"}
    r = _run(["iscsiadm", "-m", "discovery", "-t", "sendtargets", "-p", portal],
             timeout=30)
    targets = []
    if r["ok"]:
        for line in r["stdout"].splitlines():
            parts = line.split()
            if len(parts) >= 2:
                targets.append({"portal": parts[0].split(",")[0],
                                "target": parts[1]})
    return {"ok": r["ok"], "targets": targets, "stderr": r["stderr"]}


def iscsi_login(portal, target):
    if not _PORTAL_RE.fullmatch(portal or "") or not _IQN_RE.fullmatch(target or ""):
        return {"ok": False, "stderr": "Ungueltiges Portal oder Target"}
    return _run(["iscsiadm", "-m", "node", "-T", target, "-p", portal,
                 "--login"], timeout=30)


def iscsi_logout(portal, target):
    if not _PORTAL_RE.fullmatch(portal or "") or not _IQN_RE.fullmatch(target or ""):
        return {"ok": False, "stderr": "Ungueltiges Portal oder Target"}
    return _run(["iscsiadm", "-m", "node", "-T", target, "-p", portal,
                 "--logout"], timeout=30)
