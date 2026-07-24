from fastapi.testclient import TestClient

import server
from modules.federation.crypto import create_or_load_identity
from modules.federation.membership import apply_event, create_event
from modules.federation.service import FederationManager


def _snapshot():
    return {
        "api_version": 1,
        "version": "test",
        "cpu_percent": 1,
        "ram_percent": 2,
        "disk_percent": 3,
        "docker": {"running": 0, "total": 0, "available": False},
        "vms": {"running": 0, "total": 0, "available": False},
        "updates": 0,
        "alerts": 0,
    }


def _client(role="admin"):
    client = TestClient(server.app)
    client.cookies.set(
        server.COOKIE_NAME, server.make_token("tester", 3600, role),
    )
    return client


def _confirm(client, action, target):
    return client.post(
        "/api/confirm-token", data={"action": action, "target": target},
    ).json()["token"]


def test_overview_is_stable_when_disabled_and_readonly_can_view(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "FEDERATION", FederationManager(tmp_path, _snapshot))
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client("readonly").get("/api/federation/v1/admin/overview")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False, "nodes": [], "online": 0, "total": 0,
    }


def test_enable_requires_admin_and_danger_confirmation(tmp_path, monkeypatch):
    manager = FederationManager(tmp_path, _snapshot)
    monkeypatch.setattr(server, "FEDERATION", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    data = {
        "name": "Node A",
        "internal_url": "http://127.0.0.1:8101",
        "browser_url": "https://a.example.test",
        "allowed_cidrs": "127.0.0.0/8",
    }

    assert _client("readonly").post(
        "/api/federation/v1/admin/enable", data=data,
    ).status_code == 403
    admin = _client()
    assert admin.post(
        "/api/federation/v1/admin/enable", data=data,
    ).status_code == 403
    data["confirm_token"] = _confirm(
        admin, "federation:enable", data["internal_url"],
    )

    response = admin.post("/api/federation/v1/admin/enable", data=data)

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["total"] == 1


def test_sso_start_returns_no_store_post_bridge_without_url_ticket(
    tmp_path, monkeypatch,
):
    manager = FederationManager(tmp_path / "a", _snapshot)
    manager.enable(
        "Node A", "http://127.0.0.1:8101", "https://a.example.test",
        ["127.0.0.0/8"],
    )
    peer = create_or_load_identity(tmp_path / "b")
    peer_node = {
        "node_id": peer.node_id,
        "public_key": peer.public_key,
        "name": "Node B",
        "hostname": "b",
        "internal_url": "http://127.0.0.1:8102",
        "browser_url": "https://b.example.test",
        "api_version": 1,
        "runvard_version": "test",
    }
    apply_event(
        manager.state,
        create_event(
            manager.identity, manager.state["federation_id"],
            "node_joined", peer_node,
        ),
    )
    manager.state["peer_status"][peer.node_id] = {
        "health": "online", "last_success": 100, "snapshot": _snapshot(),
    }
    manager._save()
    monkeypatch.setattr(server, "FEDERATION", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client().post(
        "/api/federation/v1/sso/start", data={"node_id": peer.node_id},
    )

    assert response.status_code == 200
    assert "https://b.example.test/api/federation/v1/sso/accept" in response.text
    assert 'name="ticket"' in response.text
    assert "location" not in response.headers
    assert response.headers["cache-control"].startswith("no-store")
    assert "form-action https://b.example.test" in \
        response.headers["content-security-policy"]
