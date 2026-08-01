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
