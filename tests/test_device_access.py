import json

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import importlib.util
    assert importlib.util.find_spec("modules.device_access"), "Device access is not implemented"
    from modules.device_access import DeviceAccessStore
    store = DeviceAccessStore(tmp_path / 'devices.json')
    monkeypatch.setattr(server, 'DEVICE_ACCESS', store)
    monkeypatch.setattr(server, '_device_sources', lambda: [
        {'id': 'local', 'name': 'Host', 'local': True},
        {'id': 'peer:a', 'name': 'Peer', 'snapshot': {
            'cpu_percent': 32, 'ram_percent': 55, 'captured_at': 100,
            'password': 'must never escape', 'docker': {'total': 2, 'running': 1, 'secret': 'hidden'},
        }, 'health': 'online', 'last_contact': 100},
    ])
    monkeypatch.setattr(server, '_device_local_metrics', lambda fields: {
        k: {'cpu_percent': 12, 'ram_percent': 25}.get(k) for k in fields
    })
    client = TestClient(server.app)
    admin = TestClient(server.app)
    admin.cookies.set(server.COOKIE_NAME, server.make_token('admin', 3600))
    return store, client, admin


def create(admin, **changes):
    data = {'name': 'Tischuhr', 'servers': json.dumps(['local']),
            'fields': json.dumps(['cpu_percent'])}
    data.update(changes)
    return admin.post('/api/devices/admin/create', data=data)


def test_create_token_is_only_returned_once_and_stored_hashed(setup):
    store, client, admin = setup
    response = create(admin)
    assert response.status_code == 200
    created = response.json()
    assert created['token'].startswith('rvd_')
    listed = admin.get('/api/devices/admin/list').json()
    assert created['token'] not in json.dumps(listed)
    assert created['token'] not in store.path.read_text()
    assert store.path.stat().st_mode & 0o777 == 0o600
    result = client.get('/api/devices/metrics', headers={'Authorization': 'Bearer ' + created['token']})
    assert result.status_code == 200
    row = result.json()['servers'][0]
    assert row['cpu_percent'] == 12
    assert 'ram_percent' not in row
    assert result.headers['cache-control'] == 'no-store'
    assert 'token' not in admin.get('/api/devices/admin/list').json()['devices'][0]


def test_device_token_never_grants_session_or_admin_access(setup):
    _, client, admin = setup
    token = create(admin).json()['token']
    headers = {'Authorization': 'Bearer ' + token}
    for path in ['/api/system/stats', '/api/accounts', '/api/devices/admin/list']:
        assert client.get(path, headers=headers).status_code == 401
    assert client.post('/api/devices/admin/create', data={'name': 'evil'}, headers=headers).status_code == 401
    assert client.post('/api/devices/metrics', headers=headers).status_code == 405
    assert admin.get('/api/devices/metrics').status_code == 401
    for credential in ['', 'Bearer wrong', 'Basic wrong']:
        assert client.get('/api/devices/metrics', headers={'Authorization': credential}).status_code == 401
    admin.cookies.set(server.COOKIE_NAME, server.make_token('reader', 3600, 'readonly'))
    assert create(admin).status_code == 403


def test_edit_restricts_existing_token_immediately_and_revocation_persists(setup):
    store, client, admin = setup
    created = create(admin).json()
    headers = {'Authorization': 'Bearer ' + created['token']}
    data = {'device_id': created['device']['id'], 'name': 'Clock',
            'servers': json.dumps(['peer:a']), 'fields': json.dumps(['ram_percent']), 'enabled': 'true'}
    assert admin.post('/api/devices/admin/update', data=data).status_code == 200
    rows = client.get('/api/devices/metrics', headers=headers).json()['servers']
    assert [r['id'] for r in rows] == ['peer:a']
    assert rows[0]['ram_percent'] == 55
    assert 'cpu_percent' not in rows[0]
    assert 'password' not in json.dumps(rows)
    data['enabled'] = 'false'
    assert admin.post('/api/devices/admin/update', data=data).status_code == 200
    from modules.device_access import DeviceAccessStore
    assert DeviceAccessStore(store.path).authenticate(created['token']) is None
    assert client.get('/api/devices/metrics', headers=headers).status_code == 401
    assert admin.post('/api/devices/admin/delete', data={'device_id': created['device']['id']}).status_code == 200
    assert admin.get('/api/devices/admin/list').json()['devices'] == []


def test_default_deny_and_strict_validation(setup):
    _, client, admin = setup
    created = admin.post('/api/devices/admin/create', data={'name': 'Empty'}).json()
    assert client.get('/api/devices/metrics', headers={'Authorization': 'Bearer ' + created['token']}).json()['servers'] == []
    for changes in [{'servers': '["unknown"]'}, {'fields': '["password"]'},
                    {'fields': '{}'}, {'fields': 'not json'}, {'name': '  '},
                    {'servers': '[1]'}]:
        assert create(admin, **changes).status_code == 400


def test_remote_payload_is_explicitly_projected_and_missing_values_are_null(setup):
    _, client, admin = setup
    created = create(admin, servers='["peer:a"]', fields='["containers","temperatures"]').json()
    row = client.get('/api/devices/metrics', headers={'Authorization': 'Bearer ' + created['token']}).json()['servers'][0]
    assert row['containers'] == {'total': 2, 'running': 1}
    assert row['temperatures'] is None
    assert 'secret' not in json.dumps(row)
    assert 'online' not in row


def test_selection_change_during_collection_does_not_leak_old_permissions(setup, monkeypatch):
    _, client, admin = setup
    created = create(admin).json()
    def collect(fields):
        admin.post('/api/devices/admin/update', data={
            'device_id': created['device']['id'], 'name': 'Clock',
            'servers': '[]', 'fields': '[]', 'enabled': 'true',
        })
        return {'cpu_percent': 99}
    monkeypatch.setattr(server, '_device_local_metrics', collect)
    assert client.get('/api/devices/metrics', headers={'Authorization': 'Bearer ' + created['token']}).json()['servers'] == []


def test_device_metrics_route_requires_credentials():
    assert TestClient(server.app).get('/api/devices/metrics').status_code == 401


def test_management_ui_exposes_explicit_checkboxes_and_one_time_token():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'static/index.html').read_text()
    assert 'id="um-devices"' in source
    assert 'const DEVICE_I18N=' in source
    assert 'name="device-server"' in source
    assert 'name="device-field"' in source
    assert 'deviceTokenResult' in source


def test_disable_still_works_after_shared_server_disappears(setup, monkeypatch):
    _, client, admin = setup
    created = create(admin, servers='["peer:a"]').json()
    monkeypatch.setattr(server, '_device_sources', lambda: [])
    response = admin.post('/api/devices/admin/enabled', data={'device_id': created['device']['id'], 'enabled': 'false'})
    assert response.status_code == 200
    assert client.get('/api/devices/metrics', headers={'Authorization': 'Bearer ' + created['token']}).status_code == 401


def test_local_telemetry_does_not_read_unselected_categories(monkeypatch):
    from modules.device_access import LocalTelemetry
    def forbidden():
        raise AssertionError('unselected source read')
    monkeypatch.setattr(server.storage, 'list_block_devices', forbidden)
    monkeypatch.setattr(server.backup, 'get_history', forbidden)
    calls = []
    monkeypatch.setattr(server.system, 'get_stats', lambda: calls.append(1) or {'cpu': {'percent': 14}, 'password': 'secret'})
    telemetry = LocalTelemetry()
    assert telemetry.collect([]) == {}
    assert telemetry.collect(['cpu_percent']) == {'cpu_percent': 14}
    assert telemetry.collect(['cpu_percent']) == {'cpu_percent': 14}
    assert calls == [1]


def test_local_categories_project_only_safe_measurements(monkeypatch):
    from modules.device_access import LocalTelemetry, FIELDS
    monkeypatch.setattr(server.system, 'get_system_info', lambda: {'uptime_seconds': 90, 'secret': 'private'})
    monkeypatch.setattr(server.system, 'get_stats', lambda: {
        'cpu': {'percent': 14}, 'memory': {'percent': 22},
        'network': {'up_rate': 10, 'down_rate': 20, 'secret': 'private'},
    })
    monkeypatch.setattr(server.system, 'get_disk_usage', lambda: [{'mountpoint': '/', 'percent': 40, 'device': 'private'}])
    monkeypatch.setattr(server.system, 'get_temps', lambda: {'cpu': [{'label': 'CPU', 'current': 45, 'secret': 'private'}]})
    monkeypatch.setattr(server.storage, 'list_block_devices', lambda: {'devices': [{'type': 'disk', 'name': 'sda'}]})
    monkeypatch.setattr(server.storage, 'smart_data', lambda name: {'available': True, 'healthy': True, 'temperature': 30, 'serial': 'private'})
    monkeypatch.setattr(server.docker_mgr, 'list_containers', lambda: [{'state': 'running', 'env': 'private'}, {'state': 'exited'}])
    monkeypatch.setattr(server.vms, 'list_vms', lambda: [{'active': True, 'config': 'private'}])
    monkeypatch.setattr(server.apps, '_load_updates', lambda: {'updates': [{'credentials': 'private'}]})
    monkeypatch.setattr(server.monitoring, 'get_alert_history', lambda: [{'message': 'private'}, {'resolved': True}])
    monkeypatch.setattr(server.backup, 'get_history', lambda: [
        {'time': 200, 'success': False, 'output': 'private'}, {'time': 100, 'success': True},
    ])
    result = LocalTelemetry().collect(FIELDS)
    assert set(result) == set(FIELDS)
    assert result['containers'] == {'total': 2, 'running': 1}
    assert result['vms'] == {'total': 1, 'running': 1}
    assert result['updates'] == result['alerts'] == 1
    assert result['disk_percent'] == 40
    assert result['temperatures'] == [{'sensor': 'CPU', 'current': 45}]
    assert result['disk_health'] == [{'device': 'sda', 'available': True, 'healthy': True, 'temperature': 30}]
    assert result['backups'] == {'last_run': 200, 'success': False, 'last_success': 100}
    assert 'private' not in json.dumps(result)


def test_peer_snapshot_zero_and_unknown_are_distinct():
    from modules.device_access import snapshot_metrics
    result = snapshot_metrics({'snapshot': {'cpu_percent': 0, 'ram_percent': None, 'docker': {'available': False, 'running': 0}}}, ['cpu_percent', 'ram_percent', 'containers'])
    assert result == {'cpu_percent': 0, 'ram_percent': None, 'containers': None}
