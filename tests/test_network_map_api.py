from fastapi.testclient import TestClient
import server
from modules.network_map import NetworkMap


def client(role=None):
    c = TestClient(server.app)
    if role: c.cookies.set(server.COOKIE_NAME, server.make_token('tester', 3600, role))
    return c


def test_map_requires_login():
    assert client().get('/network-map', follow_redirects=False).status_code in (302,401)
    assert client().get('/api/network-map').status_code == 401


def test_readonly_can_view_but_cannot_scan_or_edit(monkeypatch,tmp_path):
    monkeypatch.setattr(server, 'NETWORK_MAP', NetworkMap(tmp_path), raising=False)
    c = client('readonly')
    assert c.get('/api/network-map').status_code == 200
    assert c.get('/network-map').status_code == 200
    for path in ('scan','device','settings','mesh'):
        assert c.post('/api/network-map/'+path,json={}).status_code == 403


def test_settings_and_bad_inputs(monkeypatch,tmp_path):
    monkeypatch.setattr(server, 'NETWORK_MAP', NetworkMap(tmp_path), raising=False)
    c = client('admin')
    assert c.post('/api/network-map/settings',json={'networks':['8.8.8.0/24']}).status_code == 400
    assert c.post('/api/network-map/settings',json={'networks':['192.168.1.0/24']}).status_code == 200
    assert c.post('/api/network-map/settings',data={'networks':'bad'}).status_code == 415
    assert c.post('/api/network-map/settings',json={},headers={'Origin':'https://evil.example'}).status_code == 403
    assert c.post('/api/network-map/device',json={'id':'missing'}).status_code == 400
