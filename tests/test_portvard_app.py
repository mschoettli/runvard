import importlib.util
from pathlib import Path


def _load_portvard():
    path = Path(__file__).resolve().parents[1] / "docker-apps" / "portvard" / "app.py"
    spec = importlib.util.spec_from_file_location("portvard_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_compose_published_ports():
    portvard = _load_portvard()
    content = """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
      - "127.0.0.1:9443:443/tcp"
"""

    rows = portvard.parse_compose_ports(content, "demo-app", "App", ["192.168.1.10"])

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
    portvard = _load_portvard()
    content = """
services:
  portvard:
    image: python:3.13-slim
    network_mode: host
    environment:
      - PORT=8766
"""

    rows = portvard.parse_compose_ports(content, "portvard", "App", ["192.168.1.10"])

    assert rows[0]["ip"] == "192.168.1.10"
    assert rows[0]["port"] == 8766
    assert rows[0]["app"] == "Portvard"


def test_runvard_ports_reads_app_and_compose_dirs(monkeypatch, tmp_path):
    portvard = _load_portvard()
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

    monkeypatch.setattr(portvard, "RUNVARD_APPS_DIR", apps_dir)
    monkeypatch.setattr(portvard, "RUNVARD_COMPOSE_DIR", compose_dir)
    monkeypatch.setattr(portvard, "host_ips", lambda: ["192.168.1.10"])

    rows = portvard.runvard_ports()

    assert [(row["app"], row["port"]) for row in rows] == [
        ("Jellyfin", 8096),
        ("Custom", 9000),
    ]
