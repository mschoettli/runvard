import json
import stat

from modules.external_servers.storage import ExternalServerStore, SecretStore


def test_secret_store_encrypts_credentials_and_removes_them(tmp_path):
    store = SecretStore(tmp_path)
    credentials = {
        "username": "monitor",
        "password": "not-plain-text",
        "private_key": "private-key-material",
    }

    store.set("srv-1", credentials)

    assert store.get("srv-1") == credentials
    raw = (tmp_path / "secrets.json").read_text()
    assert "not-plain-text" not in raw
    assert "private-key-material" not in raw
    assert stat.S_IMODE((tmp_path / "secret.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600

    store.delete("srv-1")

    assert store.get("srv-1") == {}


def test_external_server_store_never_persists_credentials_in_registry(tmp_path):
    store = ExternalServerStore(tmp_path)
    state = store.load()
    state["servers"]["srv-1"] = {
        "server_id": "srv-1",
        "name": "Build server",
        "kind": "linux",
        "admin_url": "https://build.example.test",
    }

    store.save(state)

    persisted = json.loads((tmp_path / "servers.json").read_text())
    assert persisted["servers"]["srv-1"]["name"] == "Build server"
    assert "credentials" not in persisted["servers"]["srv-1"]
    assert stat.S_IMODE((tmp_path / "servers.json").stat().st_mode) == 0o600
