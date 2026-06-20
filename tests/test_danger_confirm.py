from fastapi.testclient import TestClient

import server


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
