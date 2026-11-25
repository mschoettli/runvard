"""Port checks must follow protocol and host address, including Zoraxy."""
import json
import socket
from types import SimpleNamespace

import pytest
import yaml
from modules import compose_utils, docker_mgr


def compose(*ports):
    return yaml.safe_dump({'services': {'zoraxy': {'image': 'nginx', 'ports': list(ports)}}})


@pytest.mark.parametrize('ports', [
    ['192.168.178.60:53:53/tcp', '192.168.178.60:53:53/udp'],
    ['192.168.178.60:8001:8000', '127.0.0.1:8001:8000'],
    [{'host_ip': '192.168.178.60', 'published': '53', 'target': 53, 'protocol': 'udp'},
     {'host_ip': '192.168.178.60', 'published': '53', 'target': 53, 'protocol': 'tcp'}],
])
def test_distinct_bindings_are_allowed(monkeypatch, ports):
    monkeypatch.setattr(docker_mgr, '_compose_owned_ports', lambda _: set())
    monkeypatch.setattr(docker_mgr, '_port_is_available', lambda *a, **kw: True)
    assert docker_mgr.check_compose_ports('zoraxy', compose(*ports))['ok']


@pytest.mark.parametrize('ports', [
    ['8001:8000', '127.0.0.1:8001:8000'],
    ['127.0.0.1:8001:8000', '127.0.0.1:8001:80'],
    ['[::]:8001:8000', '[::1]:8001:80'],
])
def test_overlapping_bindings_are_rejected(monkeypatch, ports):
    monkeypatch.setattr(docker_mgr, '_compose_owned_ports', lambda _: set())
    monkeypatch.setattr(docker_mgr, '_port_is_available', lambda *a, **kw: True)
    result = docker_mgr.check_compose_ports('demo', compose(*ports))
    assert [c['reason'] for c in result['conflicts']] == ['duplicate', 'duplicate']


def test_parser_preserves_protocol_and_ipv6_host():
    entry, = compose_utils.published_ports_from_compose(compose('[::1]:8001:8000/udp'))
    assert entry['host_ip'] == '::1'
    assert entry['protocol'] == 'udp'


def test_owned_bindings_preserve_protocol_and_address(monkeypatch):
    container = SimpleNamespace(attrs={'NetworkSettings': {'Ports': {
        '8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8001'}],
        '53/udp': [{'HostIp': '192.168.178.60', 'HostPort': '53'}],
    }}})
    monkeypatch.setattr(docker_mgr, 'HAS_DOCKER', True)
    monkeypatch.setattr(docker_mgr, '_get_client', lambda: SimpleNamespace(containers=SimpleNamespace(list=lambda **kw: [container])))
    assert docker_mgr._compose_owned_ports('zoraxy') == {
        (8001, 'tcp', '127.0.0.1'), (53, 'udp', '192.168.178.60'),
    }


@pytest.mark.parametrize('mapping,allowed', [
    ('127.0.0.1:8001:8000/tcp', True),
    ('127.0.0.1:8001:8000/udp', False),
    ('192.168.178.60:8001:8000/tcp', False),
])
def test_ownership_does_not_hide_other_bindings(monkeypatch, mapping, allowed):
    monkeypatch.setattr(docker_mgr, '_compose_owned_ports', lambda _: {(8001, 'tcp', '127.0.0.1')})
    monkeypatch.setattr(docker_mgr, '_port_is_available', lambda *a, **kw: False)
    assert docker_mgr.check_compose_ports('zoraxy', compose(mapping))['ok'] is allowed


def test_udp_probe_checks_udp_without_confusing_tcp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        assert not docker_mgr._port_is_available(port, '127.0.0.1', 'udp')
        assert docker_mgr._port_is_available(port, '127.0.0.1', 'tcp')


def test_modal_saves_existing_zoraxy_bindings(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    content = compose('192.168.178.60:53:53/udp', '192.168.178.60:53:53/tcp',
                      '192.168.178.60:80:80', '192.168.178.60:443:443',
                      '192.168.178.60:8001:8000', '127.0.0.1:8001:8000')
    owned = {(53, p, '192.168.178.60') for p in ('tcp', 'udp')}
    owned.update((p, 'tcp', '192.168.178.60') for p in (80, 443, 8001))
    owned.add((8001, 'tcp', '127.0.0.1'))
    monkeypatch.setattr(docker_mgr, '_compose_owned_ports', lambda _: owned)
    monkeypatch.setattr(docker_mgr, '_port_is_available', lambda *a, **kw: False)
    monkeypatch.setattr(docker_mgr, 'save_compose', lambda *a, **kw: {'ok': True})
    server.app.dependency_overrides[server.require_admin] = lambda: 'tester'
    try:
        response = TestClient(server.app).post('/api/docker/compose/save', data={'name': 'zoraxy', 'content': content})
        assert response.status_code == 200
        assert response.json()['ok'], response.json()
    finally:
        server.app.dependency_overrides.pop(server.require_admin, None)
