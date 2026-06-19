import json
from types import SimpleNamespace

import pytest
import requests

from modules import dashboard


@pytest.fixture(autouse=True)
def clear_health_cache():
    dashboard._custom_health_cache.clear()
    yield
    dashboard._custom_health_cache.clear()


@pytest.mark.parametrize("status_code", [200, 302, 401, 403, 404])
def test_custom_url_health_reachable_http_statuses(monkeypatch, status_code):
    monkeypatch.setattr(
        dashboard.requests,
        "head",
        lambda *args, **kwargs: SimpleNamespace(status_code=status_code),
    )

    health = dashboard._check_custom_url("http://example.test")

    assert health["state"] == "run"
    assert health["label"] == "Reachable"
    assert health["detail"] == f"HTTP {status_code}"


def test_custom_url_health_get_fallback_for_unsupported_head(monkeypatch):
    calls = []

    def fake_head(*args, **kwargs):
        calls.append("head")
        return SimpleNamespace(status_code=405)

    def fake_get(*args, **kwargs):
        calls.append("get")
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(dashboard.requests, "head", fake_head)
    monkeypatch.setattr(dashboard.requests, "get", fake_get)

    health = dashboard._check_custom_url("https://example.test")

    assert calls == ["head", "get"]
    assert health["state"] == "run"
    assert health["detail"] == "HTTP 200"


@pytest.mark.parametrize("status_code", [500, 503])
def test_custom_url_health_unreachable_for_server_errors(monkeypatch, status_code):
    monkeypatch.setattr(
        dashboard.requests,
        "head",
        lambda *args, **kwargs: SimpleNamespace(status_code=status_code),
    )

    health = dashboard._check_custom_url("http://example.test")

    assert health["state"] == "stop"
    assert health["label"] == "Unreachable"
    assert health["detail"] == f"HTTP {status_code}"


@pytest.mark.parametrize(
    "exc,label",
    [
        (requests.Timeout("slow"), "Timeout"),
        (requests.ConnectionError("refused"), "Unreachable"),
    ],
)
def test_custom_url_health_unreachable_for_request_errors(monkeypatch, exc, label):
    def fake_head(*args, **kwargs):
        raise exc

    monkeypatch.setattr(dashboard.requests, "head", fake_head)

    health = dashboard._check_custom_url("http://example.test")

    assert health["state"] == "stop"
    assert health["label"] == label


@pytest.mark.parametrize("url", ["", "example.test", "ftp://example.test"])
def test_custom_url_health_unknown_for_invalid_urls(url):
    health = dashboard._check_custom_url(url)

    assert health["state"] == "unknown"
    assert health["label"] == "Invalid URL"


def test_get_dashboard_adds_custom_health_and_keeps_app_status(monkeypatch, tmp_path):
    dash_file = tmp_path / "dashboard.json"
    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "demo"
    app_dir.mkdir(parents=True)
    (app_dir / "docker-compose.yml").write_text("services: {}\n")
    dash_file.write_text(json.dumps({
        "tiles": [
            {
                "id": "custom_1",
                "type": "custom",
                "name": "Custom",
                "url": "http://example.test",
            },
            {
                "id": "demo",
                "type": "app",
                "name": "Demo",
                "port": 8080,
            },
        ],
    }))

    monkeypatch.setattr(dashboard, "DASH_FILE", str(dash_file))
    monkeypatch.setattr(dashboard, "APPS_DIR", str(apps_dir))
    monkeypatch.setattr(dashboard, "_cached_custom_health", lambda url: {
        "state": "run",
        "label": "Reachable",
        "detail": "HTTP 200",
        "checked_at": 123,
    })
    monkeypatch.setattr(dashboard, "_compose_running", lambda path, service=None: True)

    result = dashboard.get_dashboard()

    custom = next(t for t in result["tiles"] if t["id"] == "custom_1")
    app = next(t for t in result["tiles"] if t["id"] == "demo")
    assert custom["health"]["state"] == "run"
    assert app["running"] is True
    assert app["installed"] is True
