import json
import os
import stat

import pytest

from modules import time_machine


@pytest.fixture
def tm_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    etc_dir = tmp_path / "etc" / "samba"
    pool = tmp_path / "pool"
    data_dir.mkdir()
    etc_dir.mkdir(parents=True)
    pool.mkdir()
    smb_conf = etc_dir / "smb.conf"
    managed_conf = etc_dir / "runvard-timemachine.conf"
    avahi_service = tmp_path / "etc" / "avahi" / "services" / "runvard-time-machine.service"
    smb_conf.write_text("[global]\n   workgroup = WORKGROUP\n")

    monkeypatch.setattr(time_machine, "STATE_FILE", str(data_dir / "time_machine.json"))
    monkeypatch.setattr(time_machine, "SMB_CONF", str(smb_conf))
    monkeypatch.setattr(time_machine, "MANAGED_SMB_CONF", str(managed_conf))
    monkeypatch.setattr(time_machine, "AVAHI_SERVICE", str(avahi_service))
    monkeypatch.setattr(
        time_machine,
        "_mount_info",
        lambda path: {
            "mountpoint": str(pool),
            "source": "/dev/test-pool",
            "fstype": "ext4",
            "options": "rw,relatime",
        },
    )
    monkeypatch.setattr(
        time_machine,
        "_user_info",
        lambda name: {"name": name, "uid": 1001, "gid": 1001},
    )
    monkeypatch.setattr(time_machine, "_set_owner", lambda *args: None)
    monkeypatch.setattr(time_machine, "_assert_creation_preflight", lambda: None)
    return {"data": data_dir, "etc": etc_dir, "pool": pool}


def test_rendered_share_requires_fruit_authentication_and_smb_encryption():
    rendered = time_machine.render_samba_config(
        [
            {
                "id": "7f5e3b08",
                "display_name": "MacBook Arbeit",
                "share_name": "tm-macbook-arbeit-7f5e3b08",
                "owner": "alice",
                "path": "/mnt/backups/runvard-time-machine/7f5e3b08",
                "advertised_bytes": 950 * 1024**3,
                "enabled": True,
            }
        ]
    )

    assert "[tm-macbook-arbeit-7f5e3b08]" in rendered
    assert "valid users = alice" in rendered
    assert "guest ok = no" in rendered
    assert "read only = no" in rendered
    assert "vfs objects = catia fruit streams_xattr" in rendered
    assert "fruit:time machine = yes" in rendered
    assert "fruit:time machine max size = 950G" in rendered
    assert "server smb encrypt = required" in rendered
    assert "hosts allow =" in rendered
    assert "10.0.0.0/8" in rendered
    assert "192.168.0.0/16" in rendered
    assert "100.64.0.0/10" in rendered
    assert "fc00::/7" in rendered
    assert "0.0.0.0/0" not in rendered


def test_creation_preflight_fails_closed_when_host_is_not_ready(monkeypatch):
    monkeypatch.setattr(
        time_machine, "system_status",
        lambda: {
            "ready": False,
            "samba": {"installed": True, "active": True, "configuration_valid": True},
            "avahi": {"active": True}, "worker": {"timer_active": True},
            "aapl_audit": {"ok": False, "risky_shares": ["legacy"]},
            "managed_config": {"drift": False},
        },
    )

    with pytest.raises(RuntimeError, match="AAPL.*legacy"):
        time_machine._assert_creation_preflight()


def test_bonjour_advertisement_contains_enabled_targets_only():
    rendered = time_machine.render_avahi_service([
        {"id": "7f5e3b08", "share_name": "tm-maria-7f5e3b08", "enabled": True},
        {"id": "8f5e3b08", "share_name": "tm-paused-8f5e3b08", "enabled": False},
    ])

    assert "_smb._tcp" in rendered
    assert "_adisk._tcp" in rendered
    assert "adVN=tm-maria-7f5e3b08" in rendered
    assert "tm-paused-8f5e3b08" not in rendered


def test_managed_include_is_inserted_in_global_section():
    source = "[global]\n   workgroup = WORKGROUP\n\n[public]\n   path = /srv/public\n"

    rendered = time_machine._main_with_include(source, time_machine.MANAGED_SMB_CONF)

    assert rendered.index("include = ") < rendered.index("[public]")


def test_managed_config_drift_requires_explicit_reconciliation(tm_env, monkeypatch):
    target = {
        "id": "7f5e3b08", "display_name": "MacBook",
        "share_name": "tm-macbook-7f5e3b08", "owner": "alice",
        "path": str(tm_env["pool"] / "runvard-time-machine" / "7f5e3b08"),
        "advertised_bytes": 100 * 1024**3, "enabled": True,
    }
    Path = __import__("pathlib").Path
    Path(time_machine.MANAGED_SMB_CONF).write_text("# external edit\n")
    commands = []
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command) or {"ok": True, "stdout": ""},
    )

    with pytest.raises(RuntimeError, match="reconcile"):
        time_machine._activate_managed_config([target], [target])

    assert commands == []


def test_explicit_reconciliation_uses_registered_targets(monkeypatch, tm_env):
    state = time_machine.load_state()
    state["targets"] = [{
        "id": "7f5e3b08", "share_name": "tm-macbook-7f5e3b08",
        "owner": "alice", "path": str(tm_env["pool"] / "target"),
        "advertised_bytes": 100 * 1024**3, "enabled": True,
    }]
    time_machine.save_state(state)
    captured = []
    monkeypatch.setattr(
        time_machine, "_activate_managed_config",
        lambda targets, previous, allow_drift=False: captured.append(
            (targets, previous, allow_drift)
        ),
    )

    result = time_machine.reconcile_managed_config()

    assert result["ok"] is True
    assert captured == [(state["targets"], state["targets"], True)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("share_name", "safe\n[global]"),
        ("owner", "alice valid users = root"),
        ("path", "/mnt/backups\nread only = yes"),
    ],
)
def test_rendered_share_rejects_samba_configuration_injection(field, value):
    target = {
        "id": "7f5e3b08",
        "display_name": "MacBook",
        "share_name": "tm-macbook-7f5e3b08",
        "owner": "alice",
        "path": "/mnt/backups/runvard-time-machine/7f5e3b08",
        "advertised_bytes": 950 * 1024**3,
        "enabled": True,
    }
    target[field] = value

    with pytest.raises(ValueError):
        time_machine.render_samba_config([target])


def test_create_directory_target_persists_no_password_and_uses_managed_include(
    tm_env, monkeypatch
):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    result = time_machine.create_target(
        display_name="Marias MacBook",
        owner="maria",
        storage_root=str(tm_env["pool"]),
        capacity_gb=1000,
        source_capacity_gb=500,
        password="never-store-this",
        client_encryption_required=True,
    )

    assert result["ok"] is True
    assert result["target"]["quota_mode"] == "reported"
    assert result["target"]["advertised_bytes"] < result["target"]["hard_limit_bytes"]
    assert result["target"]["path"].startswith(str(tm_env["pool"]))
    assert result["target"]["client_encryption_required"] is True
    assert result["target"]["client_encryption_policy_confirmed_at"] > 0
    assert os.path.isdir(result["target"]["path"])

    persisted = json.loads((tm_env["data"] / "time_machine.json").read_text())
    assert "never-store-this" not in json.dumps(persisted)
    assert stat.S_IMODE((tm_env["data"] / "time_machine.json").stat().st_mode) == 0o600

    main_conf = (tm_env["etc"] / "smb.conf").read_text()
    managed = (tm_env["etc"] / "runvard-timemachine.conf").read_text()
    assert f"include = {time_machine.MANAGED_SMB_CONF}" in main_conf
    assert result["target"]["share_name"] in managed
    assert ["smbpasswd", "-a", "-s", "maria"] in commands
    assert ["systemctl", "reload", "smbd"] in commands


def test_target_creation_requires_client_encryption_policy_confirmation(
    tm_env, monkeypatch
):
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: {"ok": True, "stdout": "", "stderr": ""},
    )

    with pytest.raises(ValueError, match="client-side encryption"):
        time_machine.create_target(
            display_name="Unconfirmed Mac", owner="maria",
            storage_root=str(tm_env["pool"]), capacity_gb=500,
            password="correct-horse-battery",
        )


def test_failed_samba_reload_restores_config_registry_and_empty_target(
    tm_env, monkeypatch
):
    original_main = (tm_env["etc"] / "smb.conf").read_text()

    def fake_run(command, **kwargs):
        if command == ["systemctl", "reload", "smbd"]:
            return {"ok": False, "stdout": "", "stderr": "reload failed"}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    with pytest.raises(RuntimeError, match="reload"):
        time_machine.create_target(
            display_name="Rollback Mac",
            owner="maria",
            storage_root=str(tm_env["pool"]),
            capacity_gb=1000,
            password="temporary-secret",
            client_encryption_required=True,
        )

    assert time_machine.list_targets() == []
    assert (tm_env["etc"] / "smb.conf").read_text() == original_main
    assert not list((tm_env["pool"] / "runvard-time-machine").glob("*"))


def test_health_check_fails_closed_when_expected_mount_disappears(tm_env, monkeypatch):
    state = {
        "version": 1,
        "targets": [
            {
                "id": "7f5e3b08",
                "display_name": "Offline Mac",
                "share_name": "tm-offline-mac-7f5e3b08",
                "owner": "alice",
                "storage_root": str(tm_env["pool"]),
                "mount_source": "/dev/test-pool",
                "path": str(tm_env["pool"] / "runvard-time-machine" / "7f5e3b08"),
                "advertised_bytes": 950 * 1024**3,
                "hard_limit_bytes": 1000 * 1024**3,
                "enabled": True,
                "status": "active",
            }
        ],
        "protection_points": [],
        "replications": [],
        "jobs": [],
    }
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_mount_info", lambda path: None)

    overview = time_machine.health_check()

    assert overview["targets"][0]["status"] == "critical"
    assert overview["targets"][0]["health_code"] == "mount_missing"
    assert not os.path.exists(state["targets"][0]["path"])


def test_observed_smb_activity_is_persisted_and_marks_target_active(tm_env, monkeypatch):
    target_path = tm_env["pool"] / "runvard-time-machine" / "7f5e3b08"
    target_path.mkdir(parents=True)
    state = time_machine.load_state()
    state["targets"] = [{
        "id": "7f5e3b08", "display_name": "Active Mac",
        "share_name": "tm-active-mac-7f5e3b08", "owner": "alice",
        "storage_root": str(tm_env["pool"]), "mount_source": "/dev/test-pool",
        "path": str(target_path), "enabled": True, "status": "waiting",
    }]
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: True)

    result = time_machine.observe_target_activity(now=1234)

    assert result == {"ok": True, "active": ["7f5e3b08"]}
    assert time_machine.load_state()["targets"][0]["last_activity"] == 1234
    assert time_machine.health_check()["targets"][0]["status"] == "active"


def test_password_failure_rolls_back_activated_share(tm_env, monkeypatch):
    def fake_run(command, **kwargs):
        if command[:2] == ["smbpasswd", "-a"]:
            return {"ok": False, "stdout": "", "stderr": "password failed"}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    with pytest.raises(RuntimeError, match="password"):
        time_machine.create_target(
            display_name="Password Rollback",
            owner="maria",
            storage_root=str(tm_env["pool"]),
            capacity_gb=500,
            password="correct-horse-battery",
            client_encryption_required=True,
        )

    assert time_machine.list_targets() == []
    managed_path = tm_env["etc"] / "runvard-timemachine.conf"
    assert not managed_path.exists() or "[tm-" not in managed_path.read_text()


def test_create_target_can_create_backup_only_account(tm_env, monkeypatch):
    commands = []
    account = {"value": None}

    def fake_user_info(name):
        if account["value"] == name:
            return {"name": name, "uid": 1001, "gid": 1001}
        return None

    def fake_run(command, **kwargs):
        commands.append(command)
        if command and command[0] == "useradd":
            account["value"] = command[-1]
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_user_info", fake_user_info)
    monkeypatch.setattr(time_machine, "_run", fake_run)

    result = time_machine.create_target(
        display_name="Neuer Mac",
        owner="tm-maria",
        storage_root=str(tm_env["pool"]),
        capacity_gb=500,
        password="correct-horse-battery",
        create_account=True,
        client_encryption_required=True,
    )

    assert result["ok"] is True
    assert [
        "useradd", "--system", "--no-create-home", "--shell",
        "/usr/sbin/nologin", "--user-group", "tm-maria",
    ] in commands


def test_unusable_new_backup_account_is_rolled_back(tm_env, monkeypatch):
    commands = []
    monkeypatch.setattr(time_machine, "_user_info", lambda name: None)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command)
        or {"ok": True, "stdout": "", "stderr": ""},
    )

    with pytest.raises(ValueError, match="does not exist"):
        time_machine.create_target(
            display_name="Account Rollback", owner="tm-new",
            storage_root=str(tm_env["pool"]), capacity_gb=500,
            password="correct-horse-battery", create_account=True,
            client_encryption_required=True,
        )

    assert ["smbpasswd", "-x", "tm-new"] in commands
    assert ["userdel", "tm-new"] in commands


def test_pause_remove_and_delete_keep_data_until_explicit_delete(tm_env, monkeypatch):
    monkeypatch.setattr(
        time_machine,
        "_run",
        lambda command, **kwargs: {"ok": True, "stdout": "", "stderr": ""},
    )
    created = time_machine.create_target(
        display_name="Lifecycle Mac",
        owner="maria",
        storage_root=str(tm_env["pool"]),
        capacity_gb=500,
        password="correct-horse-battery",
        client_encryption_required=True,
    )["target"]
    marker = os.path.join(created["path"], "backupbundle")
    os.mkdir(marker)

    paused = time_machine.set_target_enabled(created["id"], False)
    assert paused["target"]["status"] == "paused"
    assert f"[{created['share_name']}]" not in (
        tm_env["etc"] / "runvard-timemachine.conf"
    ).read_text()

    removed = time_machine.remove_target(created["id"])
    assert removed["data_preserved"] is True
    assert os.path.isdir(created["path"])
    assert time_machine.list_targets()[0]["status"] == "removed"

    deleted = time_machine.delete_target_data(created["id"])
    assert deleted["ok"] is True
    assert not os.path.exists(created["path"])
    assert time_machine.list_targets() == []


def test_delete_target_data_refuses_open_backup_handles(tm_env, monkeypatch):
    state = time_machine.load_state()
    path = tm_env["pool"] / "runvard-time-machine" / "mac-a"
    path.mkdir(parents=True)
    state["targets"] = [{
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(path),
        "backend": "directory", "enabled": False, "status": "removed",
    }]
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: True)

    with pytest.raises(ValueError, match="open backup handles"):
        time_machine.delete_target_data("a" * 16)

    assert path.is_dir()


def test_missing_mount_is_quarantined_and_removed_from_advertisement(
    tm_env, monkeypatch
):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "directory", "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
    }
    state["targets"] = [target]
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_mount_info", lambda path: None)
    activated = []
    monkeypatch.setattr(
        time_machine, "_activate_managed_config",
        lambda targets, previous: activated.append((targets, previous)),
    )

    result = time_machine.quarantine_unavailable_targets(now=1000)

    assert result["quarantined"] == [{
        "target_id": target["id"], "health_code": "mount_missing",
    }]
    quarantined = time_machine.list_targets()[0]
    assert quarantined["enabled"] is False
    assert quarantined["status"] == "critical"
    assert activated[0][0][0]["enabled"] is False
    event = time_machine.list_events(limit=1)[0]
    assert event["type"] == "target_quarantined"
    assert event["created_at"] == 1000


def test_usage_refresh_persists_allocated_bytes_for_overview(tm_env, monkeypatch):
    state = time_machine.load_state()
    path = tm_env["pool"] / "runvard-time-machine" / "mac-a"
    path.mkdir(parents=True)
    state["targets"] = [{
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(path),
        "backend": "directory", "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
    }]
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_target_allocated_bytes", lambda target: 42)

    result = time_machine.refresh_target_usage(now=1000)

    assert result == {"ok": True, "updated": ["a" * 16], "errors": []}
    target = time_machine.health_check()["targets"][0]
    assert target["allocated_bytes"] == 42
    assert target["usage_measured_at"] == 1000


def test_zfs_target_uses_native_quota_and_storage_protection(tm_env, monkeypatch):
    commands = []
    monkeypatch.setattr(
        time_machine,
        "_mount_info",
        lambda path: {
            "mountpoint": str(tm_env["pool"]),
            "source": "tank/backups",
            "fstype": "zfs",
            "options": "rw,relatime",
        },
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["zfs", "create"]:
            mountpoint_arg = next(value for value in command if value.startswith("mountpoint="))
            os.makedirs(mountpoint_arg.split("=", 1)[1], exist_ok=True)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)
    created = time_machine.create_target(
        display_name="ZFS Mac",
        owner="maria",
        storage_root=str(tm_env["pool"]),
        capacity_gb=1000,
        password="correct-horse-battery",
        client_encryption_required=True,
    )["target"]

    assert created["backend"] == "zfs"
    assert created["quota_mode"] == "hard"
    assert any(command[:2] == ["zfs", "create"] for command in commands)
    assert [
        "zfs", "set", f"refquota={1000 * 1024**3}", created["dataset"],
    ] in commands

    point = time_machine.create_protection_point(created["id"], kind="daily", now=1000)
    assert point["ok"] is True
    assert point["protection_point"]["kind"] == "daily"
    assert [
        "zfs", "snapshot", f"{created['dataset']}@runvard-daily-1000",
    ] in commands
    assert time_machine.list_protection_points(created["id"])[0]["native_name"].endswith(
        "@runvard-daily-1000"
    )


def test_directory_target_policy_updates_capacity_and_retention(tm_env, monkeypatch):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "directory", "quota_mode": "reported",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
        "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
    }
    state["targets"] = [target]
    time_machine.save_state(state)
    activated = []
    monkeypatch.setattr(
        time_machine, "_activate_managed_config",
        lambda targets, previous: activated.append((targets, previous)),
    )

    result = time_machine.update_target_policy(
        target["id"], capacity_gb=800, daily=14, weekly=8, monthly=6,
    )

    updated = result["target"]
    assert updated["hard_limit_bytes"] == 800 * 1024**3
    assert updated["advertised_bytes"] == 760 * 1024**3
    assert updated["protection_policy"] == {"daily": 14, "weekly": 8, "monthly": 6}
    assert activated[0][0][0]["hard_limit_bytes"] == 800 * 1024**3
    assert time_machine.list_targets()[0]["protection_policy"]["monthly"] == 6


def test_target_policy_refuses_shrink_below_allocated_usage(tm_env, monkeypatch):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "directory", "quota_mode": "reported",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
        "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
    }
    state["targets"] = [target]
    time_machine.save_state(state)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: {
            "ok": True, "stdout": f"{96 * 1024**3}\t{target['path']}\n", "stderr": "",
        },
    )
    monkeypatch.setattr(
        time_machine, "_activate_managed_config",
        lambda *args: pytest.fail("configuration must not change"),
    )

    with pytest.raises(ValueError, match="currently uses"):
        time_machine.update_target_policy(
            target["id"], capacity_gb=100, daily=7, weekly=4, monthly=3,
        )

    assert time_machine.list_targets()[0]["hard_limit_bytes"] == 500 * 1024**3


def test_target_policy_fails_closed_when_shrink_usage_cannot_be_measured(
    tm_env, monkeypatch
):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "directory", "quota_mode": "reported",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
        "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
    }
    state["targets"] = [target]
    time_machine.save_state(state)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: {"ok": False, "stdout": "", "stderr": "I/O error"},
    )

    with pytest.raises(RuntimeError, match="measure target usage"):
        time_machine.update_target_policy(
            target["id"], capacity_gb=100, daily=7, weekly=4, monthly=3,
        )


def test_target_policy_records_bounded_change_event(tm_env, monkeypatch):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "directory", "quota_mode": "reported",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
        "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
    }
    state["targets"] = [target]
    state["events"] = [{"id": f"{index:016x}", "created_at": index}
                       for index in range(200)]
    time_machine.save_state(state)
    monkeypatch.setattr(time_machine, "_activate_managed_config", lambda *args: None)

    time_machine.update_target_policy(
        target["id"], capacity_gb=800, daily=14, weekly=8, monthly=6,
        actor="alice", now=1000,
    )

    events = time_machine.list_events(limit=10)
    assert len(time_machine.load_state()["events"]) == 200
    assert events[0]["type"] == "target_policy_updated"
    assert events[0]["actor"] == "alice"
    assert events[0]["target_id"] == target["id"]
    assert events[0]["changes"]["capacity_gb"] == {"from": 500, "to": 800}
    assert events[0]["changes"]["protection_policy"]["to"]["daily"] == 14
    assert events[0]["created_at"] == 1000


def test_zfs_target_policy_rolls_back_native_quota_when_config_fails(
    tm_env, monkeypatch
):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "zfs", "dataset": "tank/backups/mac-a", "quota_mode": "hard",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
        "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
    }
    state["targets"] = [target]
    time_machine.save_state(state)
    commands = []
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command) or {"ok": True, "stderr": ""},
    )
    monkeypatch.setattr(
        time_machine, "_activate_managed_config",
        lambda *args: (_ for _ in ()).throw(RuntimeError("reload failed")),
    )

    with pytest.raises(RuntimeError, match="reload failed"):
        time_machine.update_target_policy(
            target["id"], capacity_gb=800, daily=7, weekly=4, monthly=3,
        )

    assert commands == [
        ["zfs", "set", f"refquota={800 * 1024**3}", "tank/backups/mac-a"],
        ["zfs", "set", f"refquota={500 * 1024**3}", "tank/backups/mac-a"],
    ]
    assert time_machine.list_targets()[0]["hard_limit_bytes"] == 500 * 1024**3


def test_target_policy_restores_managed_config_when_state_save_fails(
    tm_env, monkeypatch
):
    state = time_machine.load_state()
    target = {
        "id": "a" * 16, "display_name": "Mac A", "share_name": "tm-mac-a",
        "owner": "maria", "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool", "path": str(tm_env["pool"] / "mac-a"),
        "backend": "directory", "quota_mode": "reported",
        "hard_limit_bytes": 500 * 1024**3, "advertised_bytes": 475 * 1024**3,
        "enabled": True, "status": "active",
        "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
    }
    state["targets"] = [target]
    time_machine.save_state(state)
    activations = []
    monkeypatch.setattr(
        time_machine, "_activate_managed_config",
        lambda targets, previous: activations.append(
            (targets[0]["hard_limit_bytes"], previous[0]["hard_limit_bytes"])
        ),
    )
    monkeypatch.setattr(
        time_machine, "save_state",
        lambda candidate: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        time_machine.update_target_policy(
            target["id"], capacity_gb=800, daily=14, weekly=8, monthly=6,
        )

    assert activations == [
        (800 * 1024**3, 500 * 1024**3),
        (500 * 1024**3, 800 * 1024**3),
    ]


@pytest.mark.parametrize(
    ("capacity", "daily", "weekly", "monthly"),
    [(9, 7, 4, 3), (10_000_001, 7, 4, 3), (500, -1, 4, 3), (500, 7, 366, 3)],
)
def test_target_policy_rejects_invalid_limits(
    tm_env, capacity, daily, weekly, monthly
):
    with pytest.raises(ValueError):
        time_machine.update_target_policy(
            "a" * 16, capacity_gb=capacity, daily=daily,
            weekly=weekly, monthly=monthly,
        )


def test_protection_point_defers_while_bundle_files_are_open(tm_env, monkeypatch):
    monkeypatch.setattr(
        time_machine,
        "_mount_info",
        lambda path: {
            "mountpoint": str(tm_env["pool"]),
            "source": "tank/backups",
            "fstype": "zfs",
            "options": "rw",
        },
    )

    def fake_run(command, **kwargs):
        if command[:2] == ["zfs", "create"]:
            mountpoint_arg = next(value for value in command if value.startswith("mountpoint="))
            os.makedirs(mountpoint_arg.split("=", 1)[1], exist_ok=True)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)
    created = time_machine.create_target(
        display_name="Busy Mac",
        owner="maria",
        storage_root=str(tm_env["pool"]),
        capacity_gb=500,
        password="correct-horse-battery",
        client_encryption_required=True,
    )["target"]
    monkeypatch.setattr(time_machine, "_target_has_open_handles", lambda target: True)

    result = time_machine.create_protection_point(created["id"], now=1000)

    assert result == {"ok": False, "deferred": True, "reason": "target_busy"}
    assert time_machine.list_protection_points(created["id"]) == []


def test_system_status_reports_dependencies_aapl_risk_and_config_drift(
    tm_env, monkeypatch
):
    state = time_machine.load_state()
    state["targets"] = [{
        "id": "7f5e3b08",
        "share_name": "tm-test-7f5e3b08",
        "owner": "maria",
        "path": str(tm_env["pool"] / "runvard-time-machine" / "test"),
        "advertised_bytes": 95 * 1024**3,
        "enabled": True,
    }]
    time_machine.save_state(state)
    (tm_env["etc"] / "runvard-timemachine.conf").write_text("externally edited\n")

    def fake_run(command, **kwargs):
        if command[:2] == ["smbd", "--version"]:
            return {"ok": True, "stdout": "Version 4.20.0\n", "stderr": ""}
        if command[:3] == ["testparm", "-s", "--suppress-prompt"]:
            return {
                "ok": True,
                "stdout": "[global]\n\tfruit:aapl = no\n[legacy]\n\tvfs objects = recycle\n",
                "stderr": "",
            }
        if command[:2] == ["systemctl", "is-active"]:
            return {"ok": True, "stdout": "active\n", "stderr": ""}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    status = time_machine.system_status()

    assert status["ready"] is False
    assert status["samba"]["installed"] is True
    assert status["samba"]["version"] == "4.20.0"
    assert status["avahi"]["active"] is True
    assert status["managed_config"]["drift"] is True
    assert status["aapl_audit"]["ok"] is False
    assert "legacy" in status["aapl_audit"]["risky_shares"]


def test_system_status_reports_inactive_maintenance_timer(tm_env, monkeypatch):
    def fake_run(command, **kwargs):
        if command[:2] == ["smbd", "--version"]:
            return {"ok": True, "stdout": "Version 4.20.0\n", "stderr": ""}
        if command[:3] == ["testparm", "-s", "--suppress-prompt"]:
            return {"ok": True, "stdout": "[global]\n", "stderr": ""}
        if command[:2] == ["systemctl", "is-active"]:
            service = command[-1]
            active = service != "runvard-time-machine-maintenance.timer"
            return {"ok": active, "stdout": "active\n" if active else "inactive\n", "stderr": ""}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(time_machine, "_run", fake_run)

    status = time_machine.system_status()

    assert status["worker"] == {"timer_active": False}
    assert status["ready"] is False


def test_setup_guide_distinguishes_lan_bonjour_and_vpn_direct_url(tm_env):
    target = {
        "id": "7f5e3b08",
        "display_name": "Maria MacBook",
        "share_name": "tm-maria-7f5e3b08",
        "owner": "maria",
        "storage_root": str(tm_env["pool"]),
        "mount_source": "/dev/test-pool",
        "path": str(tm_env["pool"] / "runvard-time-machine" / "maria"),
        "advertised_bytes": 95 * 1024**3,
        "hard_limit_bytes": 100 * 1024**3,
        "enabled": True,
        "status": "waiting",
    }
    state = time_machine.load_state()
    state["targets"].append(target)
    time_machine.save_state(state)

    guide = time_machine.setup_guide(target["id"], hostname="backup.lan")

    assert guide["lan"]["url"] == "smb://backup.lan/tm-maria-7f5e3b08"
    assert guide["vpn"]["url"] == guide["lan"]["url"]
    assert guide["lan"]["bonjour"] is True
    assert guide["vpn"]["bonjour"] is False
    assert guide["client_encryption_required"] is True
    assert guide["restore_with_apple_tools"] is True


def test_target_limit_is_twenty(tm_env, monkeypatch):
    state = time_machine.load_state()
    state["targets"] = [{"id": f"{index:016x}"} for index in range(20)]
    time_machine.save_state(state)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: {"ok": True, "stdout": "", "stderr": ""},
    )

    with pytest.raises(ValueError, match="20"):
        time_machine.create_target(
            display_name="Mac 21",
            owner="maria",
            storage_root=str(tm_env["pool"]),
            capacity_gb=500,
            password="correct-horse-battery",
            client_encryption_required=True,
        )


def test_target_limit_is_checked_before_creating_backup_account(tm_env, monkeypatch):
    state = time_machine.load_state()
    state["targets"] = [{"id": f"{index:016x}"} for index in range(20)]
    time_machine.save_state(state)
    commands = []
    monkeypatch.setattr(time_machine, "_user_info", lambda name: None)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command)
        or {"ok": True, "stdout": "", "stderr": ""},
    )

    with pytest.raises(ValueError, match="20"):
        time_machine.create_target(
            display_name="Mac 21", owner="tm-new",
            storage_root=str(tm_env["pool"]), capacity_gb=500,
            password="correct-horse-battery", create_account=True,
            client_encryption_required=True,
        )

    assert not any(command and command[0] == "useradd" for command in commands)


def test_zfs_storage_is_validated_before_creating_backup_account(tm_env, monkeypatch):
    commands = []
    monkeypatch.setattr(
        time_machine,
        "_mount_info",
        lambda path: {
            "mountpoint": str(tm_env["pool"]),
            "source": "/dev/invalid-zfs-source",
            "fstype": "zfs",
            "options": "rw,relatime",
        },
    )
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

    with pytest.raises(ValueError, match="parent ZFS dataset"):
        time_machine.create_target(
            display_name="Invalid ZFS Mac", owner="tm-new",
            storage_root=str(tm_env["pool"]), capacity_gb=500,
            password="correct-horse-battery", create_account=True,
            client_encryption_required=True,
        )

    assert not any(command and command[0] == "useradd" for command in commands)


def test_retention_prunes_oldest_native_protection_point(tm_env, monkeypatch):
    commands = []
    target = {
        "id": "7f5e3b08",
        "backend": "zfs",
        "dataset": "tank/backups/runvard-timemachine/7f5e3b08",
        "protection_policy": {"daily": 2, "weekly": 1, "monthly": 1},
    }
    state = time_machine.load_state()
    state["targets"] = [target]
    state["protection_points"] = [
        {
            "id": f"{index:016x}", "target_id": target["id"], "kind": "daily",
            "backend": "zfs",
            "native_name": f"{target['dataset']}@daily-{index}",
            "created_at": index,
        }
        for index in (1, 2, 3)
    ]
    time_machine.save_state(state)
    monkeypatch.setattr(
        time_machine, "_run",
        lambda command, **kwargs: commands.append(command) or {
            "ok": True, "stdout": "", "stderr": "",
        },
    )

    result = time_machine.prune_protection_points(target["id"])

    assert result["deleted"] == 1
    assert ["zfs", "destroy", f"{target['dataset']}@daily-1"] in commands
    remaining = time_machine.list_protection_points(target["id"])
    assert [point["created_at"] for point in remaining] == [2, 3]
