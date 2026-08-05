import json
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from modules import accounts, security_tokens


def _server_client(monkeypatch, tmp_path):
    import server
    monkeypatch.setenv("RUNVARD_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server.accounts, "STORE", str(tmp_path / "users.json"))
    server.accounts.add_user("admin-a", "root password", "admin")
    server.accounts.add_user("viewer", "view password", "readonly")
    monkeypatch.setattr(server, "SECRET_FILE", str(tmp_path / "secret.key"))
    return server, TestClient(server.app)


def _login(client, username, password):
    return client.post("/api/login", data={"username": username, "password": password})


def test_bootstrap_rejects_missing_credentials_for_empty_store(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "STORE", str(tmp_path / "users.json"))
    monkeypatch.delenv("RUNVARD_USER", raising=False)
    monkeypatch.delenv("RUNVARD_PASS", raising=False)

    with pytest.raises(RuntimeError, match="no administrator account"):
        accounts.ensure_bootstrap_account()


@pytest.mark.parametrize(
    ("username", "password"),
    [("admin", None), (None, "secret"), ("admin", ""), ("", "secret")],
)
def test_bootstrap_rejects_partial_or_empty_legacy_credentials(
    monkeypatch, tmp_path, username, password
):
    monkeypatch.setattr(accounts, "STORE", str(tmp_path / "users.json"))
    monkeypatch.delenv("RUNVARD_USER", raising=False)
    monkeypatch.delenv("RUNVARD_PASS", raising=False)
    if username is not None:
        monkeypatch.setenv("RUNVARD_USER", username)
    if password is not None:
        monkeypatch.setenv("RUNVARD_PASS", password)

    with pytest.raises(RuntimeError, match="both be non-empty"):
        accounts.ensure_bootstrap_account()


def test_bootstrap_migrates_explicit_credentials_to_restricted_hash_store(
    monkeypatch, tmp_path
):
    store = tmp_path / "users.json"
    monkeypatch.setattr(accounts, "STORE", str(store))
    monkeypatch.setenv("RUNVARD_USER", "operator")
    monkeypatch.setenv("RUNVARD_PASS", "correct horse battery staple")

    assert accounts.ensure_bootstrap_account() == "operator"
    assert accounts.verify("operator", "correct horse battery staple") == "admin"
    assert "correct horse battery staple" not in store.read_text()
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


def test_bootstrap_rejects_known_legacy_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "STORE", str(tmp_path / "users.json"))
    monkeypatch.setenv("RUNVARD_USER", "admin")
    monkeypatch.setenv("RUNVARD_PASS", "runvard")
    with pytest.raises(RuntimeError, match="insecure legacy"):
        accounts.ensure_bootstrap_account()


def test_existing_account_needs_no_environment_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "STORE", str(tmp_path / "users.json"))
    assert accounts.add_user("existing", "safe password", "admin")["ok"]
    monkeypatch.delenv("RUNVARD_USER", raising=False)
    monkeypatch.delenv("RUNVARD_PASS", raising=False)

    assert accounts.ensure_bootstrap_account() == "existing"


def test_disabled_auth_config_is_migrated_atomically(monkeypatch, tmp_path, caplog):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"login_enabled": False}))
    os.chmod(path, 0o644)

    from modules import auth_config

    assert auth_config.enforce_authentication(str(path)) is True
    assert json.loads(path.read_text()) == {"login_enabled": True}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "disabled authentication" in caplog.text.lower()


def test_terminal_token_is_bound_single_use_and_expires(monkeypatch):
    security_tokens.clear_terminal_tokens()
    token = security_tokens.issue_terminal_token("alice")["token"]
    security_tokens.consume_terminal_token(token, "alice")
    with pytest.raises(PermissionError):
        security_tokens.consume_terminal_token(token, "alice")

    other = security_tokens.issue_terminal_token("alice")["token"]
    with pytest.raises(PermissionError, match="does not match"):
        security_tokens.consume_terminal_token(other, "bob")

    expired = security_tokens.issue_terminal_token("alice")["token"]
    monkeypatch.setattr(time, "time", lambda: float("inf"))
    with pytest.raises(PermissionError):
        security_tokens.consume_terminal_token(expired, "alice")


def test_unauthenticated_api_and_removed_toggle_are_denied(monkeypatch, tmp_path):
    server, client = _server_client(monkeypatch, tmp_path)
    assert client.get("/api/accounts").status_code == 401
    assert client.post("/api/auth/toggle", data={"enabled": "0"}).status_code == 404
    status = client.get("/api/auth/status").json()
    assert status == {"login_enabled": True, "user": None, "role": None, "expert": False}


def test_proxy_https_header_is_used_only_for_explicitly_trusted_proxy(monkeypatch):
    import server
    class Client:
        host = "10.0.0.2"
    class URL:
        scheme = "http"
    class Request:
        client = Client()
        url = URL()
        headers = {"x-forwarded-proto": "https"}
    monkeypatch.delenv("RUNVARD_TRUSTED_PROXIES", raising=False)
    assert server._is_https(Request()) is False
    monkeypatch.setenv("RUNVARD_TRUSTED_PROXIES", "10.0.0.0/24")
    assert server._is_https(Request()) is True


def test_terminal_step_up_rejects_readonly_and_wrong_password(monkeypatch, tmp_path):
    _, client = _server_client(monkeypatch, tmp_path)
    assert _login(client, "viewer", "view password").status_code == 200
    assert client.post("/api/terminal/authorize", data={"password": "view password"}).status_code == 403
    assert _login(client, "admin-a", "root password").status_code == 200
    assert client.post("/api/terminal/authorize", data={"password": "wrong"}).status_code == 401


def test_terminal_step_up_grant_is_bound_and_single_use(monkeypatch, tmp_path):
    server, client = _server_client(monkeypatch, tmp_path)
    assert _login(client, "admin-a", "root password").status_code == 200
    response = client.post("/api/terminal/authorize", data={"password": "root password"})
    assert response.status_code == 200
    token = response.json()["token"]
    assert security_tokens.consume_terminal_token(token, "admin-a") == "admin-a"
    with pytest.raises(PermissionError):
        security_tokens.consume_terminal_token(token, "admin-a")


def test_terminal_websocket_requires_login_and_step_up(monkeypatch, tmp_path):
    _, client = _server_client(monkeypatch, tmp_path)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/terminal"):
            pass

    _login(client, "admin-a", "root password")
    with client.websocket_connect("/ws/terminal") as websocket:
        websocket.send_json({"type": "authorize", "token": "invalid"})
        with pytest.raises(Exception):
            websocket.receive_text()


def test_installer_hashes_admin_and_never_writes_password_to_env():
    script = (os.path.dirname(__file__) + "/../scripts/install-full.sh")
    text = open(script, encoding="utf-8").read()
    env_block = text.split('cat > "$ENV_FILE" <<EOF', 1)[1].split("EOF", 1)[0]
    assert "RUNVARD_PASS" not in env_block
    assert "RUNVARD_USER" not in env_block
    assert "accounts.add_user" in text
    assert 'chmod 600 "$ENV_FILE"' in text
