import json
from fastapi.testclient import TestClient
import server


def client(monkeypatch, role='admin'):
    monkeypatch.setattr(server, 'login_enabled', lambda: True)
    c = TestClient(server.app)
    c.cookies.set(server.COOKIE_NAME, server.make_token('tester', 3600, role, account_salt=None))
    return c


def test_convert_roundtrip_is_available_to_authenticated_users(monkeypatch):
    c = client(monkeypatch)
    source = 'services: {web: {image: nginx, environment: {A: "false"}}}'
    parsed = c.post('/api/docker/compose/convert', data={'content': source})
    assert parsed.status_code == 200
    document = parsed.json()['document']
    encoded = c.post('/api/docker/compose/convert', data={'document': json.dumps(document)})
    assert encoded.status_code == 200
    assert encoded.json()['document'] == document


def test_invalid_yaml_returns_400(monkeypatch):
    response = client(monkeypatch).post('/api/docker/compose/convert', data={'content': 'services: ['})
    assert response.status_code == 400


def test_source_load_errors_are_reported_as_400(monkeypatch):
    def missing(name):
        raise ValueError('missing source')
    monkeypatch.setattr(server.docker_mgr, 'get_compose', missing)
    response = client(monkeypatch).get('/api/docker/compose/get', params={'name': 'demo'})
    assert response.status_code == 400


def test_save_passes_all_sources_to_backend(monkeypatch):
    calls = []
    monkeypatch.setattr(server.docker_mgr, 'check_compose_source_ports', lambda *args: {'ok': True})
    def save(*args, **kwargs):
        calls.append((args, kwargs))
        return {'ok': True}
    monkeypatch.setattr(server.docker_mgr, 'save_compose', save)
    sources = ['services: {web: {image: nginx}}', 'services: {web: {restart: always}}']
    response = client(monkeypatch).post('/api/docker/compose/save', data={
        'name': 'demo', 'content': sources[0], 'source_contents': json.dumps(sources)})
    assert response.status_code == 200
    assert calls[0][1]['source_contents'] == sources


def test_convert_preserves_compose_override_tags(monkeypatch):
    c = client(monkeypatch)
    response = c.post('/api/docker/compose/convert', data={'content': 'services: {web: {ports: !override ["9090:80"]}}'})
    assert response.status_code == 200
    data = response.json()
    response = c.post('/api/docker/compose/convert', data={
        'document': json.dumps(data['document']), 'yaml_tags': json.dumps(data['tags'])})
    assert response.status_code == 200
    assert '!override' in response.json()['content']


def test_scalar_form_edits_preserve_yaml_comments_and_quotes(monkeypatch):
    c = client(monkeypatch)
    source = '# Project notes\nservices:\n  web:\n    image: "nginx" # pinned by admin\n    environment:\n      TZ: Europe/Zurich\n'
    document = c.post('/api/docker/compose/convert', data={'content': source}).json()['document']
    document['services']['web']['image'] = 'nginx:stable'
    response = c.post('/api/docker/compose/convert', data={'content': source, 'document': json.dumps(document)})
    assert response.status_code == 200
    assert '# Project notes' in response.json()['content']
    assert 'image: "nginx:stable" # pinned by admin' in response.json()['content']


def test_adding_port_preserves_existing_yaml_comments(monkeypatch):
    c = client(monkeypatch)
    source = '# Project notes\nservices:\n  web:\n    image: nginx # image notes\n    ports:\n      - "8080:80" # existing port\n    restart: always\n'
    document = c.post('/api/docker/compose/convert', data={'content': source}).json()['document']
    document['services']['web']['ports'].append('8081:81')
    response = c.post('/api/docker/compose/convert', data={'content': source, 'document': json.dumps(document)})
    assert response.status_code == 200
    output = response.json()['content']
    assert '# Project notes' in output
    assert '# image notes' in output
    assert '# existing port' in output
    assert c.post('/api/docker/compose/convert', data={'content': output}).json()['document'] == document
