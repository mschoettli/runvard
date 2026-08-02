"""Policy-aware filesystem browsing for reusable path selection dialogs."""

from __future__ import annotations

import os
import re
import stat
import subprocess

import psutil

from modules import storage


BASE_SAFE_ROOTS = ("/mnt", "/media", "/srv")
PURPOSE_EXTRA_ROOTS = {
    "backup-source": ("/home",),
    "container-host": ("/opt/runvard/data/apps", "/opt/runvard/data/compose"),
    "app-host": ("/opt/runvard/data/apps", "/opt/runvard/data/compose"),
    "vm-image": ("/var/lib/libvirt/images", "/var/lib/libvirt/pools"),
    "vm-pool": ("/var/lib/libvirt/pools",),
}
FILE_EXTENSIONS = {
    "vm-image": (".qcow2", ".raw", ".img", ".iso"),
}
WRITABLE_PURPOSES = {
    "app-host", "backup-destination", "container-host", "mountpoint",
    "share", "swap-file", "time-machine", "vm-pool",
}
BLOCKED_ROOTS = ("/proc", "/sys", "/run")
MAX_ENTRIES = 500
_REMOTE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,253}[A-Za-z0-9])?$")


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path or "/")))


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((_real(path), _real(root))) == _real(root)
    except ValueError:
        return False


def _is_blocked(path: str) -> bool:
    return any(_is_within(path, root) for root in BLOCKED_ROOTS)


def _normal_roots(purpose: str) -> list[str]:
    candidates = [*BASE_SAFE_ROOTS, *PURPOSE_EXTRA_ROOTS.get(purpose, ())]
    for partition in psutil.disk_partitions(all=True):
        mountpoint = getattr(partition, "mountpoint", "")
        if (mountpoint and mountpoint != "/" and not _is_blocked(mountpoint)
                and any(_is_within(mountpoint, root) for root in candidates)):
            candidates.append(mountpoint)
    result = []
    for candidate in candidates:
        real = _real(candidate)
        if os.path.isdir(real) and real not in result and not _is_blocked(real):
            result.append(real)
    return result


def list_roots(purpose: str, expert: bool = False, mode: str = "folder") -> dict:
    roots = _normal_roots(purpose)
    if expert:
        roots = ["/", *[root for root in roots if root != "/"]]
    return {
        "roots": [{"path": root, "name": os.path.basename(root) or "/"} for root in roots],
        "expert": expert,
        "manual": expert,
        "mode": mode,
    }


def _authorize(path: str, purpose: str, expert: bool, mode: str) -> str:
    real = _real(path)
    if _is_blocked(real):
        raise PermissionError("Path is outside the allowed locations")
    if expert:
        return real
    if not any(_is_within(real, root) for root in _normal_roots(purpose)):
        raise PermissionError("Path is outside the allowed locations")
    return real


def _device_entries() -> list[dict]:
    rows = []
    payload = storage.list_block_devices()

    def leaves(device):
        children = device.get("children") or []
        if not children:
            yield device
        for child in children:
            yield from leaves(child)

    for disk in payload.get("devices", []):
        for device in leaves(disk):
            path = device.get("path") or device.get("name", "")
            if path and not path.startswith("/dev/"):
                path = f"/dev/{path}"
            if path and not device.get("protected"):
                rows.append({"name": device.get("name") or path, "path": path,
                             "type": "device", "size": device.get("size", 0)})
    return rows


def browse(path: str, purpose: str, mode: str = "folder", expert: bool = False) -> dict:
    if mode == "device":
        if not expert:
            raise PermissionError("Device selection requires expert mode")
        return {"path": "/dev", "parent": None, "entries": _device_entries(),
                "writable": False, "truncated": False}
    current = _authorize(path, purpose, expert, mode)
    if not os.path.isdir(current):
        raise ValueError("Path is not a folder")
    entries = []
    extensions = FILE_EXTENSIONS.get(purpose)
    with os.scandir(current) as iterator:
        for item in iterator:
            try:
                is_dir = item.is_dir(follow_symlinks=True)
                item_type = "folder" if is_dir else "file"
                if not is_dir and (mode != "file" or
                                   (extensions and not item.name.lower().endswith(extensions))):
                    continue
                entries.append({"name": item.name, "path": item.path, "type": item_type})
            except OSError:
                continue
    entries.sort(key=lambda row: (row["type"] != "folder", row["name"].casefold()))
    truncated = len(entries) > MAX_ENTRIES
    entries = entries[:MAX_ENTRIES]
    parent = os.path.dirname(current) if current != "/" else None
    if parent and not expert and not any(_is_within(parent, root) for root in _normal_roots(purpose)):
        parent = None
    return {"path": current, "parent": parent, "entries": entries,
            "writable": os.access(current, os.W_OK), "truncated": truncated}


def validate(path: str, purpose: str, mode: str = "folder", expert: bool = False) -> dict:
    real = _authorize(path, purpose, expert, mode)
    exists = os.path.exists(real)
    item_type = "missing"
    if exists:
        item_mode = os.stat(real).st_mode
        if stat.S_ISDIR(item_mode):
            item_type = "folder"
        elif stat.S_ISBLK(item_mode):
            item_type = "device"
        else:
            item_type = "file"
    readable = exists and os.access(real, os.R_OK)
    writable = exists and os.access(real, os.W_OK)
    expected = "file" if mode == "file" else mode
    type_ok = exists and item_type == expected
    extensions = FILE_EXTENSIONS.get(purpose)
    extension_ok = not extensions or item_type != "file" or real.lower().endswith(extensions)
    access_ok = writable if purpose in WRITABLE_PURPOSES else readable
    ok = type_ok and extension_ok and access_ok
    return {"ok": ok, "path": real, "exists": exists, "type": item_type,
            "readable": readable, "writable": writable}


def create_folder(parent: str, name: str, purpose: str, expert: bool = False) -> dict:
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise ValueError("Invalid folder name")
    target_parent = _authorize(parent, purpose, expert, "folder")
    if not os.path.isdir(target_parent):
        raise ValueError("Parent is not a folder")
    target = os.path.join(target_parent, name)
    os.mkdir(target)
    return {"ok": True, "path": target}


def discover_nfs_exports(server: str) -> dict:
    host = (server or "").strip()
    if not _REMOTE_HOST.fullmatch(host):
        raise ValueError("Invalid server address")
    result = subprocess.run(["showmount", "-e", host], capture_output=True,
                            text=True, timeout=8, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Could not query NFS exports")
    exports = []
    for line in result.stdout.splitlines()[1:]:
        path = line.split(maxsplit=1)[0] if line.strip() else ""
        if path.startswith("/"):
            exports.append({"name": path, "path": path, "type": "remote"})
    return {"server": host, "entries": exports}
