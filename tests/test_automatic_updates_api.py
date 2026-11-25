import pytest
from fastapi.testclient import TestClient

import server


def client(role="admin"):
    result = TestClient(server.app)
    result.cookies.set(server.COOKIE_NAME, server.make_token("tester", 3600, role, account_salt=None))
    return result


@pytest.mark.parametrize("role", ["admin", "readonly"])
def test_status_is_available_without_expert_mode(monkeypatch, role):
    status = {"installed": True, "enabled": True, "auto_reboot": False}
    monkeypatch.setattr(server.system_mgr, "unattended_status", lambda: status)
    response = client(role).get("/api/sysmgr/unattended")
    assert response.status_code == 200
    assert response.json() == status


@pytest.mark.parametrize("role,confirmed,expected", [
    ("admin", True, 200), ("admin", False, 403),
    ("operator", True, 401), ("readonly", True, 403),
])
def test_settings_require_confirmed_admin_without_expert_mode(monkeypatch, role, confirmed, expected):
    calls = []
    monkeypatch.setattr(server.system_mgr, "unattended_set",
                        lambda *args: calls.append(args) or {"ok": True})
    token = server.security_tokens.issue_confirm_token(
        "tester", "sysmgr:unattended", "unattended-upgrades",
    )["token"] if confirmed else ""
    response = client(role).post("/api/sysmgr/unattended/set", data={
        "enable": "true", "auto_reboot": "false", "reboot_time": "02:00",
        "confirm_token": token,
    })
    assert response.status_code == expected
    assert calls == ([(True, False, "02:00")] if expected == 200 else [])


def test_status_requires_login():
    assert TestClient(server.app).get("/api/sysmgr/unattended").status_code == 401


def test_unrecognized_session_role_cannot_read_settings():
    assert client("operator").get("/api/sysmgr/unattended").status_code == 401
