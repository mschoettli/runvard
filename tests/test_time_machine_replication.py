import json
import os
from pathlib import Path

import pytest

from modules import time_machine
from modules import time_machine_receiver


@pytest.fixture
def replication_env(tmp_path, monkeypatch):
    source_root = tmp_path / "source-pool"
    destination_root = tmp_path / "destination-pool"
    source_path = source_root / "runvard-time-machine" / "mac-a"
    source_path.mkdir(parents=True)
    destination_root.mkdir()
    (source_path / "Mac.sparsebundle").mkdir()
    (source_path / "Mac.sparsebundle" / "Info.plist").write_text("test")

    monkeypatch.setattr(time_machine, "STATE_FILE", str(tmp_path / "time-machine.json"))
    monkeypatch.setattr(time_machine, "REPLICATION_KEY", str(tmp_path / "keys" / "id_ed25519"), raising=False)
    monkeypatch.setattr(time_machine, "KNOWN_HOSTS_FILE", str(tmp_path / "keys" / "known_hosts"), raising=False)
    monkeypatch.setattr(time_machine, "_activate_managed_config", lambda *args: None)
    monkeypatch.setattr(
        time_machine,
        "_user_info",
        lambda name: {"name": name, "uid": 1001, "gid": 1001},
    )
    monkeypatch.setattr(time_machine, "_set_owner", lambda *args: None)
    monkeypatch.setattr(time_machine, "_target_allocated_bytes", lambda target: 0)

    def mount_info(path):
        real = os.path.realpath(path)
        if real == str(source_root):
            return {
                "mountpoint": str(source_root), "source": "/dev/source",
                "fstype": "ext4", "options": "rw",
            }
        if real == str(destination_root):
            return {
                "mountpoint": str(destination_root), "source": "/dev/destination",
                "fstype": "ext4", "options": "rw",
            }
        return None

    monkeypatch.setattr(time_machine, "_mount_info", mount_info)
    target = {
        "id": "a" * 16,
        "display_name": "Mac A",
        "share_name": "tm-mac-a-aaaaaaaa",
        "owner": "tm-maria",
        "storage_root": str(source_root),
        "mount_source": "/dev/source",
        "filesystem": "ext4",
        "path": str(source_path),
        "backend": "directory",
        "quota_mode": "reported",
        "hard_limit_bytes": 500 * 1024**3,
        "advertised_bytes": 475 * 1024**3,
        "enabled": True,
        "status": "active",
    }
    state = time_machine.load_state()
    state["targets"] = [target]
    time_machine.save_state(state)
    return {
        "source_root": source_root,
        "source_path": source_path,
        "destination_root": destination_root,
        "target": target,
    }


def test_local_replication_is_passive_and_uses_versioned_destination(replication_env):
    result = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
        schedule_hour=2,
    )

    replica = result["replication"]
    assert replica["transport"] == "local-rsync"
    assert replica["status"] == "passive"
    assert replica["destination_mount_source"] == "/dev/destination"
    assert replica["destination_path"].startswith(str(replication_env["destination_root"]))
    assert "password" not in json.dumps(time_machine.load_state())


def test_replication_policy_can_disable_and_cancel_queued_job(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    queued = time_machine.queue_replication(replica["id"])["job"]

    result = time_machine.update_replication_policy(
        replica["id"], schedule_hour=23, bandwidth_mbps=250, enabled=False,
        actor="alice", now=1234,
    )

    assert result["replication"]["enabled"] is False
    assert result["replication"]["status"] == "paused"
    assert result["replication"]["schedule_hour"] == 23
    assert result["replication"]["bandwidth_mbps"] == 250
    job = next(item for item in time_machine.list_jobs() if item["id"] == queued["id"])
    assert job["status"] == "cancelled"
    assert job["finished_at"] == 1234
    event = time_machine.list_events(limit=1)[0]
    assert event["type"] == "replication_policy_updated"
    assert event["actor"] == "alice"
    assert event["replication_id"] == replica["id"]
    assert event["changes"]["enabled"] == {"from": True, "to": False}


def test_promoted_replication_cannot_be_reenabled(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    state = time_machine.load_state()
    state["replications"][0].update(enabled=False, status="promoted")
    time_machine.save_state(state)

    with pytest.raises(ValueError, match="promoted"):
        time_machine.update_replication_policy(
            replica["id"], schedule_hour=2, bandwidth_mbps=0, enabled=True,
        )


@pytest.mark.parametrize(("hour", "bandwidth"), [(-1, 0), (24, 0), (2, -1), (2, 100_001)])
def test_replication_policy_rejects_invalid_limits(replication_env, hour, bandwidth):
    with pytest.raises(ValueError):
        time_machine.update_replication_policy(
            "b" * 16, schedule_hour=hour, bandwidth_mbps=bandwidth, enabled=True,
        )


def test_remove_target_disables_replication_and_cancels_queued_job(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    queued = time_machine.queue_replication(replica["id"])["job"]

    result = time_machine.remove_target(
        replication_env["target"]["id"], actor="alice", now=1234,
    )

    assert result["target"]["status"] == "removed"
    replication = time_machine.list_replications()[0]
    assert replication["enabled"] is False
    assert replication["status"] == "paused"
    job = next(item for item in time_machine.list_jobs() if item["id"] == queued["id"])
    assert job["status"] == "cancelled"
    event = time_machine.list_events(limit=1)[0]
    assert event["type"] == "target_removed"
    assert event["actor"] == "alice"
    assert event["disabled_replication_ids"] == [replica["id"]]
    assert event["cancelled_job_ids"] == [queued["id"]]


def test_remove_target_refuses_while_replication_is_running(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    state = time_machine.load_state()
    state["jobs"] = [{
        "id": "c" * 16, "type": "replication", "replication_id": replica["id"],
        "status": "running", "started_at": 1000,
    }]
    time_machine.save_state(state)

    with pytest.raises(ValueError, match="running"):
        time_machine.remove_target(replication_env["target"]["id"])

    assert time_machine.list_targets()[0]["status"] == "active"


def test_successful_rsync_replication_commits_new_version(replication_env, monkeypatch):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]

    def fake_run(command, **kwargs):
        if command[0] == "rsync":
            destination = Path(command[-1].rstrip("/"))
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "Mac.sparsebundle").mkdir()
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)

    result = time_machine.run_replication(replica["id"], now=1000)

    assert result["ok"] is True
    completed = time_machine.list_replications()[0]
    assert completed["status"] == "passive"
    assert completed["last_complete_at"] == 1000
    assert completed["last_complete_path"].endswith("/versions/1000")
    assert os.path.isdir(completed["last_complete_path"])


def test_replication_defers_when_source_pool_is_95_percent_full(
    replication_env, monkeypatch
):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    commands = []
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)
    monkeypatch.setattr(
        time_machine.shutil, "disk_usage",
        lambda path: type("Usage", (), {"total": 100, "used": 95, "free": 5})(),
    )
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command) or {"ok": True},
    )

    result = time_machine.run_replication(replica["id"], now=1000)

    assert result == {"ok": False, "deferred": True, "reason": "pool_full"}
    assert commands == []


def test_local_rsync_replica_keeps_two_latest_complete_versions(
    replication_env, monkeypatch
):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)

    def fake_run(command, **kwargs):
        if command[0] == "rsync":
            Path(command[-1].rstrip("/")).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)
    for timestamp in (1000, 2000, 3000):
        time_machine.run_replication(replica["id"], now=timestamp)

    versions = Path(replica["destination_path"]) / "versions"
    assert sorted(path.name for path in versions.iterdir()) == ["2000", "3000"]


def test_failed_replication_preserves_previous_complete_version(replication_env, monkeypatch):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    state = time_machine.load_state()
    previous = Path(replica["destination_path"]) / "versions" / "900"
    previous.mkdir(parents=True)
    state["replications"][0].update(
        last_complete_at=900, last_complete_path=str(previous), status="passive",
    )
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: {
            "ok": False, "returncode": 23, "stdout": "", "stderr": "transfer failed",
        },
    )

    with pytest.raises(RuntimeError, match="transfer failed"):
        time_machine.run_replication(replica["id"], now=1000)

    after = time_machine.list_replications()[0]
    assert after["last_complete_at"] == 900
    assert after["last_complete_path"] == str(previous)
    assert os.path.isdir(previous)


def test_remote_replication_rejects_public_destination(replication_env, monkeypatch):
    monkeypatch.setattr(time_machine, "_resolved_addresses", lambda host: ["203.0.113.10"], raising=False)

    with pytest.raises(ValueError, match="private LAN or VPN"):
        time_machine.create_replication(
            target_id=replication_env["target"]["id"],
            destination_root="/srv/runvard-replicas",
            remote_host="backup.example.test",
            remote_user="runvard-replica",
        )


def test_remote_command_requires_pinned_host_key_and_managed_identity(
    replication_env, monkeypatch
):
    monkeypatch.setattr(time_machine, "_resolved_addresses", lambda host: ["10.23.0.8"], raising=False)
    known_hosts = Path(time_machine.KNOWN_HOSTS_FILE)
    known_hosts.parent.mkdir(parents=True)
    known_hosts.write_text("[backup.vpn]:2222 ssh-ed25519 AAAATEST\n")
    key = Path(time_machine.REPLICATION_KEY)
    key.write_text("PRIVATE")
    os.chmod(key, 0o600)
    key.with_suffix(".pub").write_text("ssh-ed25519 AAAAPUBLIC runvard\n")

    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root="/srv/runvard-replicas",
        remote_host="backup.vpn",
        remote_user="runvard-replica",
        remote_port=2222,
    )["replication"]
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ssh":
            return {
                "ok": True,
                "stdout": '{"total": 1000, "used": 100, "free": 900}',
                "stderr": "",
            }
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)

    result = time_machine.run_replication(replica["id"], now=1000)

    assert result["ok"] is True
    rsync = next(command for command in commands if command[0] == "rsync")
    remote_shell = rsync[rsync.index("-e") + 1]
    assert f"IdentityFile={time_machine.REPLICATION_KEY}" in remote_shell
    assert "StrictHostKeyChecking=yes" in remote_shell
    assert f"UserKnownHostsFile={time_machine.KNOWN_HOSTS_FILE}" in remote_shell
    assert "backup.vpn" in rsync[-1]
    assert not any("PRIVATE" in part for part in rsync)


def test_remote_replication_checks_receiver_capacity_before_rsync(
    replication_env, monkeypatch
):
    monkeypatch.setattr(time_machine, "_resolved_addresses", lambda host: ["10.23.0.8"])
    known_hosts = Path(time_machine.KNOWN_HOSTS_FILE)
    known_hosts.parent.mkdir(parents=True)
    known_hosts.write_text("backup.vpn ssh-ed25519 AAAATEST\n")
    key = Path(time_machine.REPLICATION_KEY)
    key.write_text("PRIVATE")
    os.chmod(key, 0o600)
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root="/srv/runvard-replicas",
        remote_host="backup.vpn", remote_user="runvard-replica",
    )["replication"]
    commands = []
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ssh":
            return {
                "ok": True,
                "stdout": '{"total": 100, "used": 95, "free": 5}',
                "stderr": "",
            }
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    result = time_machine.run_replication(replica["id"], now=1000)

    assert result == {"ok": False, "deferred": True, "reason": "remote_pool_full"}
    assert commands[0][0] == "ssh"
    assert commands[0][-2:] == ["runvard-tm-capacity", "/srv/runvard-replicas"]
    assert not any(command[0] == "rsync" for command in commands)


def test_remote_replication_commits_staging_version_atomically(
    replication_env, monkeypatch
):
    monkeypatch.setattr(time_machine, "_resolved_addresses", lambda host: ["10.23.0.8"])
    known_hosts = Path(time_machine.KNOWN_HOSTS_FILE)
    known_hosts.parent.mkdir(parents=True)
    known_hosts.write_text("backup.vpn ssh-ed25519 AAAATEST\n")
    key = Path(time_machine.REPLICATION_KEY)
    key.write_text("PRIVATE")
    os.chmod(key, 0o600)
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root="/srv/runvard-replicas",
        remote_host="backup.vpn", remote_user="runvard-replica",
    )["replication"]
    commands = []
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ssh" and "runvard-tm-capacity" in command:
            return {"ok": True, "stdout": '{"total":100,"used":10,"free":90}', "stderr": ""}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    result = time_machine.run_replication(replica["id"], now=1000)

    rsync = next(command for command in commands if command[0] == "rsync")
    assert ".incomplete-" in rsync[-1]
    commit = next(command for command in commands if "runvard-tm-commit" in command)
    assert commit[-3] == "runvard-tm-commit"
    assert ".incomplete-" in commit[-2]
    assert commit[-1].endswith("/versions/1000")
    assert result["replication"]["last_complete_path"] == commit[-1]
    clean = next(command for command in commands if "runvard-tm-clean-staging" in command)
    assert clean[-3] == "runvard-tm-clean-staging"
    assert clean[-2] == replica["id"]


def test_forced_receiver_accepts_only_rsync_within_replica_root(tmp_path):
    root = tmp_path / "replicas"
    root.mkdir()
    versions = root / "runvard-time-machine-replicas" / ("b" * 16) / "versions"
    valid_destination = versions / ".incomplete-1000-abcd1234"

    argv = time_machine_receiver.validate_original_command(
        f"rsync --server -logDtpre.iLsfxCIvu . {valid_destination}", str(root),
    )

    assert argv[0] == "/usr/bin/rsync"
    assert argv[-1] == str(valid_destination)
    with pytest.raises(ValueError):
        time_machine_receiver.validate_original_command("sh -c id", str(root))
    with pytest.raises(ValueError):
        time_machine_receiver.validate_original_command(
            "rsync --server -logDtpre.iLsfxCIvu . /etc/cron.d/pwn", str(root),
        )
    with pytest.raises(ValueError):
        time_machine_receiver.validate_original_command(
            f"rsync --server --sender -logDtpre.iLsfxCIvu . {valid_destination}",
            str(root),
        )
    with pytest.raises(ValueError):
        time_machine_receiver.validate_original_command(
            f"rsync --server -logDtpre.iLsfxCIvu . {versions / '1000'}", str(root),
        )
    with pytest.raises(ValueError):
        time_machine_receiver.validate_original_command(
            f"rsync --server -e/bin/sh . {valid_destination}", str(root),
        )


def test_forced_receiver_capacity_command_is_confined_to_receiver_root(tmp_path):
    root = tmp_path / "replicas"
    root.mkdir()

    assert time_machine_receiver.validate_capacity_command(
        f"runvard-tm-capacity {root}", str(root),
    ) == str(root)
    with pytest.raises(ValueError):
        time_machine_receiver.validate_capacity_command(
            "runvard-tm-capacity /", str(root),
        )


def test_forced_receiver_atomically_commits_received_version(tmp_path):
    root = tmp_path / "replicas"
    versions = root / "runvard-time-machine-replicas" / ("d" * 16) / "versions"
    staging = versions / ".incomplete-1000-abcd1234"
    final = versions / "1000"
    staging.mkdir(parents=True)

    result = time_machine_receiver.commit_received_version(
        f"runvard-tm-commit {staging} {final}", str(root),
    )

    assert result == str(final)
    assert final.is_dir()
    assert (final / ".runvard-complete").is_file()
    with pytest.raises(ValueError):
        time_machine_receiver.commit_received_version(
            f"runvard-tm-commit {final} /etc/1000", str(root),
        )


def test_receiver_keeps_two_latest_committed_versions(tmp_path):
    root = tmp_path / "replicas"
    versions = root / "runvard-time-machine-replicas" / ("e" * 16) / "versions"
    for timestamp in (1000, 2000, 3000):
        staging = versions / f".incomplete-{timestamp}-abcd1234"
        staging.mkdir(parents=True)
        time_machine_receiver.commit_received_version(
            f"runvard-tm-commit {staging} {versions / str(timestamp)}", str(root),
        )

    assert sorted(path.name for path in versions.iterdir()) == ["2000", "3000"]


def test_receiver_cleans_only_expired_incomplete_versions(tmp_path):
    root = tmp_path / "replicas"
    replication_id = "e" * 16
    versions = root / "runvard-time-machine-replicas" / replication_id / "versions"
    old = versions / ".incomplete-1000-abcd1234"
    recent = versions / ".incomplete-3000-abcd1234"
    complete = versions / "900"
    for path in (old, recent, complete):
        path.mkdir(parents=True)

    removed = time_machine_receiver.clean_staging_versions(
        f"runvard-tm-clean-staging {replication_id} 2000", str(root),
    )

    assert removed == 1
    assert not old.exists()
    assert recent.is_dir()
    assert complete.is_dir()


def test_promotion_requires_paused_source_and_consumes_passive_replica(
    replication_env, monkeypatch
):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    version = Path(replica["destination_path"]) / "versions" / "1000"
    version.mkdir(parents=True)
    (version / "Mac.sparsebundle").mkdir()
    state = time_machine.load_state()
    state["replications"][0].update(
        last_complete_at=1000, last_complete_path=str(version), status="passive",
    )
    time_machine.save_state(state)

    with pytest.raises(ValueError, match="paused or removed"):
        time_machine.promote_replica(replica["id"], source_unavailable_confirmed=True)

    state = time_machine.load_state()
    state["targets"][0].update(enabled=False, status="paused")
    time_machine.save_state(state)
    promoted = time_machine.promote_replica(
        replica["id"], source_unavailable_confirmed=True, now=1100,
    )

    target = promoted["target"]
    assert target["enabled"] is True
    assert target["verification_required"] is True
    assert target["promoted_from_replication"] == replica["id"]
    assert target["storage_root"] == str(replication_env["destination_root"])
    assert os.path.isdir(target["path"])
    assert time_machine.list_replications()[0]["status"] == "promoted"


def test_failed_btrfs_promotion_restores_passive_snapshot_readonly(
    replication_env, monkeypatch
):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    snapshot = Path(replica["destination_path"]) / "snapshots" / "runvard-daily-1000"
    snapshot.mkdir(parents=True)
    state = time_machine.load_state()
    state["targets"][0].update(enabled=False, status="paused")
    state["replications"][0].update(
        transport="local-btrfs", destination_filesystem="btrfs",
        last_complete_at=1000, last_complete_path=str(snapshot), status="passive",
    )
    time_machine.save_state(state)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["btrfs", "qgroup", "limit"]:
            return {"ok": False, "stdout": "", "stderr": "quota failed"}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    with pytest.raises(RuntimeError, match="quota failed"):
        time_machine.promote_replica(
            replica["id"], source_unavailable_confirmed=True, now=1100,
        )

    assert snapshot.is_dir()
    assert [
        "btrfs", "property", "set", "-ts", str(snapshot), "ro", "true",
    ] in commands


def test_zfs_replication_uses_native_snapshot_stream(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_path = source_root / "mac"
    source_path.mkdir(parents=True)
    destination_root.mkdir()
    monkeypatch.setattr(time_machine, "STATE_FILE", str(tmp_path / "state.json"))

    def mount_info(path):
        if os.path.realpath(path) == str(source_root):
            return {"mountpoint": str(source_root), "source": "tank/source", "fstype": "zfs", "options": "rw"}
        if os.path.realpath(path) == str(destination_root):
            return {"mountpoint": str(destination_root), "source": "backup/replicas", "fstype": "zfs", "options": "rw"}
        return None

    monkeypatch.setattr(time_machine, "_mount_info", mount_info)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)
    target = {
        "id": "a" * 16, "display_name": "ZFS Mac", "share_name": "tm-zfs-aaaaaaaa",
        "owner": "tm-maria", "storage_root": str(source_root), "mount_source": "tank/source",
        "filesystem": "zfs", "path": str(source_path), "backend": "zfs",
        "dataset": "tank/source/runvard-timemachine/aaaaaaaaaaaaaaaa",
        "quota_mode": "hard", "hard_limit_bytes": 500 * 1024**3,
        "advertised_bytes": 475 * 1024**3, "enabled": True, "status": "active",
    }
    snapshot = target["dataset"] + "@runvard-daily-1000"
    state = time_machine.load_state()
    state["targets"] = [target]
    state["protection_points"] = [{
        "id": "b" * 16, "target_id": target["id"], "kind": "daily", "backend": "zfs",
        "native_name": snapshot, "created_at": 1000,
    }]
    time_machine.save_state(state)
    commands = []
    streams = []
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command) or {"ok": True, "stdout": "", "stderr": ""},
    )
    monkeypatch.setattr(
        time_machine, "_run_pipe",
        lambda source, destination, **kwargs: streams.append((source, destination)) or {
            "ok": True, "stdout": "", "stderr": "",
        },
        raising=False,
    )
    replica = time_machine.create_replication(
        target_id=target["id"], destination_root=str(destination_root),
    )["replication"]

    result = time_machine.run_replication(replica["id"], now=1100)

    destination_dataset = f"backup/replicas/runvard-replicas/{replica['id']}"
    assert replica["transport"] == "local-zfs"
    assert streams == [
        (["zfs", "send", snapshot], ["zfs", "receive", "-u", destination_dataset]),
    ]
    assert ["zfs", "set", "readonly=on", destination_dataset] in commands
    assert result["replication"]["last_source_point"] == snapshot
    assert result["replication"]["replica_native_name"] == (
        destination_dataset + "@runvard-daily-1000"
    )


def test_btrfs_replication_uses_readonly_subvolume_stream(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_path = source_root / "mac"
    snapshot = source_root / ".protection" / "runvard-daily-1000"
    source_path.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    destination_root.mkdir()
    monkeypatch.setattr(time_machine, "STATE_FILE", str(tmp_path / "state.json"))

    def mount_info(path):
        real = os.path.realpath(path)
        source = "/dev/source" if real == str(source_root) else "/dev/destination"
        root = source_root if real == str(source_root) else destination_root
        return {"mountpoint": str(root), "source": source, "fstype": "btrfs", "options": "rw"}

    monkeypatch.setattr(time_machine, "_mount_info", mount_info)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)
    target = {
        "id": "a" * 16, "display_name": "Btrfs Mac", "share_name": "tm-btrfs-aaaaaaaa",
        "owner": "tm-maria", "storage_root": str(source_root), "mount_source": "/dev/source",
        "filesystem": "btrfs", "path": str(source_path), "backend": "btrfs",
        "quota_mode": "hard", "hard_limit_bytes": 500 * 1024**3,
        "advertised_bytes": 475 * 1024**3, "enabled": True, "status": "active",
    }
    state = time_machine.load_state()
    state["targets"] = [target]
    state["protection_points"] = [{
        "id": "b" * 16, "target_id": target["id"], "kind": "daily", "backend": "btrfs",
        "native_name": str(snapshot), "created_at": 1000,
    }]
    time_machine.save_state(state)
    streams = []
    monkeypatch.setattr(
        time_machine, "_run_pipe",
        lambda source, destination, **kwargs: streams.append((source, destination)) or {
            "ok": True, "stdout": "", "stderr": "",
        },
        raising=False,
    )
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: {"ok": True, "stdout": "", "stderr": ""},
    )
    replica = time_machine.create_replication(
        target_id=target["id"], destination_root=str(destination_root),
    )["replication"]

    result = time_machine.run_replication(replica["id"], now=1100)

    receive_root = os.path.join(replica["destination_path"], "snapshots")
    assert replica["transport"] == "local-btrfs"
    assert streams == [
        (["btrfs", "send", str(snapshot)], ["btrfs", "receive", receive_root]),
    ]
    assert result["replication"]["last_source_point"] == str(snapshot)
    assert result["replication"]["last_complete_path"] == os.path.join(
        receive_root, snapshot.name,
    )


def test_zfs_promotion_switches_replica_dataset_to_writable_target(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    monkeypatch.setattr(time_machine, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(time_machine, "_activate_managed_config", lambda *args: None)
    monkeypatch.setattr(
        time_machine, "_user_info",
        lambda name: {"name": name, "uid": 1001, "gid": 1001},
    )
    monkeypatch.setattr(time_machine, "_set_owner", lambda *args: None)
    monkeypatch.setattr(
        time_machine, "_mount_info",
        lambda path: {
            "mountpoint": str(destination_root), "source": "backup/replicas",
            "fstype": "zfs", "options": "rw",
        },
    )
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["zfs", "set", f"mountpoint={destination_root}/runvard-time-machine/promoted-{'a' * 16}"]:
            os.makedirs(command[2].split("=", 1)[1], exist_ok=True)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)
    dataset = "backup/replicas/runvard-replicas/" + "b" * 16
    target = {
        "id": "a" * 16, "display_name": "Mac", "share_name": "tm-mac-aaaaaaaa",
        "owner": "tm-maria", "storage_root": str(source_root), "mount_source": "tank/source",
        "filesystem": "zfs", "path": str(source_root / "old"), "backend": "zfs",
        "dataset": "tank/source/mac", "quota_mode": "hard",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": False, "status": "paused",
    }
    replica = {
        "id": "b" * 16, "target_id": target["id"], "kind": "local",
        "transport": "local-zfs", "status": "passive", "enabled": True,
        "destination_root": str(destination_root),
        "destination_mount_source": "backup/replicas", "destination_filesystem": "zfs",
        "destination_path": str(destination_root / "runvard-time-machine-replicas" / ("b" * 16)),
        "replica_dataset": dataset, "last_complete_at": 1000,
        "last_complete_path": dataset + "@runvard-daily-1000",
    }
    state = time_machine.load_state()
    state["targets"] = [target]
    state["replications"] = [replica]
    time_machine.save_state(state)

    result = time_machine.promote_replica(
        replica["id"], source_unavailable_confirmed=True, now=1100,
    )

    promoted_path = str(destination_root / "runvard-time-machine" / f"promoted-{target['id']}")
    assert ["zfs", "set", "readonly=off", dataset] in commands
    assert ["zfs", "set", f"mountpoint={promoted_path}", dataset] in commands
    assert ["zfs", "set", f"refquota={target['hard_limit_bytes']}", dataset] in commands
    assert result["target"]["backend"] == "zfs"
    assert result["target"]["dataset"] == dataset
    assert result["target"]["quota_mode"] == "hard"


def test_queued_replication_is_completed_by_persistent_worker(
    replication_env, monkeypatch
):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    queued = time_machine.queue_replication(replica["id"])["job"]
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: False)

    def fake_run(command, **kwargs):
        if command[0] == "rsync":
            destination = Path(command[-1].rstrip("/"))
            destination.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    result = time_machine.process_replication_queue(limit=1, now=1000)

    assert result["processed"] == 1
    jobs = [job for job in time_machine.load_state()["jobs"] if job["id"] == queued["id"]]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"


def test_daily_schedule_queues_replication_once(replication_env):
    now = 1_800_000_000
    hour = time_machine.time.localtime(now).tm_hour
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
        schedule_hour=hour,
    )["replication"]

    first = time_machine.schedule_due_replications(now=now)
    second = time_machine.schedule_due_replications(now=now)

    assert first["queued"] == [replica["id"]]
    assert second["queued"] == []
    queued = [
        job for job in time_machine.load_state()["jobs"]
        if job.get("replication_id") == replica["id"] and job.get("status") == "queued"
    ]
    assert len(queued) == 1


def test_critical_health_alert_is_deduplicated(replication_env, monkeypatch):
    messages = []
    monkeypatch.setattr(
        time_machine, "health_check",
        lambda: {
            "targets": [{
                "id": replication_env["target"]["id"],
                "display_name": "Mac A", "status": "critical",
                "health_code": "mount_missing",
            }],
            "checked_at": 1000,
        },
    )
    monkeypatch.setattr(
        time_machine, "_alert_sink",
        lambda message: messages.append(message) or {"ok": True},
        raising=False,
    )

    time_machine.emit_health_alerts(now=1000)
    time_machine.emit_health_alerts(now=1001)

    assert len(messages) == 1
    assert "Mac A" in messages[0]
    assert "mount_missing" in messages[0]


def test_maintenance_worker_schedules_processes_and_alerts(replication_env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        time_machine, "schedule_due_replications",
        lambda now=None: calls.append("schedule") or {"ok": True, "queued": []},
    )
    monkeypatch.setattr(
        time_machine, "process_replication_queue",
        lambda limit=1, now=None: calls.append("process") or {
            "ok": True, "processed": 0, "deferred": 0, "errors": [],
        },
    )
    monkeypatch.setattr(
        time_machine, "emit_health_alerts",
        lambda now=None: calls.append("alerts") or {"ok": True, "emitted": []},
    )

    result = time_machine.run_scheduled_maintenance(now=1000)

    assert calls == ["schedule", "process", "alerts"]
    assert result["status"] == "completed"


def test_maintenance_reports_unrecoverable_interrupted_job(replication_env, monkeypatch):
    state = time_machine.load_state()
    state["jobs"] = [{
        "id": "f" * 16, "type": "replication", "replication_id": "e" * 16,
        "status": "running", "started_at": 900,
    }]
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "schedule_due_replications", lambda now=None: {"ok": True, "queued": []})
    monkeypatch.setattr(time_machine, "process_replication_queue", lambda limit=1, now=None: {"ok": True, "processed": 0, "deferred": 0, "errors": []})
    monkeypatch.setattr(time_machine, "emit_health_alerts", lambda now=None: {"ok": True, "emitted": []})

    result = time_machine.run_scheduled_maintenance(now=1000)

    assert result["status"] == "failed"
    assert any(error.get("component") == "job_recovery" for error in result["errors"])


def test_worker_recovers_interrupted_replication_job(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    state = time_machine.load_state()
    state["replications"][0]["status"] = "replicating"
    state["jobs"] = [{
        "id": "f" * 16, "type": "replication", "replication_id": replica["id"],
        "status": "running", "started_at": 900,
    }]
    time_machine.save_state(state)

    result = time_machine.recover_interrupted_jobs(now=1000)

    assert result == {"ok": True, "recovered": ["f" * 16]}
    recovered = time_machine.load_state()
    assert recovered["jobs"][0]["status"] == "queued"
    assert recovered["jobs"][0]["recovery_count"] == 1
    assert recovered["jobs"][0]["queued_at"] == 1000
    assert recovered["replications"][0]["status"] == "passive"


def test_worker_stops_requeueing_after_three_interruptions(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    state = time_machine.load_state()
    state["replications"][0]["status"] = "replicating"
    state["jobs"] = [{
        "id": "f" * 16, "type": "replication", "replication_id": replica["id"],
        "status": "running", "started_at": 900, "recovery_count": 2,
    }]
    time_machine.save_state(state)

    result = time_machine.recover_interrupted_jobs(now=1000)

    assert result == {"ok": False, "recovered": [], "failed": ["f" * 16]}
    failed = time_machine.load_state()
    assert failed["jobs"][0]["status"] == "failed"
    assert failed["jobs"][0]["recovery_count"] == 3
    assert failed["replications"][0]["status"] == "warning"
    assert "recovery limit" in failed["replications"][0]["last_error"]


def test_recovery_persists_failure_when_replication_was_removed(replication_env):
    state = time_machine.load_state()
    state["jobs"] = [{
        "id": "f" * 16, "type": "replication", "replication_id": "e" * 16,
        "status": "running", "started_at": 900,
    }]
    time_machine.save_state(state)

    result = time_machine.recover_interrupted_jobs(now=1000)

    assert result == {"ok": False, "recovered": [], "failed": ["f" * 16]}
    assert time_machine.load_state()["jobs"][0]["status"] == "failed"


def test_recovery_removes_only_managed_local_incomplete_versions(replication_env):
    replica = time_machine.create_replication(
        target_id=replication_env["target"]["id"],
        destination_root=str(replication_env["destination_root"]),
    )["replication"]
    versions = Path(replica["destination_path"]) / "versions"
    incomplete = versions / ".incomplete-900-abcd1234"
    complete = versions / "800"
    incomplete.mkdir(parents=True)
    complete.mkdir()
    state = time_machine.load_state()
    state["jobs"] = [{
        "id": "f" * 16, "type": "replication", "replication_id": replica["id"],
        "status": "running", "started_at": 900,
    }]
    time_machine.save_state(state)

    time_machine.recover_interrupted_jobs(now=1000)

    assert not incomplete.exists()
    assert complete.is_dir()


def test_time_machine_job_list_is_bounded_and_newest_first(replication_env):
    state = time_machine.load_state()
    state["jobs"] = [
        {"id": "a" * 16, "type": "maintenance", "started_at": 10, "status": "completed"},
        {"id": "b" * 16, "type": "replication", "queued_at": 20, "status": "queued"},
        {"id": "c" * 16, "type": "unrelated", "started_at": 30, "status": "completed"},
    ]
    time_machine.save_state(state)

    jobs = time_machine.list_jobs(limit=1)

    assert [job["id"] for job in jobs] == ["b" * 16]


def test_receiver_client_key_is_restricted_and_stored_without_injection(
    tmp_path, monkeypatch
):
    authorized = tmp_path / "authorized_keys"
    monkeypatch.setattr(
        time_machine, "RECEIVER_AUTHORIZED_KEYS", str(authorized), raising=False,
    )
    public = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey runvard-source"

    result = time_machine.register_replication_client_key(public)

    stored = authorized.read_text()
    assert result["ok"] is True
    assert stored.startswith("restrict,command=")
    assert public in stored
    assert os.stat(authorized).st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        time_machine.register_replication_client_key(public + "\nssh-rsa injected")


def test_received_remote_replica_can_be_promoted_with_new_credentials(
    replication_env, monkeypatch
):
    source_replication_id = "c" * 16
    received = (
        replication_env["destination_root"]
        / "runvard-time-machine-replicas" / source_replication_id
        / "versions" / "1000"
    )
    received.mkdir(parents=True)
    (received / "Mac.sparsebundle").mkdir()
    (received / ".runvard-complete").write_text("complete\n")
    state = time_machine.load_state()
    state["targets"] = []
    state["replications"] = []
    time_machine.save_state(state)
    commands = []
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command) or {
            "ok": True, "stdout": "", "stderr": "",
        },
    )

    result = time_machine.promote_received_replica(
        source_replication_id=source_replication_id,
        version="1000",
        display_name="Remote Mac",
        owner="tm-maria",
        storage_root=str(replication_env["destination_root"]),
        capacity_gb=500,
        password="correct-horse-battery",
        create_account=False,
        source_unavailable_confirmed=True,
        client_encryption_required=True,
        now=1100,
    )

    target = result["target"]
    assert target["enabled"] is True
    assert target["verification_required"] is True
    assert target["remote_replica_source"] == {
        "replication_id": source_replication_id, "version": "1000",
    }
    assert os.path.isdir(target["path"])
    assert not received.exists()
    assert ["smbpasswd", "-a", "-s", "tm-maria"] in commands


def test_received_replica_rolls_back_unusable_created_account(
    replication_env, monkeypatch
):
    source_replication_id = "c" * 16
    received = (
        replication_env["destination_root"]
        / "runvard-time-machine-replicas" / source_replication_id
        / "versions" / "1000"
    )
    received.mkdir(parents=True)
    (received / ".runvard-complete").write_text("complete\n")
    state = time_machine.load_state()
    state["targets"] = []
    state["replications"] = []
    time_machine.save_state(state)
    commands = []
    monkeypatch.setattr(time_machine, "_user_info", lambda name: None)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command)
        or {"ok": True, "stdout": "", "stderr": ""},
    )

    with pytest.raises(ValueError, match="does not exist"):
        time_machine.promote_received_replica(
            source_replication_id=source_replication_id, version="1000",
            display_name="Remote Mac", owner="tm-new",
            storage_root=str(replication_env["destination_root"]), capacity_gb=500,
            password="correct-horse-battery", create_account=True,
            source_unavailable_confirmed=True, client_encryption_required=True,
        )

    assert ["smbpasswd", "-x", "tm-new"] in commands
    assert ["userdel", "tm-new"] in commands
    assert received.is_dir()


def test_received_replica_move_failure_rolls_back_created_account(
    replication_env, monkeypatch
):
    source_replication_id = "c" * 16
    received = (
        replication_env["destination_root"]
        / "runvard-time-machine-replicas" / source_replication_id
        / "versions" / "1000"
    )
    received.mkdir(parents=True)
    (received / ".runvard-complete").write_text("complete\n")
    state = time_machine.load_state()
    state["targets"] = []
    state["replications"] = []
    time_machine.save_state(state)
    commands = []
    monkeypatch.setattr(
        time_machine,
        "_user_info",
        lambda name: (
            {"name": name, "uid": 1001, "gid": 1001}
            if any(command and command[0] == "useradd" for command in commands)
            else None
        ),
    )
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command)
        or {"ok": True, "stdout": "", "stderr": ""},
    )
    original_replace = time_machine.os.replace

    def fail_received_move(source, destination):
        if os.path.realpath(source) == os.path.realpath(received):
            raise OSError("simulated move failure")
        return original_replace(source, destination)

    monkeypatch.setattr(time_machine.os, "replace", fail_received_move)

    with pytest.raises(OSError, match="simulated move failure"):
        time_machine.promote_received_replica(
            source_replication_id=source_replication_id, version="1000",
            display_name="Remote Mac", owner="tm-new",
            storage_root=str(replication_env["destination_root"]), capacity_gb=500,
            password="correct-horse-battery", create_account=True,
            source_unavailable_confirmed=True, client_encryption_required=True,
        )

    assert ["smbpasswd", "-x", "tm-new"] in commands
    assert ["userdel", "tm-new"] in commands
    assert received.is_dir()


def test_received_replica_import_requires_encryption_policy_confirmation(tmp_path):
    with pytest.raises(ValueError, match="client-side encryption"):
        time_machine.promote_received_replica(
            source_replication_id="c" * 16, version="1000",
            display_name="Remote Mac", owner="tm-maria",
            storage_root=str(tmp_path), capacity_gb=500,
            password="correct-horse-battery", create_account=False,
            source_unavailable_confirmed=True,
        )
