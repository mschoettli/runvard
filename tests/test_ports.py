import json

from modules import ports


def test_parse_compose_published_ports():
    content = """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
      - "127.0.0.1:9443:443/tcp"
"""

    rows = ports.parse_compose_ports(content, "demo-app", "App", ["192.168.1.10"])

    assert rows == [
        {
            "ip": "192.168.1.10",
            "port": 8080,
            "protocol": "tcp",
            "app": "Demo App",
            "service": "web",
            "source": "App",
        },
        {
            "ip": "127.0.0.1",
            "port": 9443,
            "protocol": "tcp",
            "app": "Demo App",
            "service": "web",
            "source": "App",
        },
    ]


def test_parse_host_network_port_env():
    content = """
services:
  hostapp:
    image: python:3.13-slim
    network_mode: host
    environment:
      - PORT=8766
"""

    rows = ports.parse_compose_ports(content, "hostapp", "App", ["192.168.1.10"])

    assert rows[0]["ip"] == "192.168.1.10"
    assert rows[0]["port"] == 8766
    assert rows[0]["app"] == "Hostapp"


def test_parse_compose_long_port_syntax():
    content = """
services:
  app:
    image: nginx
    ports:
      - target: 80
        published: 8080
        protocol: tcp
"""

    rows = ports.parse_compose_ports(content, "long-form", "Compose", ["192.168.1.10"])

    assert rows[0]["ip"] == "192.168.1.10"
    assert rows[0]["port"] == 8080
    assert rows[0]["app"] == "Long Form"


def test_list_ports_reads_app_and_compose_dirs(monkeypatch, tmp_path):
    apps_dir = tmp_path / "apps"
    compose_dir = tmp_path / "compose"
    app_dir = apps_dir / "jellyfin"
    app_dir.mkdir(parents=True)
    compose_dir.mkdir()
    (app_dir / "docker-compose.yml").write_text("""
services:
  jellyfin:
    ports:
      - "8096:8096"
""")
    (compose_dir / "custom.yml").write_text("""
services:
  ui:
    ports:
      - "9000:80"
""")

    monkeypatch.setattr(ports, "host_ips", lambda: ["192.168.1.10"])
    monkeypatch.setattr(ports, "_tcp_reachable", lambda ip, port: port == 8096)

    rows = ports.list_ports(apps_dir, compose_dir)["ports"]

    assert [(row["app"], row["port"]) for row in rows] == [
        ("Jellyfin", 8096),
        ("Custom", 9000),
    ]
    assert [row["reachable"] for row in rows] == [True, False]


def test_list_ports_hides_database_and_cache_ports(monkeypatch, tmp_path):
    apps_dir = tmp_path / "apps"
    compose_dir = tmp_path / "compose"
    app_dir = apps_dir / "stack"
    app_dir.mkdir(parents=True)
    compose_dir.mkdir()
    (app_dir / "docker-compose.yml").write_text("""
services:
  web:
    image: nginx
    ports:
      - "8080:80"
  postgres:
    image: postgres
    ports:
      - "5432:5432"
  redis:
    image: redis
    ports:
      - "6379:6379"
  mariadb:
    image: mariadb
    ports:
      - "3306:3306"
""")

    monkeypatch.setattr(ports, "host_ips", lambda: ["192.168.1.10"])
    monkeypatch.setattr(ports, "_tcp_reachable", lambda ip, port: True)

    rows = ports.list_ports(apps_dir, compose_dir)["ports"]

    assert [(row["service"], row["port"]) for row in rows] == [("web", 8080)]


def test_list_ports_hides_infra_service_even_on_web_like_port(monkeypatch, tmp_path):
    apps_dir = tmp_path / "apps"
    compose_dir = tmp_path / "compose"
    app_dir = apps_dir / "infra"
    app_dir.mkdir(parents=True)
    compose_dir.mkdir()
    (app_dir / "docker-compose.yml").write_text("""
services:
  db-admin-internal:
    image: example
    ports:
      - "8080:8080"
  app:
    image: example
    ports:
      - target: 3000
        published: 3000
        protocol: tcp
""")

    monkeypatch.setattr(ports, "host_ips", lambda: ["192.168.1.10"])
    monkeypatch.setattr(ports, "_tcp_reachable", lambda ip, port: True)

    rows = ports.list_ports(apps_dir, compose_dir)["ports"]

    assert [(row["service"], row["port"]) for row in rows] == [("app", 3000)]


def test_list_ports_includes_custom_dashboard_tiles(monkeypatch, tmp_path):
    apps_dir = tmp_path / "apps"
    compose_dir = tmp_path / "compose"
    dash_file = tmp_path / "dashboard.json"
    apps_dir.mkdir()
    compose_dir.mkdir()
    dash_file.write_text(json.dumps({
        "tiles": [
            {
                "id": "custom_1",
                "type": "custom",
                "name": "Home Assistant",
                "url": "http://192.168.1.50:8123/lovelace",
            },
            {
                "id": "custom_2",
                "type": "custom",
                "name": "Router",
                "url": "https://router.local",
            },
            {
                "id": "custom_3",
                "type": "custom",
                "name": "Invalid",
                "url": "ftp://192.168.1.5:21",
            },
        ]
    }))

    monkeypatch.setattr(ports, "host_ips", lambda: ["192.168.1.10"])
    monkeypatch.setattr(ports, "_tcp_reachable", lambda ip, port: port == 8123)

    rows = ports.list_ports(apps_dir, compose_dir, dash_file)["ports"]

    assert [(row["app"], row["ip"], row["port"], row.get("url")) for row in rows] == [
        ("Home Assistant", "192.168.1.50", 8123, "http://192.168.1.50:8123/lovelace"),
        ("Router", "router.local", 443, "https://router.local"),
    ]
    assert [row["reachable"] for row in rows] == [True, False]


def test_list_ports_ignores_legacy_portvard_app(monkeypatch, tmp_path):
    apps_dir = tmp_path / "apps"
    compose_dir = tmp_path / "compose"
    old_dir = apps_dir / "portvard"
    old_dir.mkdir(parents=True)
    compose_dir.mkdir()
    (old_dir / "docker-compose.yml").write_text("""
services:
  portvard:
    network_mode: host
    environment:
      - PORT=8767
""")

    monkeypatch.setattr(ports, "host_ips", lambda: ["192.168.1.10"])
    monkeypatch.setattr(ports, "_tcp_reachable", lambda ip, port: True)

    assert ports.list_ports(apps_dir, compose_dir)["ports"] == []
