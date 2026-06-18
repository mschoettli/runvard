"""Backup: RSync-Jobs, 3-2-1 Strategie, Verlauf."""
import os
import json
import time
import subprocess

CONFIG = "/opt/runvard/data/backup_jobs.json"
HISTORY = "/opt/runvard/data/backup_history.json"


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
    r = subprocess.run(
        ["rsync", "-a", "--delete", "--stats", job["source"] + "/", job["dest"]],
        capture_output=True, text=True, timeout=3600,
    )
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
