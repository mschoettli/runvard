from fastapi.testclient import TestClient

import server
from modules.external_servers.service import ExternalServerManager


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


def generic_data():
    return {
        "name": "Router",
        "kind": "generic",
        "admin_url": "https://router.example.test",
        "status_url": "https://10.0.0.30:8443/health",
        "verify_tls": "1",
        "enabled": "1",
    }


def test_admin_can_create_external_server_without_exposing_secrets(
    tmp_path, monkeypatch,
):
    manager = ExternalServerManager(tmp_path)
    monkeypatch.setattr(server, "EXTERNAL_SERVERS", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    data = {
        "name": "Proxmox",
        "kind": "proxmox",
        "admin_url": "https://pve.example.test",
        "status_url": "https://10.0.0.10:8006",
        "node": "pve-a",
        "token_id": "root@pam!runvard",
        "token_secret": "super-secret",
        "verify_tls": "1",
        "enabled": "1",
    }

    response = _client().post("/api/external-servers/v1/admin/create", data=data)

    assert response.status_code == 200
    assert response.json()["kind"] == "proxmox"
    assert response.json()["has_credentials"] is True
    assert "token_secret" not in response.text
    assert "super-secret" not in (tmp_path / "servers.json").read_text()


def test_readonly_can_view_overview_but_not_admin_configuration(
    tmp_path, monkeypatch,
):
    manager = ExternalServerManager(tmp_path)
    manager.create(generic_data())
    monkeypatch.setattr(server, "EXTERNAL_SERVERS", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    client = _client("readonly")

    overview = client.get("/api/external-servers/v1/overview")
    admin_list = client.get("/api/external-servers/v1/admin/list")
    create = client.post(
        "/api/external-servers/v1/admin/create", data=generic_data(),
    )

    assert overview.status_code == 200
    assert overview.json()["nodes"][0]["name"] == "Router"
    assert "status_url" not in overview.text
    assert admin_list.status_code == 403
    assert create.status_code == 403


def test_delete_requires_confirmation_and_removes_secret(
    tmp_path, monkeypatch,
):
    manager = ExternalServerManager(tmp_path)
    server_id = manager.create({
        "name": "Windows",
        "kind": "windows",
        "admin_url": "https://windows.example.test",
        "status_url": "https://10.0.0.40:5986/wsman",
        "username": "monitor",
        "verify_tls": True,
        "enabled": True,
    }, {"password": "secret"})["server_id"]
    monkeypatch.setattr(server, "EXTERNAL_SERVERS", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    client = _client()

    denied = client.post(
        "/api/external-servers/v1/admin/delete",
        data={"server_id": server_id},
    )
    token = _confirm(client, "external-server:delete", server_id)
    deleted = client.post(
        "/api/external-servers/v1/admin/delete",
        data={"server_id": server_id, "confirm_token": token},
    )

    assert denied.status_code == 403
    assert deleted.status_code == 200
    assert manager.admin_list() == []
    assert manager.secrets.get(server_id) == {}


def test_update_retains_existing_credentials_when_secret_fields_are_blank(
    tmp_path, monkeypatch,
):
    manager = ExternalServerManager(tmp_path)
    created = manager.create({
        "name": "Linux",
        "kind": "linux",
        "host": "10.0.0.21",
        "port": 22,
        "username": "monitor",
        "host_key": "SHA256:test",
        "admin_url": "https://linux.example.test",
        "verify_tls": True,
        "enabled": True,
    }, {"private_key": "private-key"})
    monkeypatch.setattr(server, "EXTERNAL_SERVERS", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    data = {
        "server_id": created["server_id"],
        "name": "Linux renamed",
        "kind": "linux",
        "host": "10.0.0.21",
        "port": "22",
        "username": "monitor",
        "host_key": "SHA256:test",
        "admin_url": "https://linux.example.test",
        "private_key": "",
        "passphrase": "",
        "verify_tls": "1",
        "enabled": "1",
    }

    response = _client().post(
        "/api/external-servers/v1/admin/update", data=data,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Linux renamed"
    assert manager.secrets.get(created["server_id"]) == {
        "private_key": "private-key",
        "passphrase": "",
    }


def test_edit_connection_test_reuses_saved_secret(tmp_path, monkeypatch):
    class Connector:
        def collect(self, config, credentials):
            assert credentials["private_key"] == "saved-key"
            return {"cpu_percent": 1}

        def collect_updates(self, config, credentials):
            return 0

    manager = ExternalServerManager(
        tmp_path, connector_factory=lambda kind: Connector(),
    )
    created = manager.create({
        "name": "Linux",
        "kind": "linux",
        "host": "10.0.0.21",
        "port": 22,
        "username": "monitor",
        "host_key": "SHA256:test",
        "admin_url": "https://linux.example.test",
        "verify_tls": True,
        "enabled": True,
    }, {"private_key": "saved-key"})
    monkeypatch.setattr(server, "EXTERNAL_SERVERS", manager)
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client().post("/api/external-servers/v1/admin/test", data={
        "server_id": created["server_id"],
        "name": "Linux",
        "kind": "linux",
        "host": "10.0.0.21",
        "port": "22",
        "username": "monitor",
        "host_key": "SHA256:test",
        "admin_url": "https://linux.example.test",
        "private_key": "",
        "verify_tls": "1",
        "enabled": "1",
    })

    assert response.status_code == 200
    assert response.json()["ok"] is True
