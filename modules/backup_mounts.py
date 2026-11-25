"""Identify the filesystems a local rsync mirror would read and write."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil

from modules.files import _mount_field

_LINUX = sys.platform.startswith("linux")
_MOUNTINFO = Path("/proc/self/mountinfo")
_FSTAB = Path("/etc/fstab")
_DISKS = Path("/dev/disk")


def _mount_table():
    """Keep the last entry when a mount covers an existing mountpoint."""
    if not _LINUX:
        return {part.mountpoint: (part.fstype, "/", part.device, None)
                for part in psutil.disk_partitions(all=True)}
    mounts = {}
    for line in _MOUNTINFO.read_text().splitlines():
        before, sep, after = line.partition(" - ")
        fields, fs = before.split(), after.split()
        if sep and len(fields) >= 6 and len(fs) >= 2:
            mounts[_mount_field(fields[4])] = (
                fs[0], _mount_field(fields[3]), _mount_field(fs[1]), fields[2],
            )
    return mounts


def _configured_targets():
    if not _LINUX:
        return set()
    try:
        lines = _FSTAB.read_text().splitlines()
    except FileNotFoundError:
        return set()
    targets = set()
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 3 and fields[1].startswith("/") and fields[2] != "swap":
            targets.add(os.path.normpath(_mount_field(fields[1])))
    return targets


def _filesystem_identity(row):
    kind, root, source, device = row
    if kind == "autofs":
        raise ValueError("Not mounted")
    if _LINUX and source.startswith("/dev/"):
        # Linux device numbers and /dev/sdX names can change after a reboot.
        aliases = {os.path.realpath(source), os.path.realpath("/dev/block/" + device)}
        for category in ("by-uuid", "by-partuuid", "by-id"):
            link = next((p for p in sorted((_DISKS / category).glob("*"))
                         if os.path.realpath(p) in aliases), None)
            if link:
                source = str(link)
                break
        else:
            # A minimal installation may have blkid but no udev symlinks.
            try:
                value = subprocess.check_output(
                    ["blkid", "-s", "UUID", "-o", "value", source],
                    text=True, stderr=subprocess.DEVNULL, timeout=5,
                ).strip()
            except (OSError, subprocess.SubprocessError):
                value = ""
            if not value:
                raise ValueError("Not mounted")
            source = str(_DISKS / "by-uuid" / value)
    # Some remote filesystems include a username in the mount source.
    return hashlib.sha256(json.dumps([kind, root, source]).encode()).hexdigest()


def _within(path, parent):
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def snapshot(source, dest):
    """Capture covering and nested mounts, including missing fstab targets."""
    table = _mount_table()
    configured = _configured_targets()
    identities = {}
    result = {}
    for key, path in (("source", source), ("dest", dest)):
        covering = [p for p in table if _within(path, p)]
        if not covering:
            raise ValueError("Not mounted")
        for target in configured:
            if (_within(path, target) or _within(target, path)) and target not in table:
                raise ValueError("Not mounted")
        relevant = {max(covering, key=len)} | {p for p in table if _within(p, path)}
        for target in relevant:
            if target not in identities:
                identities[target] = _filesystem_identity(table[target])
        result[key] = {target: identities[target] for target in relevant}
    return result
