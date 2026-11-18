import importlib
import ipaddress
import pytest


def api():
    return importlib.import_module('modules.network_map')


def test_scope_rejects_public_loopback_and_large_networks():
    for value in ['8.8.8.0/24', '127.0.0.0/24', '169.254.0.0/24', '10.0.0.0/8', '::1/128']:
        with pytest.raises(ValueError):
            api().validate_network(value)
    assert str(api().validate_network('192.168.178.0/24')) == '192.168.178.0/24'


def test_scan_preserves_names_positions_offline_devices_and_never_invents_edges(tmp_path):
    manager = api().NetworkMap(tmp_path)
    manager.merge_lan([{'id': 'lan:aa:bb', 'name': 'NAS', 'ip': '192.168.1.2', 'status': 'online'}])
    manager.edit_device('lan:aa:bb', {'label': 'Archive', 'room': 'Office', 'x': 20, 'y': 50})
    manager.merge_lan([])
    snapshot = api().NetworkMap(tmp_path).snapshot()
    device = snapshot['devices'][0]
    assert device['label'] == 'Archive'
    assert device['x'] == 20
    assert device['status'] == 'offline'
    assert snapshot['edges'] == []


def test_zigbee_devices_mesh_and_sleeping_sensor(tmp_path):
    manager = api().NetworkMap(tmp_path)
    manager.ingest_zigbee('bridge/devices', [
        {'ieee_address': '0x01', 'friendly_name': 'Coordinator', 'type': 'Coordinator'},
        {'ieee_address': '0x02', 'friendly_name': 'Socket', 'type': 'Router'},
        {'ieee_address': '0x03', 'friendly_name': 'Door', 'type': 'EndDevice', 'power_source': 'Battery'},
    ])
    manager.ingest_zigbee('Door', {'battery': 85, 'linkquality': 70})
    manager.ingest_zigbee('bridge/response/networkmap', {'status': 'ok', 'data': {'value': {'links': [
        {'source': {'ieeeAddr': '0x02'}, 'target': {'ieeeAddr': '0x03'}, 'linkquality': 70}
    ]}}})
    snap = manager.snapshot()
    assert len(snap['devices']) == 3
    sensor = next(d for d in snap['devices'] if d['name'] == 'Door')
    assert sensor['battery'] == 85
    assert sensor['status'] == 'unknown'
    assert snap['edges'][0]['source'] == 'zigbee:0x02'
    assert snap['edges'][0]['target'] == 'zigbee:0x03'
    assert snap['edges'][0]['origin'] == 'zigbee'
    assert snap['mesh_at'] > 0


def test_manual_link_validation_and_disconnect(tmp_path):
    m = api().NetworkMap(tmp_path)
    m.merge_lan([{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}])
    with pytest.raises(ValueError): m.edit_device('a', {'parent': 'missing'})
    with pytest.raises(ValueError): m.edit_device('a', {'parent': 'a'})
    m.edit_device('a', {'parent': 'b'})
    with pytest.raises(ValueError): m.edit_device('b', {'parent': 'a'})
    assert m.snapshot()['edges'][0]['origin'] == 'manual'
    m.edit_device('a', {'parent': ''})
    assert not m.snapshot()['edges']


def test_zigbee_endpoint_rejects_external_and_credentials(tmp_path):
    m = api().NetworkMap(tmp_path)
    for url in ['http://example.com:8080', 'http://8.8.8.8:8080', 'http://user:pass@192.168.1.3', 'file:///etc/passwd']:
        with pytest.raises(ValueError): m.configure({'zigbee_url': url})
    m.configure({'zigbee_url': 'http://192.168.1.3:8080', 'token': 'private-test-token'})
    assert 'private-test-token' not in str(m.snapshot())
    assert 'private-test-token' not in m.path.read_text()


def test_failed_scan_keeps_previous_snapshot(monkeypatch, tmp_path):
    m = api().NetworkMap(tmp_path)
    m.merge_lan([{'id':'nas','name':'NAS','ip':'192.168.1.2'}])
    previous = m.snapshot()
    monkeypatch.setattr(api(), 'local_networks', lambda: [])
    assert m.scan() is False
    assert m.snapshot()['devices'] == previous['devices']
    assert m.snapshot()['scan_at'] == previous['scan_at']
    assert not m.snapshot()['scanning']


def test_mac_promotion_preserves_manual_connection(tmp_path):
    m = api().NetworkMap(tmp_path)
    m.merge_lan([{'ip':'192.168.1.1','name':'Router'}, {'ip':'192.168.1.2','name':'NAS'}])
    m.edit_device('lan:192.168.1.2', {'parent':'lan:192.168.1.1','label':'Archive'})
    m.merge_lan([{'ip':'192.168.1.1','name':'Router','mac':'aa:bb'}, {'ip':'192.168.1.2','name':'NAS','mac':'cc:dd'}])
    assert len(m.snapshot()['devices']) == 2
    assert m.snapshot()['edges'] == [{'source':'lan:aa:bb','target':'lan:cc:dd','origin':'manual'}]
    assert next(d for d in m.snapshot()['devices'] if d['id']=='lan:cc:dd')['label']=='Archive'


def test_invalid_mesh_does_not_discard_last_successful_map(tmp_path):
    m = api().NetworkMap(tmp_path)
    m.state['mesh_at'] = 123
    m.state['edges'] = [{'source':'a','target':'b','origin':'zigbee'}]
    m.ingest_zigbee('bridge/response/networkmap',{'status':'error','error':'timeout'})
    assert m.snapshot()['mesh_at'] == 123
    assert len(m.snapshot()['edges']) == 1
    assert m.snapshot()['zigbee_error'] == 'mesh_failed'


def test_zigbee_session_reads_devices_and_only_requests_mesh(monkeypatch, tmp_path):
    import asyncio
    import json
    m = api().NetworkMap(tmp_path)
    m.configure({'zigbee_url':'http://192.168.1.3:8080'})
    m.mesh_request.set()
    sent = []
    messages = [
        {'topic':'bridge/devices','payload':[{'ieee_address':'0x01','friendly_name':'Sensor','type':'EndDevice'}]},
        {'topic':'Sensor','payload':{'battery':90}},
    ]
    class Connection:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def send(self,value): sent.append(json.loads(value))
        async def recv(self):
            message = messages.pop(0)
            if not messages: m.stop_event.set()
            return json.dumps(message)
    monkeypatch.setattr(api().websockets,'connect',lambda *args,**kwargs:Connection())
    asyncio.run(m.zigbee_session('http://192.168.1.3:8080'))
    assert m.snapshot()['devices'][0]['battery'] == 90
    assert sent == [{'topic':'bridge/request/networkmap','payload':{'type':'raw','routes':True}}]
