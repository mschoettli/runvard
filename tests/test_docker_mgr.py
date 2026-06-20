from types import SimpleNamespace

from modules import docker_mgr


def _container(name, labels=None):
    return SimpleNamespace(
        name=name,
        labels=labels or {},
        attrs={"Config": {"Labels": labels or {}}},
    )


def test_container_app_group_uses_compose_project_label():
    app = _container("papervard-app-1", {
        "com.docker.compose.project": "papervard",
        "com.docker.compose.service": "app",
    })
    db = _container("papervard-db-1", {
        "com.docker.compose.project": "papervard",
        "com.docker.compose.service": "db",
    })

    assert docker_mgr._container_app_group(app)["id"] == "compose:papervard"
    assert docker_mgr._container_app_group(db)["id"] == "compose:papervard"


def test_container_app_group_keeps_standalone_container_separate():
    jellyfin = _container("jellyfin")
    code_server = _container("code-server")

    assert docker_mgr._container_app_group(jellyfin)["id"] == "container:jellyfin"
    assert docker_mgr._container_app_group(code_server)["id"] == "container:code-server"


def test_check_compose_ports_reports_occupied_host_port(monkeypatch):
    monkeypatch.setattr(docker_mgr, "_compose_owned_ports", lambda name: set())
    monkeypatch.setattr(docker_mgr, "_port_is_available", lambda port: False)

    result = docker_mgr.check_compose_ports("demo", """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
""")

    assert result["ok"] is False
    assert result["conflicts"] == [{
        "service": "web",
        "port": 8080,
        "target": 80,
        "reason": "in_use",
    }]


def test_check_compose_ports_allows_existing_project_port(monkeypatch):
    monkeypatch.setattr(docker_mgr, "_compose_owned_ports", lambda name: {8080})
    monkeypatch.setattr(docker_mgr, "_port_is_available", lambda port: False)

    result = docker_mgr.check_compose_ports("demo", """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
""")

    assert result["ok"] is True
    assert result["conflicts"] == []


def test_check_compose_ports_reports_duplicate_yaml_ports(monkeypatch):
    monkeypatch.setattr(docker_mgr, "_compose_owned_ports", lambda name: set())
    monkeypatch.setattr(docker_mgr, "_port_is_available", lambda port: True)

    result = docker_mgr.check_compose_ports("demo", """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
  api:
    image: nginx
    ports:
      - "8080:8080"
""")

    assert result["ok"] is False
    assert [item["reason"] for item in result["conflicts"]] == ["duplicate", "duplicate"]
    assert [item["service"] for item in result["conflicts"]] == ["web", "api"]
