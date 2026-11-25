"""Backup jobs, safe path selection, storage discovery, and history."""
from contextlib import ExitStack, contextmanager
import fcntl
import glob
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time

import psutil

from modules import backup_mounts
from modules.backup_scheduler import is_due

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


def _lock_file(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    return os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), "a")


@contextmanager
def _transaction():
    with _lock_file(CONFIG + ".lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except FileNotFoundError:
        return []
    if not isinstance(rows, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return rows


def _save(path, data):
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".backup-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _new_id(used):
    # Job IDs travel through JavaScript numbers and FastAPI integer form fields.
    while True:
        candidate = secrets.randbelow(2**53 - 1) + 1
        if candidate not in used:
            return candidate


def _load_jobs():
    """Read under the metadata lock and repair colliding legacy second-based IDs."""
    jobs = _load(CONFIG)
    used = {job["id"] for job in jobs}
    if len(used) != len(jobs):
        seen = set()
        for job in jobs:
            if job["id"] in seen:
                job.setdefault("created_at", job["id"])
                job["id"] = _new_id(used)
                used.add(job["id"])
            seen.add(job["id"])
        _save(CONFIG, jobs)
    return jobs


def list_jobs():
    with _transaction():
        return _load_jobs()


def managed_destination(source, dest):
    # Identically named folders on different paths must have independent mirrors.
    suffix = hashlib.sha256(os.fsencode(_real(source))).hexdigest()[:16]
    parent, name = os.path.split(str(dest).rstrip("/"))
    if not os.path.isabs(dest) or not name:
        raise ValueError("The destination parent does not exist")
    prefix = name.encode("utf-8")[:238].decode("utf-8", "ignore")
    return os.path.join(parent, f"{prefix}-{suffix}")


def _mirror_conflicts(other, source, dest):
    if not other.get("source") or not other.get("dest"):
        return False
    other_dest = _real(other["dest"])
    if other_dest == dest and _real(other["source"]) == source:
        return False
    common = os.path.commonpath((other_dest, dest))
    return common in (other_dest, dest)


def _check_mirror_destination(jobs, source, dest):
    if any(_mirror_conflicts(other, source, dest) for other in jobs):
        raise ValueError("Backup destinations overlap")


def add_job(name, source, dest, schedule="manual", direction="push"):
    validation = validate_paths(source, dest)
    name = str(name or "").strip()
    if not name:
        raise ValueError("A backup name is required")
    if schedule not in {"manual", "hourly", "daily", "weekly"}:
        raise ValueError("Unsupported backup schedule")
    mounts = backup_mounts.snapshot(validation["source"], validation["dest"])
    with _transaction():
        jobs = _load_jobs()
        _check_mirror_destination(jobs, validation["source"], validation["dest"])
        jobs.append({
            "id": _new_id({job["id"] for job in jobs}),
            "created_at": time.time(),
            "name": name,
            "source": validation["source"],
            "dest": validation["dest"],
            "mounts": mounts,
            "schedule": schedule,
            "direction": direction,
            "last_run": None,
        })
        _save(CONFIG, jobs)
    return {"ok": True, "validation": validation}


def delete_job(job_id: int):
    with _transaction():
        jobs = _load_jobs()
        removed = next((job for job in jobs if job["id"] == job_id), None)
        if removed is None:
            raise KeyError(job_id)
        path = f"{CONFIG}.{job_id}.lock"
        with _lock_file(path) as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Running") from None
            remaining = [job for job in jobs if job["id"] != job_id]
            if removed.get("source") and removed.get("dest"):
                # Saving (and keeping) the removed job's files still matters after
                # deleting one half of an overlapping pair from an older release.
                for job in remaining:
                    if _mirror_conflicts(job, _real(removed["source"]), _real(removed["dest"])):
                        job["blocked_destination"] = _real(job["dest"])
                        job["enabled"] = False
            _save(CONFIG, remaining)
            os.unlink(path)
    return {"ok": True}


def schedule_enabled(job_id: int, enabled: bool):
    with _transaction():
        jobs = _load_jobs()
        job = next((job for job in jobs if job["id"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        job["enabled"] = enabled
        _save(CONFIG, jobs)
    return {"ok": True}


def run_job(job_id: int):
    return _run_job(job_id, scheduled_at=None)


def run_scheduled_job(job_id: int, now: float):
    return _run_job(job_id, scheduled_at=now)


def _run_job(job_id, scheduled_at):
    with ExitStack() as execution:
        with _transaction():
            jobs = _load_jobs()
            job = next((row for row in jobs if row["id"] == job_id), None)
            if scheduled_at is not None and (job is None or not is_due(job, scheduled_at)):
                return {"ok": True, "skipped": True}
            if job is None:
                return {"ok": False, "error": "Job nicht gefunden"}

            destination = _real(job["dest"])
            destination_key = hashlib.sha256(os.fsencode(destination)).hexdigest()
            job_lock = execution.enter_context(_lock_file(f"{CONFIG}.{job_id}.lock"))
            target_lock = execution.enter_context(_lock_file(f"{CONFIG}.target-{destination_key}.lock"))
            try:
                fcntl.flock(job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(target_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if scheduled_at is not None:
                    return {"ok": True, "skipped": True}
                return {"ok": False, "error": "Running"}

            # Claim this interval durably before rsync starts. No stale job list
            # gets written back when the process completes or after a restart.
            start = time.time() if scheduled_at is None else scheduled_at
            job["last_run"] = start
            _save(CONFIG, jobs)
        return _execute(job, start, destination, (job_lock.fileno(), target_lock.fileno()))


def _execute(job, start, destination, lock_fds):
    started = time.monotonic()
    try:
        validation = validate_paths(job["source"], destination)
        if job.get("blocked_destination") == validation["dest"]:
            raise ValueError("Backup destinations overlap")
        # Also guard jobs saved by older releases before any rsync --delete.
        with _transaction():
            _check_mirror_destination(_load_jobs(), validation["source"], validation["dest"])
        _verify_mounts(job, validation["source"], validation["dest"])
        os.makedirs(validation["dest"], exist_ok=True)
        r = subprocess.run(
            ["rsync", "-a", "--delete", "--stats", validation["source"] + "/", validation["dest"]],
            capture_output=True, text=True, timeout=3600, pass_fds=lock_fds,
        )
        ok = r.returncode == 0
        output = r.stdout if ok else r.stderr
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        ok, output = False, str(exc)
    duration = round(time.monotonic() - started, 1)
    with _transaction():
        history = _load(HISTORY)
        history.insert(0, {
            "job_id": job["id"], "name": job["name"], "time": start,
            "duration": duration, "success": ok, "output": output[-2000:],
        })
        _save(HISTORY, history[:100])
    result = {"ok": ok, "duration": duration}
    if not ok:
        result["error"] = output[-2000:]
    return result


def _verify_mounts(job, source, dest):
    observed = backup_mounts.snapshot(source, dest)
    if "mounts" in job:
        if job["mounts"] != observed:
            raise ValueError("Not mounted")
        return
    # Legacy jobs have no baseline. Persist only after path validation and
    # checking that every configured mount backing either path is present.
    with _transaction():
        jobs = _load_jobs()
        current = next((row for row in jobs if row["id"] == job["id"]), None)
        if current is not None:
            current["mounts"] = observed
            _save(CONFIG, jobs)


def get_history():
    return _load(HISTORY)
