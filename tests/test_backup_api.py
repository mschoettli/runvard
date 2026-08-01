from fastapi.testclient import TestClient

import server


def _client(role="admin"):
    client = TestClient(server.app)
    client.cookies.set(
        server.COOKIE_NAME, server.make_token("tester", 3600, role),
    )
    return client


def test_backup_locations_are_available_to_authenticated_users(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.backup,
        "discover_locations",
        lambda: {"sources": [], "destinations": []},
        raising=False,
    )

    response = _client("readonly").get("/api/backup/locations")

    assert response.status_code == 200
    assert response.json() == {"sources": [], "destinations": []}


def test_backup_browse_only_returns_directories(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.backup,
        "browse_directories",
        lambda path: {"path": path, "parent": "/", "entries": []},
        raising=False,
    )

    response = _client("readonly").get("/api/backup/browse", params={"path": "/srv"})

    assert response.status_code == 200
    assert response.json()["path"] == "/srv"


def test_backup_validation_is_available_before_creation(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.backup,
        "validate_paths",
        lambda source, dest: {"ok": True, "source": source, "dest": dest},
        raising=False,
    )

    response = _client().post(
        "/api/backup/validate", data={"source": "/srv/data", "dest": "/mnt/backups/data"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_backup_mutations_require_an_admin(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(server.backup, "add_job", lambda *args, **kwargs: {"ok": True})

    response = _client("operator").post(
        "/api/backup/add",
        data={"name": "Photos", "source": "/srv/photos", "dest": "/mnt/backups/photos"},
    )

    assert response.status_code == 403
