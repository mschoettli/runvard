from fastapi.testclient import TestClient

import server
from modules import jobs


def _client(role="admin"):
    client = TestClient(server.app)
    token = server.make_token("tester", 3600, role)
    client.cookies.set(server.COOKIE_NAME, token)
    return client


def test_dangerous_action_rejects_admin_without_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    called = False

    def fake_format(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(server.storage, "format_partition", fake_format)

    response = _client().post(
        "/api/storage/format",
        data={"partition": "sdb1", "fstype": "ext4"},
    )

    assert response.status_code == 403
    assert called is False


def test_dangerous_action_accepts_matching_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.storage,
        "format_partition",
        lambda partition, fstype: {"ok": True, "partition": partition, "fstype": fstype},
    )
    client = _client()
    token = client.post(
        "/api/confirm-token",
        data={"action": "storage:format", "target": "sdb1"},
    ).json()["token"]

    response = client.post(
        "/api/storage/format",
        data={"partition": "sdb1", "fstype": "ext4", "confirm_token": token},
    )

    assert response.status_code == 200
    assert response.json()["partition"] == "sdb1"


def test_raid_create_requires_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    called = False

    def fake_create_raid(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(server.storage, "create_raid", fake_create_raid)

    response = _client().post(
        "/api/storage/raid/create",
        data={"name": "md0", "level": "1", "devices": "sdb,sdc"},
    )

    assert response.status_code == 403
    assert called is False


def test_raid_create_accepts_matching_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.storage,
        "create_raid",
        lambda name, level, devices: {"ok": True, "name": name, "level": level, "devices": devices},
    )
    client = _client()
    token = client.post(
        "/api/confirm-token",
        data={"action": "storage:raid-create", "target": "md0"},
    ).json()["token"]

    response = client.post(
        "/api/storage/raid/create",
        data={"name": "md0", "level": "1", "devices": "sdb,sdc", "confirm_token": token},
    )

    assert response.status_code == 200
    assert response.json()["devices"] == ["sdb", "sdc"]


def test_confirm_token_is_single_use(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.storage, "format_partition", lambda *args: {"ok": True})
    client = _client()
    token = client.post(
        "/api/confirm-token",
        data={"action": "storage:format", "target": "sdb1"},
    ).json()["token"]
    payload = {"partition": "sdb1", "fstype": "ext4", "confirm_token": token}

    assert client.post("/api/storage/format", data=payload).status_code == 200
    assert client.post("/api/storage/format", data=payload).status_code == 403


def test_readonly_user_cannot_issue_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client("readonly").post(
        "/api/confirm-token",
        data={"action": "storage:format", "target": "sdb1"},
    )

    assert response.status_code == 403


def test_non_dangerous_docker_action_does_not_require_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.docker_mgr,
        "container_action",
        lambda container_id, action: {"ok": True, "container_id": container_id, "action": action},
    )

    response = _client().post(
        "/api/docker/action",
        data={"container_id": "abc123", "action": "restart"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "restart"


def test_account_add_does_not_require_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.accounts, "add_user", lambda username, password, role: {"ok": True})

    response = _client().post(
        "/api/accounts/add",
        data={"username": "new-user", "password": "secret", "role": "readonly"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_file_delete_job_requires_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.files, "start_job", lambda *args: {"id": "job-1"})

    response = _client().post(
        "/api/files/job",
        data={"action": "delete", "paths": "/tmp/demo.txt"},
    )

    assert response.status_code == 403


def test_file_copy_job_does_not_require_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.files, "start_job", lambda *args: {"id": "job-1"})

    response = _client().post(
        "/api/files/job",
        data={"action": "copy", "paths": "/tmp/demo.txt", "dst_dir": "/tmp/copy"},
    )

    assert response.status_code == 200
    assert response.json() == {"id": "job-1"}


def test_dashboard_remove_requires_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.dashboard, "remove_tile", lambda tile_id: {"ok": True})

    response = _client().post("/api/dashboard/remove", data={"tile_id": "demo"})

    assert response.status_code == 403


def test_docker_compose_save_does_not_require_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.security_tokens,
        "require_confirm_token",
        lambda *args: (_ for _ in ()).throw(RuntimeError("confirm backend failed")),
    )
    monkeypatch.setattr(
        server.docker_mgr,
        "check_compose_ports",
        lambda name, content: {"ok": True},
    )
    monkeypatch.setattr(
        server.docker_mgr,
        "save_compose",
        lambda name, content, env_enabled=False, env_content="": {
            "ok": True,
            "name": name,
            "env_enabled": env_enabled,
        },
    )

    response = _client().post(
        "/api/docker/compose/save",
        data={"name": "demo", "content": "services: {}\n", "env_enabled": "1"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "name": "demo", "env_enabled": True}


def test_standard_admin_cannot_call_expert_only_endpoint(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client().get("/api/storage/luks")

    assert response.status_code == 403
    assert response.json()["detail"] == "Expert mode required"


def test_admin_can_enable_expert_mode_for_session(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.storage, "luks_list", lambda: {"devices": []})
    client = _client()

    enabled = client.post("/api/expert-mode", data={"enabled": "1"})
    response = client.get("/api/storage/luks")

    assert enabled.status_code == 200
    assert enabled.json()["expert"] is True
    assert response.status_code == 200
    assert response.json() == {"devices": []}


def test_readonly_user_cannot_enable_expert_mode(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client("readonly").post("/api/expert-mode", data={"enabled": "1"})

    assert response.status_code == 403


def test_runvard_update_start_error_returns_json(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.system_mgr,
        "start_runvard_update",
        lambda: (_ for _ in ()).throw(RuntimeError("systemd-run failed")),
    )
    client = _client()

    response = client.post("/api/sysmgr/runvard-update/apply")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "systemd-run failed"}


def test_runvard_update_does_not_require_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.security_tokens,
        "require_confirm_token",
        lambda *args: (_ for _ in ()).throw(RuntimeError("confirm backend failed")),
    )
    monkeypatch.setattr(
        server.system_mgr,
        "start_runvard_update",
        lambda: {"ok": True, "started": True},
    )
    client = _client()

    response = client.post("/api/sysmgr/runvard-update/apply")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "started": True}


def test_package_update_does_not_require_confirm_token(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.security_tokens,
        "require_confirm_token",
        lambda *args: (_ for _ in ()).throw(RuntimeError("confirm backend failed")),
    )
    monkeypatch.setattr(
        jobs,
        "start_job",
        lambda name, func: {"ok": True, "job_id": "job-1", "name": name},
    )

    response = _client().post("/api/sysmgr/updates/apply")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "job_id": "job-1", "name": "apt-upgrade"}
