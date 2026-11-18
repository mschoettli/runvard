from fastapi.testclient import TestClient

import server


def _client(monkeypatch, role="admin"):
    monkeypatch.setattr(server, "_parse_token", lambda token: ("operator", role, True))
    return TestClient(server.app)


def test_workspace_update_status_requires_admin(monkeypatch):
    response = _client(monkeypatch, role="readonly").get(
        "/api/apps/workspace/update-status"
    )
    assert response.status_code == 403


def test_workspace_update_status_is_redacted(monkeypatch):
    monkeypatch.setattr(
        server.workspace_app,
        "status",
        lambda: {"state": "failed", "errorCode": "command-failed"},
    )
    response = _client(monkeypatch).get("/api/apps/workspace/update-status")
    assert response.status_code == 200
    assert response.json() == {"state": "failed", "errorCode": "command-failed"}


def test_workspace_health_requires_admin_and_is_redacted(monkeypatch):
    assert _client(monkeypatch, role="readonly").get("/api/apps/workspace/health").status_code == 403
    monkeypatch.setattr(server.workspace_app, "health", lambda: {"health": "healthy"})
    response = _client(monkeypatch).get("/api/apps/workspace/health")
    assert response.status_code == 200
    assert response.json() == {"health": "healthy"}


def test_workspace_install_and_update_require_confirmed_admin(monkeypatch):
    client = _client(monkeypatch, role="readonly")
    install = client.post(
        "/api/apps/install", data={"app_id": "workspace", "content": "ignored"}
    )
    update = client.post(
        "/api/apps/action", data={"app_id": "workspace", "action": "update"}
    )
    assert install.status_code == 403
    assert update.status_code == 403
