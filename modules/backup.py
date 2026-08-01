"""Backup jobs, safe path selection, storage discovery, and history."""
import glob
import os
import json
import shutil
import time
import subprocess

import psutil

CONFIG = "/opt/runvard/data/backup_jobs.json"
HISTORY = "/opt/runvard/data/backup_history.json"

BLOCKED_PATHS = ("/proc", "/sys", "/dev", "/run")
PSEUDO_FILESYSTEMS = {
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs",
    "debugfs", "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs",
    "mqueue", "nsfs", "overlay", "proc", "pstore", "securityfs", "sysfs",
    "tracefs", "tmpfs",
}
NETWORK_FILESYSTEMS = {"cifs", "smb3", "nfs", "nfs4", "sshfs", "fuse.sshfs"}
COMMON_SOURCE_GROUPS = (
    ("documents", "Documents", "document", [
        "/home/*/Documents", "/home/*/Dokumente", "/root/Documents",
    ]),
    ("photos", "Photos", "photo", [
        "/home/*/Pictures", "/home/*/Bilder", "/srv/photos", "/srv/fotos",
    ]),
    ("media", "Media", "media", [
        "/srv/media", "/mnt/media", "/home/*/Videos", "/home/*/Music",
    ]),
    ("docker", "Docker data", "docker", [
        "/var/lib/docker/volumes", "/opt/stacks", "/srv/docker",
    ]),
    ("homes", "User folders", "users", ["/home"]),
)


def configure_data_dir(data_dir):
    """Point persistent backup metadata at Runvard's configured data directory."""
    global CONFIG, HISTORY
    CONFIG = os.path.join(data_dir, "backup_jobs.json")
    HISTORY = os.path.join(data_dir, "backup_history.json")


def _real(path):
    return os.path.realpath(os.path.abspath(path))


def _is_within(path, parent):
    try:
        return os.path.commonpath((_real(path), _real(parent))) == _real(parent)
    except ValueError:
        return False


def _is_blocked(path):
    resolved = _real(path)
    return any(
        resolved == _real(blocked) or resolved.startswith(_real(blocked) + os.sep)
        for blocked in BLOCKED_PATHS
    )


def _existing_ancestor(path):
    current = _real(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def _location_label(device, mountpoint, kind):
    if kind == "network":
        return device.replace("//", "", 1) or os.path.basename(mountpoint) or mountpoint
    if mountpoint == "/":
        return "System storage"
    return os.path.basename(mountpoint.rstrip(os.sep)) or mountpoint


def discover_locations():
    """Return useful backup sources and every usable mounted destination."""
    sources = []
    seen_sources = set()
    for group_id, label, icon, patterns in COMMON_SOURCE_GROUPS:
        matches = []
        for pattern in patterns:
            matches.extend(glob.glob(pattern))
        for path in matches:
            resolved = _real(path)
            if resolved in seen_sources or _is_blocked(resolved):
                continue
            if not os.path.isdir(resolved) or not os.access(resolved, os.R_OK):
                continue
            seen_sources.add(resolved)
            source_id = group_id if not any(row["id"] == group_id for row in sources) \
                else f"{group_id}-{len([row for row in sources if row['id'].startswith(group_id)]) + 1}"
            sources.append({
                "id": source_id,
                "label": label,
                "icon": icon,
                "path": resolved,
            })

    destinations = []
    seen_mounts = set()
    for part in psutil.disk_partitions(all=True):
        mountpoint = _real(getattr(part, "mountpoint", "") or "/")
        fstype = (getattr(part, "fstype", "") or "").lower()
        if mountpoint in seen_mounts or fstype in PSEUDO_FILESYSTEMS or _is_blocked(mountpoint):
            continue
        if not os.path.isdir(mountpoint):
            continue
        try:
            usage = psutil.disk_usage(mountpoint)
        except (OSError, PermissionError):
            continue
        seen_mounts.add(mountpoint)
        device = getattr(part, "device", "") or mountpoint
        kind = "network" if fstype in NETWORK_FILESYSTEMS else "local"
        opts = (getattr(part, "opts", "") or "").split(",")
        writable = "ro" not in opts and os.access(mountpoint, os.W_OK)
        destinations.append({
            "id": f"destination-{len(destinations) + 1}",
            "label": _location_label(device, mountpoint, kind),
            "path": mountpoint,
            "device": device,
            "filesystem": fstype,
            "kind": kind,
            "total": usage.total,
            "free": usage.free,
            "percent": usage.percent,
            "writable": writable,
            "recommended": bool(writable and (kind == "network" or mountpoint != "/")),
        })
    return {"sources": sources, "destinations": destinations}


def browse_directories(path="/"):
    """List readable child directories for the backup path picker."""
    current = _real(path or "/")
    if _is_blocked(current):
        raise PermissionError("This system location is not available for backups")
    if not os.path.isdir(current):
        raise NotADirectoryError(current)
    entries = []
    try:
        iterator = os.scandir(current)
    except (OSError, PermissionError) as exc:
        raise PermissionError("This folder cannot be opened") from exc
    with iterator:
        for entry in iterator:
            full = _real(os.path.join(current, entry.name))
            try:
                if not entry.is_dir(follow_symlinks=False) or _is_blocked(full):
                    continue
                entries.append({
                    "name": entry.name,
                    "path": full,
                    "readable": os.access(full, os.R_OK),
                    "writable": os.access(full, os.W_OK),
                })
            except OSError:
                continue
    entries.sort(key=lambda row: row["name"].lower())
    parent = os.path.dirname(current)
    if _is_blocked(parent):
        parent = current
    return {"path": current, "parent": parent, "entries": entries}


def validate_paths(source, dest):
    """Validate and describe a source/destination pair before saving a job."""
    source = _real(source)
    dest = _real(dest)
    if _is_blocked(source) or _is_blocked(dest):
        raise ValueError("This system location is not available for backups")
    if not os.path.isabs(source) or not os.path.isdir(source):
        raise ValueError("The source folder does not exist")
    if not os.access(source, os.R_OK):
        raise ValueError("The source folder is not readable")
    if source == dest:
        raise ValueError("Source and destination must be different")
    if _is_within(dest, source):
        raise ValueError("The destination cannot be inside the source")
    if _is_within(source, dest):
        raise ValueError("The source cannot be inside the destination")

    ancestor = _existing_ancestor(dest)
    if not os.path.isdir(ancestor):
        raise ValueError("The destination parent does not exist")
    writable = os.access(ancestor, os.W_OK)
    if not writable:
        raise ValueError("The destination is not writable")
    try:
        same_filesystem = os.stat(source).st_dev == os.stat(ancestor).st_dev
        usage = shutil.disk_usage(ancestor)
    except OSError as exc:
        raise ValueError("The destination cannot be inspected") from exc
    return {
        "ok": True,
        "source": source,
        "dest": dest,
        "destination_writable": writable,
        "same_filesystem": same_filesystem,
        "free": usage.free,
        "total": usage.total,
        "warning": "same-filesystem" if same_filesystem else "",
    }


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def list_jobs():
    return _load(CONFIG, [])


def add_job(name, source, dest, schedule="manual", direction="push"):
    validation = validate_paths(source, dest)
    name = str(name or "").strip()
    if not name:
        raise ValueError("A backup name is required")
    if schedule not in {"manual", "hourly", "daily", "weekly"}:
        raise ValueError("Unsupported backup schedule")
    jobs = _load(CONFIG, [])
    jobs.append({
        "id": int(time.time()),
        "name": name,
        "source": validation["source"],
        "dest": validation["dest"],
        "schedule": schedule,   # manual, hourly, daily, weekly
        "direction": direction,  # push, pull
        "last_run": None,
    })
    _save(CONFIG, jobs)
    return {"ok": True, "validation": validation}


def delete_job(job_id: int):
    jobs = [j for j in _load(CONFIG, []) if j["id"] != job_id]
    _save(CONFIG, jobs)
    return {"ok": True}


def run_job(job_id: int):
    jobs = _load(CONFIG, [])
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return {"ok": False, "error": "Job nicht gefunden"}

    try:
        validation = validate_paths(job["source"], job["dest"])
        os.makedirs(validation["dest"], exist_ok=True)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    start = time.time()
    try:
        r = subprocess.run(
            ["rsync", "-a", "--delete", "--stats", validation["source"] + "/", validation["dest"]],
            capture_output=True, text=True, timeout=3600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    duration = round(time.time() - start, 1)

    job["last_run"] = start
    _save(CONFIG, jobs)

    history = _load(HISTORY, [])
    history.insert(0, {
        "job_id": job_id,
        "name": job["name"],
        "time": start,
        "duration": duration,
        "success": r.returncode == 0,
        "output": r.stdout[-2000:] if r.returncode == 0 else r.stderr[-2000:],
    })
    _save(HISTORY, history[:100])

    return {"ok": r.returncode == 0, "duration": duration}


def get_history():
    return _load(HISTORY, [])
