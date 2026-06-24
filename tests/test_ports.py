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

    rows = ports.list_ports(apps_dir, compose_dir)["ports"]

    assert [(row["app"], row["port"]) for row in rows] == [
        ("Jellyfin", 8096),
        ("Custom", 9000),
    ]
