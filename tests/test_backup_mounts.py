import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules import backup


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    from modules import backup_mounts

    source = tmp_path / "photos"
    target = tmp_path / "copies"
    source.mkdir()
    target.mkdir()
    mountinfo = tmp_path / "mountinfo"
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    root = "1 0 0:1 / / rw - overlay overlay rw\n"
    usb = f"2 1 0:2 / {source} rw - nfs nas:/photos rw\n"
    disk = f"3 1 0:3 / {target} rw - cifs //nas/copies rw\n"
    mountinfo.write_text(root + usb + disk)
    monkeypatch.setattr(backup_mounts, "_LINUX", True)
    monkeypatch.setattr(backup_mounts, "_MOUNTINFO", mountinfo)
    monkeypatch.setattr(backup_mounts, "_FSTAB", fstab)
    monkeypatch.setattr(backup, "CONFIG", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(backup, "HISTORY", str(tmp_path / "history.json"))
    return SimpleNamespace(source=str(source), dest=str(target / "mirror"), table=mountinfo,
                           fstab=fstab, root=root, usb=usb, disk=disk)


@pytest.mark.parametrize("change", ["source missing", "target missing", "different export"])
def test_lost_or_replaced_volume_aborts_before_mkdir_and_rsync(volumes, monkeypatch, change):
    v = volumes
    backup.add_job("mirror", v.source, v.dest, "hourly")
    job, = backup.list_jobs()
    source = "" if change == "source missing" else v.usb
    target = "" if change == "target missing" else v.disk
    if change == "different export":
        source = source.replace("nas:/photos", "nas:/different")
    v.table.write_text(v.root + source + target)
    monkeypatch.setattr(backup.subprocess, "run", lambda *a, **kw: pytest.fail("rsync started"))
    result = backup.run_scheduled_job(job["id"], job["created_at"] + 3600)
    assert result["ok"] is False
    assert result["error"] == "Not mounted"
    assert not Path(v.dest).exists()
    assert backup.get_history()[0]["success"] is False


def test_legacy_baseline_waits_for_configured_mount_then_survives_reboot(volumes, monkeypatch):
    v = volumes
    Path(backup.CONFIG).write_text(json.dumps([{"id": 42, "source": v.source, "dest": v.dest,
                                                "schedule": "hourly", "name": "legacy"}]))
    v.fstab.write_text(f"nas:/photos {v.source} nfs nofail 0 0\n")
    v.table.write_text(v.root + v.disk)
    calls = []

    def copy(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", copy)
    assert not backup.run_job(42)["ok"]
    assert calls == []
    v.table.write_text(v.root + v.usb + v.disk)
    assert backup.run_job(42)["ok"]
    v.table.write_text(v.root + v.usb.replace("2 1 0:2", "70 1 0:41") + v.disk)
    assert backup.run_job(42)["ok"]
    v.fstab.write_text("")
    v.table.write_text(v.root + v.disk)
    assert not backup.run_job(42)["ok"]
    assert len(calls) == 2


def test_ordinary_directories_on_same_mount_still_run(volumes, monkeypatch):
    v = volumes
    v.table.write_text(v.root)
    backup.add_job("local", v.source, v.dest)
    monkeypatch.setattr(backup.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert backup.run_job(backup.list_jobs()[0]["id"])["ok"]
    assert Path(v.dest).is_dir()


def test_disk_uuid_survives_device_renumbering_and_detects_replacement(volumes, tmp_path, monkeypatch):
    from modules import backup_mounts

    v = volumes
    by_uuid = tmp_path / "disk" / "by-uuid"
    by_uuid.mkdir(parents=True)
    monkeypatch.setattr(backup_mounts, "_DISKS", by_uuid.parent)
    record = v.usb.replace("nfs nas:/photos", "ext4 /dev/sdb1")
    v.table.write_text(v.root + record + v.disk)
    original = by_uuid / "original-uuid"
    original.symlink_to("/dev/sdb1")
    baseline = backup_mounts.snapshot(v.source, v.dest)
    v.table.write_text(v.root + record.replace("/dev/sdb1", "/dev/sdd1") + v.disk)
    original.unlink()
    original.symlink_to("/dev/sdd1")
    assert backup_mounts.snapshot(v.source, v.dest) == baseline
    original.rename(by_uuid / "different-uuid")
    assert backup_mounts.snapshot(v.source, v.dest) != baseline


def test_lost_nested_source_mount_is_detected(volumes, monkeypatch):
    v = volumes
    nested = Path(v.source) / "nested"
    nested.mkdir()
    v.table.write_text(v.root + v.usb + v.disk + f"4 2 0:4 / {nested} rw - nfs nas:/more rw\n")
    backup.add_job("tree", v.source, v.dest)
    v.table.write_text(v.root + v.usb + v.disk)
    monkeypatch.setattr(backup.subprocess, "run", lambda *a, **kw: pytest.fail("rsync started"))
    assert not backup.run_job(backup.list_jobs()[0]["id"])["ok"]
