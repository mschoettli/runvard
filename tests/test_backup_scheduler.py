import json
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("schedule,interval", [("hourly", 3600), ("daily", 86400), ("weekly", 604800)])
def test_first_interval_uses_creation_then_next_interval_uses_last_start(schedule, interval):
    from modules.backup_scheduler import is_due

    created = 1_700_000_000
    job = {"id": 42, "created_at": created, "last_run": None, "schedule": schedule}
    assert not is_due(job, created + interval - 1)
    assert is_due(job, created + interval)
    job["last_run"] = created + 2 * interval
    assert not is_due(job, job["last_run"] + interval - 1)
    assert is_due(job, job["last_run"] + interval)
    job["schedule"] = "manual"
    assert not is_due(job, job["last_run"] + 10 * interval)


def test_panel_startup_catches_up_one_legacy_run_and_restart_skips_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    from modules import backup
    from modules.backup_scheduler import BackupScheduler

    source = tmp_path / "source"
    source.mkdir()
    old_id = int(time.time()) - 20 * 86400
    config = tmp_path / "backup_jobs.json"
    config.write_text(json.dumps([{
        "id": old_id, "name": "Archive", "source": str(source),
        "dest": str(tmp_path / "copies"), "schedule": "daily", "last_run": None,
    }]))
    monkeypatch.setattr(backup, "CONFIG", str(config))
    monkeypatch.setattr(backup, "HISTORY", str(tmp_path / "history.json"))
    calls = []

    def rsync(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="copied", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", rsync)
    monkeypatch.setattr(server, "AUTH_CFG_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(server.accounts, "ensure_bootstrap_account", lambda: "operator")
    for component in ("NETWORK_MAP", "FEDERATION", "EXTERNAL_SERVERS"):
        monkeypatch.setenv(f"RUNVARD_{component}_NO_WORKER", "1")

    for _ in range(2):
        scanned = threading.Event()

        def list_and_signal():
            yield from backup.list_jobs()
            scanned.set()

        worker = BackupScheduler(list_and_signal, backup.run_scheduled_job)
        monkeypatch.setattr(server, "BACKUP_SCHEDULER", worker)
        with TestClient(server.app):
            assert scanned.wait(5), "the server did not finish its initial backup scan"

    assert len(calls) == 1
    assert calls[0][0] == "rsync"
    assert [entry["job_id"] for entry in backup.get_history()] == [old_id]


def test_one_failed_job_does_not_prevent_another_due_job_from_running():
    from modules.backup_scheduler import BackupScheduler

    done = threading.Event()
    calls = []

    def run(job_id, now):
        calls.append(job_id)
        if job_id == 1:
            raise OSError("cannot read backup paths")
        done.set()

    jobs = [{"id": 1, "schedule": "daily"}, {"id": 2, "schedule": "hourly"}]
    worker = BackupScheduler(lambda: jobs, run)
    worker.start()
    try:
        assert done.wait(5)
    finally:
        worker.stop()
    assert calls == [1, 2]
