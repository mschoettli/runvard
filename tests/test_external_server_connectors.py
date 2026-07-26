import json
from types import SimpleNamespace

from modules.external_servers.connectors import (
    ProxmoxConnector,
    WindowsConnector,
    WINDOWS_STATUS_SCRIPT,
    WINDOWS_UPDATES_SCRIPT,
    normalize_snapshot,
    parse_linux_sample,
    parse_windows_sample,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/api2/json/nodes"):
            return FakeResponse({"data": [{"node": "pve-a", "status": "online"}]})
        if url.endswith("/api2/json/nodes/pve-a/status"):
            return FakeResponse({"data": {
                "cpu": 0.125,
                "memory": {"used": 4, "total": 10},
            }})
        if "rrddata" in url:
            return FakeResponse({"data": [{"netin": 3200, "netout": 640}]})
        if url.endswith("/apt/update"):
            return FakeResponse({"data": [{}, {}, {}]})
        raise AssertionError(url)


def test_proxmox_connector_uses_token_header_and_normalizes_metrics():
    session = FakeSession()
    connector = ProxmoxConnector(session=session)
    config = {
        "status_url": "https://10.0.0.10:8006",
        "node": "",
        "verify_tls": True,
    }
    credentials = {
        "token_id": "root@pam!runvard",
        "token_secret": "secret",
    }

    snapshot = connector.collect(config, credentials)
    updates = connector.collect_updates(config, credentials)

    assert snapshot["cpu_percent"] == 12.5
    assert snapshot["ram_percent"] == 40.0
    assert snapshot["network_down_rate"] == 3200
    assert snapshot["network_up_rate"] == 640
    assert updates == 3
    assert all(
        call[1]["headers"]["Authorization"]
        == "PVEAPIToken=root@pam!runvard=secret"
        for call in session.calls
    )
    assert all(call[1]["allow_redirects"] is False for call in session.calls)


def test_linux_sample_parser_calculates_cpu_ram_and_network_rates():
    sample = "\n".join([
        "RV1",
        "cpu 100 0 50 850 0 0 0 0",
        "MemTotal: 1000 kB",
        "MemAvailable: 400 kB",
        "eth0: 1000 0 0 0 0 0 0 0 500 0 0 0 0 0 0 0",
        "RV2",
        "cpu 120 0 60 920 0 0 0 0",
        "eth0: 1400 0 0 0 0 0 0 0 700 0 0 0 0 0 0 0",
    ])

    result = parse_linux_sample(sample, elapsed=0.2)

    assert result["cpu_percent"] == 30.0
    assert result["ram_percent"] == 60.0
    assert result["network_down_rate"] == 2000.0
    assert result["network_up_rate"] == 1000.0


def test_windows_sample_parser_uses_structured_json_only():
    payload = json.dumps({
        "cpu_percent": 9.5,
        "ram_percent": 48.25,
        "network_down_rate": 1024,
        "network_up_rate": 512,
    })
    response = SimpleNamespace(
        status_code=0, std_out=payload.encode(), std_err=b"",
    )

    assert parse_windows_sample(response) == {
        "cpu_percent": 9.5,
        "ram_percent": 48.2,
        "network_down_rate": 1024.0,
        "network_up_rate": 512.0,
    }


def test_windows_connector_uses_https_winrm_options_and_fixed_scripts():
    calls = []

    class Session:
        def __init__(self, endpoint, **kwargs):
            calls.append(("session", endpoint, kwargs))

        def run_ps(self, script):
            calls.append(("script", script))
            if script == WINDOWS_UPDATES_SCRIPT:
                return SimpleNamespace(
                    status_code=0, std_out=b"6\n", std_err=b"",
                )
            return SimpleNamespace(
                status_code=0,
                std_out=json.dumps({
                    "cpu_percent": 4,
                    "ram_percent": 25,
                    "network_down_rate": 100,
                    "network_up_rate": 20,
                }).encode(),
                std_err=b"",
            )

    connector = WindowsConnector(session_factory=Session)
    config = {
        "status_url": "https://10.0.0.40:5986/wsman",
        "username": "monitor",
        "verify_tls": True,
    }
    credentials = {"password": "secret"}

    connector.collect(config, credentials)
    assert connector.collect_updates(config, credentials) == 6

    sessions = [call for call in calls if call[0] == "session"]
    assert all(call[1] == config["status_url"] for call in sessions)
    assert all(call[2]["auth"] == ("monitor", "secret") for call in sessions)
    assert all(call[2]["transport"] == "ntlm" for call in sessions)
    assert all(
        call[2]["server_cert_validation"] == "validate"
        for call in sessions
    )
    scripts = [call[1] for call in calls if call[0] == "script"]
    assert scripts == [WINDOWS_STATUS_SCRIPT, WINDOWS_UPDATES_SCRIPT]


def test_normalize_snapshot_uses_none_for_missing_values():
    snapshot = normalize_snapshot({"cpu_percent": None}, now=123)

    assert snapshot == {
        "captured_at": 123,
        "cpu_percent": None,
        "ram_percent": None,
        "network_down_rate": None,
        "network_up_rate": None,
        "updates": None,
    }
