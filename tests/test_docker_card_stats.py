from pathlib import Path

from fastapi.testclient import TestClient

import server
from modules import docker_mgr


def test_docker_stats_all_endpoint_returns_container_map(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: False)
    monkeypatch.setattr(
        docker_mgr,
        "list_container_stats",
        lambda: {
            "web123": {
                "cpu_percent": 12.4,
                "cpu_count": 2,
                "mem_used": 428_000_000,
                "mem_limit": 1_000_000_000,
                "mem_percent": 42.8,
                "net_rx": 1200,
                "net_tx": 800,
            },
        },
    )

    response = TestClient(server.app).get("/api/docker/stats/all")

    assert response.status_code == 200
    assert response.json()["web123"]["cpu_percent"] == 12.4
    assert response.json()["web123"]["cpu_count"] == 2


def test_modern_docker_cards_include_live_resource_visuals():
    html = Path("static/index.html").read_text()
    css = Path("static/modern-theme.css").read_text()

    assert 'class="docker-service-resources"' in html
    assert "api('/docker/stats/all')" in html
    assert "dockerStatsSparkline" in html
    assert "docker-card-stats-value" in html
    assert "docker-card-stats-note" in html
    assert "docker-service-resources" in css
    assert "docker-resource-sparkline" in css
