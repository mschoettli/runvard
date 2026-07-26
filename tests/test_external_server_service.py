import copy

import pytest

from modules.external_servers.service import ExternalServerManager


class FakeConnector:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def collect(self, config, credentials):
        self.calls.append((copy.deepcopy(config), copy.deepcopy(credentials)))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def collect_updates(self, config, credentials):
        return 4


def linux_config():
    return {
        "name": "Linux node",
        "kind": "linux",
        "host": "10.0.0.21",
        "port": 22,
        "username": "monitor",
        "host_key": "SHA256:test-fingerprint",
        "admin_url": "https://linux.example.test",
        "verify_tls": True,
        "enabled": True,
    }


def test_create_and_overview_keep_credentials_server_side(tmp_path):
    connector = FakeConnector([{
        "cpu_percent": 12.5,
        "ram_percent": 34.5,
        "network_down_rate": 1250,
        "network_up_rate": 250,
    }])
    manager = ExternalServerManager(
        tmp_path, connector_factory=lambda kind: connector, now=lambda: 1000,
    )

    created = manager.create(
        linux_config(), {"private_key": "secret-key", "passphrase": ""},
    )
    manager.refresh([created["server_id"]], force_updates=True)
    overview = manager.overview()

    assert created["name"] == "Linux node"
    assert created["has_credentials"] is True
    assert "private_key" not in created
    assert overview["total"] == 1
    assert overview["online"] == 1
    node = overview["nodes"][0]
    assert node["node_id"] == f"external:{created['server_id']}"
    assert node["external"] is True
    assert node["browser_url"] == "https://linux.example.test"
    assert node["snapshot"]["updates"] == 4
    assert connector.calls[0][1]["private_key"] == "secret-key"
    assert "secret-key" not in (tmp_path / "servers.json").read_text()


def test_three_failures_mark_server_offline_and_preserve_last_snapshot(tmp_path):
    connector = FakeConnector([
        {
            "cpu_percent": 10,
            "ram_percent": 20,
            "network_down_rate": 30,
            "network_up_rate": 40,
        },
        TimeoutError("first"),
        TimeoutError("second"),
        TimeoutError("third"),
    ])
    manager = ExternalServerManager(
        tmp_path, connector_factory=lambda kind: connector, now=lambda: 1000,
    )
    server_id = manager.create(
        linux_config(), {"private_key": "key"},
    )["server_id"]

    manager.refresh([server_id])
    manager.refresh([server_id])
    assert manager.overview()["nodes"][0]["health"] == "degraded"
    manager.refresh([server_id])
    manager.refresh([server_id])

    node = manager.overview()["nodes"][0]
    assert node["health"] == "offline"
    assert node["snapshot"]["cpu_percent"] == 10
    assert "error" not in node
    assert manager.admin_list()[0]["status"]["error"] == "timeout"


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"host": "example.test"}, "literal private IP"),
        ({"host": "127.0.0.1"}, "allowed private networks"),
        ({"admin_url": "javascript:alert(1)"}, "HTTP"),
        ({"kind": "router"}, "unsupported server type"),
    ],
)
def test_create_rejects_unsafe_or_unsupported_configuration(
    tmp_path, changes, match,
):
    config = linux_config()
    config.update(changes)
    manager = ExternalServerManager(tmp_path)

    with pytest.raises(ValueError, match=match):
        manager.create(config, {"private_key": "key"})


def test_deleting_server_also_deletes_its_secret(tmp_path):
    manager = ExternalServerManager(tmp_path)
    server_id = manager.create(
        linux_config(), {"private_key": "secret-key"},
    )["server_id"]

    manager.delete(server_id)

    assert manager.admin_list() == []
    assert manager.secrets.get(server_id) == {}


def test_overview_hides_diagnostic_errors_from_readonly_payload(tmp_path):
    connector = FakeConnector([TimeoutError("private remote detail")])
    manager = ExternalServerManager(
        tmp_path, connector_factory=lambda kind: connector, now=lambda: 1000,
    )
    server_id = manager.create(
        linux_config(), {"private_key": "key"},
    )["server_id"]

    manager.refresh([server_id])

    assert "error" not in manager.overview()["nodes"][0]
    admin = manager.admin_list()[0]
    assert admin["status"]["error"] == "timeout"


def test_changing_to_generic_removes_obsolete_credentials(tmp_path):
    manager = ExternalServerManager(tmp_path)
    server_id = manager.create(
        linux_config(), {"private_key": "secret-key"},
    )["server_id"]
    generic = {
        "name": "Appliance",
        "kind": "generic",
        "admin_url": "https://appliance.example.test",
        "status_url": "https://10.0.0.50:8443/health",
        "verify_tls": True,
        "enabled": True,
    }

    manager.update(server_id, generic)

    assert manager.secrets.get(server_id) == {}


def test_connection_test_can_reuse_saved_credentials_when_editing(tmp_path):
    connector = FakeConnector([{
        "cpu_percent": 1,
        "ram_percent": 2,
        "network_down_rate": 3,
        "network_up_rate": 4,
    }])
    manager = ExternalServerManager(
        tmp_path, connector_factory=lambda kind: connector, now=lambda: 1000,
    )
    server_id = manager.create(
        linux_config(), {"private_key": "saved-key"},
    )["server_id"]

    result = manager.test_connection(
        linux_config(), {}, server_id=server_id,
    )

    assert result["ok"] is True
    assert connector.calls[0][1]["private_key"] == "saved-key"


def test_disabled_server_is_kept_in_admin_list_but_hidden_from_overview(tmp_path):
    manager = ExternalServerManager(tmp_path)
    server_id = manager.create(
        linux_config(), {"private_key": "key"},
    )["server_id"]

    manager.set_enabled(server_id, False)

    assert manager.overview()["nodes"] == []
    assert manager.admin_list()[0]["enabled"] is False
