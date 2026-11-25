"""Management endpoints must win over proxy traffic ports."""
import pytest
import yaml

from modules import apps, compose_utils, dashboard, ports


def zoraxy_compose(name="zoraxy", management=8000):
    return yaml.safe_dump({"services": {name: {
        "image": "zoraxydocker/zoraxy:latest",
        "environment": {"PORT": str(management)},
        "ports": ["8080:80", "8443:443", f"18000:{management}"],
    }}})


@pytest.mark.parametrize("name,management", [("zoraxy", 8000), ("gateway", 8000), ("gateway", 8005)])
def test_zoraxy_management_port_wins_over_proxy_ports(name, management):
    content = zoraxy_compose(name, management)
    assert compose_utils.best_web_port_from_compose(content) == 18000
    assert compose_utils.best_web_endpoint_from_compose(content) == (18000, "http")


def test_ports_overview_includes_zoraxy_management_with_renamed_service():
    rows = ports.parse_compose_ports(zoraxy_compose("gateway", 8005), "gateway", "Compose", ["192.0.2.10"])
    assert {row["port"] for row in rows} == {8080, 8443, 18000}


@pytest.mark.parametrize("kind", ["app", "compose"])
def test_existing_dashboard_tile_resolves_zoraxy_management(monkeypatch, tmp_path, kind):
    project = tmp_path / "zoraxy"
    project.mkdir()
    (project / "docker-compose.yml").write_text(zoraxy_compose())
    monkeypatch.setattr(dashboard, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard, "COMPOSE_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard, "_compose_running", lambda *args: True)
    monkeypatch.setattr(dashboard, "_load", lambda: {"tiles": [
        {"id": "compose:zoraxy" if kind == "compose" else "zoraxy", "type": kind, "port": 8080},
    ]})
    assert dashboard.get_dashboard()["tiles"][0]["port"] == 18000


def test_docker_tile_prefers_zoraxy_management(monkeypatch):
    monkeypatch.setattr(dashboard, "_load", lambda: {"tiles": [
        {"id": "compose:gateway", "type": "docker", "port": 8080},
    ]})
    monkeypatch.setattr(dashboard.docker_mgr, "list_containers", lambda: [{
        "name": "gateway", "image": "zoraxydocker/zoraxy:latest", "state": "running",
        "ports": ["8080:80/tcp", "8443:443/tcp", "18000:8000/tcp"],
        "app_group": {"id": "compose:gateway"},
    }])
    assert dashboard.get_dashboard()["tiles"][0]["port"] == 18000


def test_udp_port_cannot_be_selected_as_web_endpoint():
    content = yaml.safe_dump({"services": {"web": {"ports": ["9000:80/udp", "18000:8000/tcp"]}}})
    assert compose_utils.best_web_port_from_compose(content) == 18000


def test_host_network_zoraxy_management_is_listed_without_port_env():
    content = yaml.safe_dump({"services": {"gateway": {
        "image": "zoraxydocker/zoraxy:latest", "network_mode": "host",
    }}})
    rows = ports.parse_compose_ports(content, "gateway", "Compose", ["192.0.2.10"])
    assert [row["port"] for row in rows] == [8000]


def test_catalog_keeps_declared_management_target_after_host_remapping(monkeypatch, tmp_path):
    path = tmp_path / "nginx-proxy-manager"
    path.mkdir()
    content = yaml.safe_dump({"services": {"nginx-proxy-manager": {
        "image": "jc21/nginx-proxy-manager:latest",
        "ports": ["8080:80", "18081:81", "8443:443"],
    }}})
    (path / "docker-compose.yml").write_text(content)
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(apps, "_running", lambda _: True)
    monkeypatch.setattr(apps.docker_mgr, "list_compose_projects", lambda: [])
    assert apps.get_app("nginx-proxy-manager")["port"] == 18081
    entry = next(app for app in apps.get_catalog()["apps"] if app["id"] == "nginx-proxy-manager")
    assert entry["port"] == 18081


@pytest.mark.parametrize("app_id,target,scheme", [
    ("postgres", 5432, ""), ("redis", 6379, ""), ("unbound", 53, ""), ("portainer", 9443, "https"),
    ("code-server", 8443, "http"), ("kasm", 6901, "https"), ("nextcloud", 80, "http"),
])
def test_apps_return_scheme_for_actual_target_after_remapping(monkeypatch, tmp_path, app_id, target, scheme):
    app = next(entry for entry in apps.CATALOG if entry["id"] == app_id)
    path = tmp_path / app_id
    path.mkdir()
    content = {"services": {app_id: {"image": app["tpl"]["image"], "ports": [f"21234:{target}"]}}}
    (path / "docker-compose.yml").write_text(yaml.safe_dump(content))
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(apps, "_running", lambda _: True)
    monkeypatch.setattr(apps.docker_mgr, "list_compose_projects", lambda: [])

    catalog_entry = next(entry for entry in apps.get_catalog()["apps"] if entry["id"] == app_id)
    for item in (catalog_entry, apps.get_app(app_id)):
        assert (item["port"], item["web_scheme"]) == (21234, scheme)


def test_unpublished_or_loopback_web_port_is_not_offered(monkeypatch, tmp_path):
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(apps, "_running", lambda _: True)
    monkeypatch.setattr(apps.docker_mgr, "list_compose_projects", lambda: [])
    for name, service in {
        "jellyfin": {"image": "jellyfin/jellyfin", "ports": ["127.0.0.1:8181:8096"]},
        "nextcloud": {"image": "nextcloud", "expose": [80]},
        "tailscale": {"image": "tailscale/tailscale", "network_mode": "host"},
    }.items():
        path = tmp_path / name
        path.mkdir()
        (path / "docker-compose.yml").write_text(yaml.safe_dump({"services": {name: service}}))
        assert apps.get_app(name)["web_scheme"] == ""


@pytest.mark.parametrize("mapping,expected", [
    ({"target": 5432, "published": 8080}, ""), ({"target": 53, "published": 8080, "protocol": "udp"}, ""),
    ({"target": 6789, "published": 20200, "app_protocol": "https"}, "https"),
    ({"target": 6789, "published": 20200}, ""),
])
def test_project_web_protocol_requires_web_binding(mapping, expected):
    content = yaml.safe_dump({"services": {"custom": {"ports": [mapping]}}})
    assert compose_utils.best_web_endpoint_from_compose(content)[1] == expected


@pytest.mark.parametrize("internal,external,preferred", [
    ("127.0.0.1:18081:81", "19080:80", 81),
    ("15432:5432", "19443:443", 5432),
])
def test_browser_prefers_reachable_web_binding_while_port_lookups_keep_preferred_target(internal, external, preferred):
    content = yaml.safe_dump({"services": {"backend": {"ports": [internal]}, "web": {"ports": [external]}}})
    port, target = map(int, external.split(":"))
    assert compose_utils.best_web_endpoint_from_compose(content, preferred) == (
        port, "https" if target == 443 else "http")
    assert compose_utils.best_web_port_from_compose(content, preferred_target=preferred) == int(
        internal.split(":")[-2])
