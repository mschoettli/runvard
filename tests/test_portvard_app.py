import importlib.util
from pathlib import Path


def _load_portvard():
    path = Path(__file__).resolve().parents[1] / "docker-apps" / "portvard" / "app.py"
    spec = importlib.util.spec_from_file_location("portvard_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_common_port_scan_is_small_default():
    portvard = _load_portvard()

    ports = portvard.parse_ports("common")

    assert 22 in ports
    assert 80 in ports
    assert 443 in ports
    assert 8766 in ports
    assert len(ports) < 50


def test_full_port_scan_keeps_all_tcp_ports_available():
    portvard = _load_portvard()

    ports = portvard.parse_ports("full")

    assert ports[0] == 1
    assert ports[-1] == 65535
    assert len(ports) == 65535


def test_iter_hosts_can_limit_large_networks():
    portvard = _load_portvard()

    hosts = list(portvard.iter_hosts(["10.0.0.0/16"], max_hosts=3))

    assert hosts == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


def test_arp_discovery_parses_ip_and_mac(monkeypatch):
    portvard = _load_portvard()

    def fake_run_cmd(args, timeout=3):
        assert args[0] == "arp-scan"
        return "192.168.1.20\taa:bb:cc:dd:ee:ff\tVendor\n"

    monkeypatch.setattr(portvard, "run_cmd", fake_run_cmd)

    assert portvard.discover_arp_hosts(["192.168.1.0/24"]) == {
        "192.168.1.20": "aa:bb:cc:dd:ee:ff",
    }
