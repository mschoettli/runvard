"""Backup: RSync-Jobs, 3-2-1 Strategie, Verlauf."""
import os
import json
import time
import subprocess

from modules.runtime import data_path
from modules import validators

CONFIG = data_path("backup_jobs.json")
HISTORY = data_path("backup_history.json")
SCHEDULES = {"manual", "hourly", "daily", "weekly"}


def _is_remote_path(path: str) -> bool:
    return not path.startswith("/") and ":" in path


def _validate_backup_source(path: str) -> str:
    if _is_remote_path(path):
        return validators.require_rsync_remote(path, "backup source")
    if not str(path or "").startswith("/"):
        raise ValueError("Backup source must be absolute")
    return validators.guard_read_path(path)


def _validate_backup_dest(path: str) -> str:
    if _is_remote_path(path):
        return validators.require_rsync_remote(path, "backup destination")
    if not str(path or "").startswith("/"):
        raise ValueError("Backup destination must be absolute")
    return validators.guard_write_path(path)


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        try:
            os.replace(path, f"{path}.corrupt-{int(time.time())}")
        except OSError:
            pass
        return default
    except OSError:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def list_jobs():
    return _load(CONFIG, [])


def add_job(name, source, dest, schedule="manual", direction="push"):
    name = validators.require_slug(name, "backup name")
    source = _validate_backup_source(source)
    dest = _validate_backup_dest(dest)
    if schedule not in SCHEDULES:
        return {"ok": False, "error": "Ungueltiger Zeitplan"}
    jobs = _load(CONFIG, [])
    jobs.append({
        "id": int(time.time()),
        "name": name,
        "source": source,
        "dest": dest,
        "schedule": schedule,   # manual, hourly, daily, weekly
        "direction": direction,  # push, pull
        "last_run": None,
    })
    _save(CONFIG, jobs)
    return {"ok": True}


def delete_job(job_id: int):
    jobs = [j for j in _load(CONFIG, []) if j["id"] != job_id]
    _save(CONFIG, jobs)
    return {"ok": True}


def run_job(job_id: int):
    jobs = _load(CONFIG, [])
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return {"ok": False, "error": "Job nicht gefunden"}

    start = time.time()
    success = False
    output = ""
    try:
        r = subprocess.run(
            ["rsync", "-a", "--delete", "--stats", job["source"] + "/", job["dest"]],
            capture_output=True, text=True, timeout=3600,
        )
        success = r.returncode == 0
        output = r.stdout[-2000:] if success else r.stderr[-2000:]
    except FileNotFoundError:
        output = "rsync nicht gefunden"
    except subprocess.TimeoutExpired as exc:
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else exc.stderr
        )
        output = (stderr or "rsync Zeitlimit erreicht")[-2000:]
    duration = round(time.time() - start, 1)

    job["last_run"] = start
    _save(CONFIG, jobs)

    history = _load(HISTORY, [])
    history.insert(0, {
        "job_id": job_id,
        "name": job["name"],
        "time": start,
        "duration": duration,
        "success": success,
        "output": output,
    })
    _save(HISTORY, history[:100])

    result = {"ok": success, "duration": duration}
    if not success:
        result["error"] = output
    return result


def get_history():
    return _load(HISTORY, [])
