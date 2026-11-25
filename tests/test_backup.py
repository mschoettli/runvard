import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from modules import backup


def test_configure_data_dir_moves_job_and_history_files(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "CONFIG", "/old/jobs.json")
    monkeypatch.setattr(backup, "HISTORY", "/old/history.json")

    backup.configure_data_dir(str(tmp_path))

    assert backup.CONFIG == str(tmp_path / "backup_jobs.json")
    assert backup.HISTORY == str(tmp_path / "backup_history.json")


def test_discover_locations_combines_common_folders_and_mounted_storage(tmp_path, monkeypatch):
    documents = tmp_path / "home" / "maria" / "Documents"
    documents.mkdir(parents=True)
    nas = tmp_path / "mnt" / "nas"
    nas.mkdir(parents=True)
    root = tmp_path / "rootfs"
    root.mkdir()

    monkeypatch.setattr(
        backup,
        "COMMON_SOURCE_GROUPS",
        [("documents", "Documents", "document", [str(documents)])],
        raising=False,
    )
    partitions = [
        SimpleNamespace(device="/dev/sda1", mountpoint=str(root), fstype="ext4", opts="rw"),
        SimpleNamespace(device="//nas/backups", mountpoint=str(nas), fstype="cifs", opts="rw"),
    ]
    monkeypatch.setattr(backup.psutil, "disk_partitions", lambda all=True: partitions)
    monkeypatch.setattr(
        backup.psutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1000, used=250, free=750, percent=25),
    )

    result = backup.discover_locations()

    assert result["sources"] == [{
        "id": "documents",
        "label": "Documents",
        "icon": "document",
        "path": str(documents),
    }]
    assert [target["kind"] for target in result["destinations"]] == ["local", "network"]
    assert result["destinations"][1]["recommended"] is True
    assert result["destinations"][1]["free"] == 750


def test_browse_directories_hides_blocked_system_locations(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    blocked = tmp_path / "blocked"
    allowed.mkdir()
    blocked.mkdir()
    monkeypatch.setattr(backup, "BLOCKED_PATHS", (str(blocked),), raising=False)

    listing = backup.browse_directories(str(tmp_path))

    assert listing["path"] == str(tmp_path)
    assert [entry["name"] for entry in listing["entries"]] == ["allowed"]
    assert listing["entries"][0]["readable"] is True


def test_validate_paths_rejects_destination_nested_inside_source(tmp_path):
    source = tmp_path / "photos"
    destination = source / "backup"
    source.mkdir()

    with pytest.raises(ValueError, match="inside the source"):
        backup.validate_paths(str(source), str(destination))


def test_validate_paths_reports_same_filesystem_and_available_space(tmp_path):
    source = tmp_path / "photos"
    destination = tmp_path / "backup" / "photos"
    source.mkdir()

    result = backup.validate_paths(str(source), str(destination))

    assert result["ok"] is True
    assert result["same_filesystem"] is True
    assert result["destination_writable"] is True
    assert result["free"] > 0


@pytest.fixture
def backup_paths(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(backup, "CONFIG", str(state / "backup_jobs.json"))
    monkeypatch.setattr(backup, "HISTORY", str(state / "backup_history.json"))
    source = tmp_path / "documents"
    source.mkdir()
    return str(source), str(tmp_path / "copy")


def test_run_preserves_job_added_and_job_deleted_during_rsync(backup_paths, monkeypatch):
    backup.add_job("original", *backup_paths)
    original, = backup.list_jobs()

    def during_rsync(*args, **kwargs):
        backup.add_job("new", *backup_paths)
        with pytest.raises(ValueError, match="Running"):
            backup.delete_job(original["id"])
        return SimpleNamespace(returncode=0, stdout="completed", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", during_rsync)
    assert backup.run_job(original["id"])["ok"]
    backup.delete_job(original["id"])
    remaining, = backup.list_jobs()
    assert remaining["name"] == "new"
    assert remaining["last_run"] is None
    entry, = backup.get_history()
    assert (entry["name"], entry["success"]) == ("original", True)


def test_parallel_creations_have_distinct_browser_safe_ids(backup_paths, monkeypatch):
    monkeypatch.setattr(backup.time, "time", lambda: 1_700_000_000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: backup.add_job(str(i), *backup_paths), range(24)))
    jobs = backup.list_jobs()
    assert len({row["id"] for row in jobs}) == 24
    assert all(0 < row["id"] < 2**53 for row in jobs)
    assert all(row["created_at"] == 1_700_000_000 for row in jobs)


def test_legacy_jobs_with_the_same_second_id_become_individually_addressable(backup_paths):
    path = Path(backup.CONFIG)
    path.parent.mkdir()
    old_id = 1_700_000_000
    path.write_text(json.dumps([{"id": old_id, "name": name} for name in ("A", "B")]))
    original, repaired = backup.list_jobs()
    assert original["id"] == old_id
    assert repaired["id"] != old_id
    assert repaired["created_at"] == old_id
    assert backup.list_jobs() == [original, repaired]
    backup.delete_job(original["id"])
    assert backup.list_jobs() == [repaired]


def test_corrupt_or_partial_write_never_overwrites_saved_jobs(backup_paths, monkeypatch):
    backup.add_job("saved", *backup_paths)
    path = Path(backup.CONFIG)
    saved = path.read_bytes()

    def interrupted_dump(data, stream, **kwargs):
        stream.write("[")
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(backup.json, "dump", interrupted_dump)
        with pytest.raises(OSError, match="disk full"):
            backup.add_job("interrupted", *backup_paths)
    assert path.read_bytes() == saved

    path.write_bytes(b"[")
    with pytest.raises(json.JSONDecodeError):
        backup.add_job("later", *backup_paths)
    assert path.read_bytes() == b"["


def test_manual_run_excludes_second_execution_and_records_preflight_errors(backup_paths, monkeypatch):
    backup.add_job("original", *backup_paths)
    job, = backup.list_jobs()
    starts = []

    def run_rsync(*args, **kwargs):
        starts.append(job["id"])
        if len(starts) == 1:
            assert not backup.run_job(job["id"])["ok"]
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", run_rsync)
    assert backup.run_job(job["id"])["ok"]
    assert starts == [job["id"]]
    Path(backup_paths[0]).rename(backup_paths[0] + "-offline")
    assert not backup.run_job(job["id"])["ok"]
    failed, passed = backup.get_history()
    assert not failed["success"] and passed["success"]
    assert failed["output"]


def test_distinct_sources_with_the_same_basename_keep_independent_mirrors(backup_paths, tmp_path):
    first, base = backup_paths
    second = tmp_path / "other" / Path(first).name
    second.mkdir(parents=True)
    dest_a = backup.managed_destination(first, base)
    dest_b = backup.managed_destination(str(second), base)
    assert dest_a != dest_b
    backup.add_job("First", first, dest_a)
    backup.add_job("Second", str(second), dest_b)
    with pytest.raises(ValueError, match="overlap"):
        backup.add_job("Collision", str(second), dest_a)
    with pytest.raises(ValueError, match="overlap"):
        backup.add_job("Nested", str(second), str(Path(dest_a) / "child"))
    jobs = backup.list_jobs()
    jobs[1]["dest"] = dest_a  # Overlapping jobs from an earlier release.
    Path(backup.CONFIG).write_text(json.dumps(jobs))
    for job in jobs:
        result = backup.run_job(job["id"])
        assert not result["ok"] and "overlap" in result["error"]
    backup.delete_job(jobs[0]["id"])
    assert not backup.run_job(jobs[1]["id"])["ok"]
    assert not Path(dest_a).exists()


def test_pause_resume_and_remove_preserve_destination_files(backup_paths):
    source, dest = backup_paths
    backup.add_job("Daily", source, dest, "daily")
    job, = backup.list_jobs()
    future = job["created_at"] + 86_401
    backup.schedule_enabled(job["id"], False)
    assert backup.run_scheduled_job(job["id"], future)["skipped"]
    backup.schedule_enabled(job["id"], True)
    assert backup.is_due(backup.list_jobs()[0], future)
    Path(dest).mkdir()
    retained = Path(dest) / "retained"
    retained.write_text("previous backup")
    backup.delete_job(job["id"])
    assert backup.list_jobs() == [] and retained.read_text() == "previous backup"


def test_two_legacy_jobs_share_one_execution_lock_for_the_resolved_destination(backup_paths, tmp_path, monkeypatch):
    source, dest = backup_paths
    alias = tmp_path / "same-destination"
    alias.symlink_to(dest, target_is_directory=True)
    jobs = [
        {"id": i, "name": str(i), "schedule": "daily", "source": source, "dest": path}
        for i, path in ((1, dest), (2, str(alias)))
    ]
    metadata = Path(backup.CONFIG)
    metadata.parent.mkdir()
    metadata.write_text(json.dumps(jobs))
    started, release = Event(), Event()
    invocations = []

    def rsync(command, **kwargs):
        invocations.append(command[-1])
        started.set()
        assert release.wait(5)
        return SimpleNamespace(returncode=0, stdout="completed", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", rsync)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(backup.run_job, 1)
        try:
            assert started.wait(5)
            assert backup.run_job(2) == {"ok": False, "error": "Running"}
            assert backup.run_scheduled_job(2, 90_000) == {"ok": True, "skipped": True}
            assert backup.list_jobs()[1].get("last_run") is None
            assert invocations == [dest]
        finally:
            release.set()
        assert first.result()["ok"]
    assert backup.run_scheduled_job(2, 90_000)["ok"]
    assert invocations == [dest, dest]
