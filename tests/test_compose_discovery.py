import json
from types import SimpleNamespace

import pytest
import yaml

from modules import docker_mgr


def install_containers(monkeypatch, tmp_path, items):
    monkeypatch.setattr(docker_mgr, 'COMPOSE_DIR', str(tmp_path / 'managed'))
    def list_containers(**kwargs):
        label = kwargs.get('filters', {}).get('label')
        if label:
            key, value = label.split('=', 1)
            return [item for item in items if item.attrs['Config']['Labels'].get(key) == value]
        return items
    client = SimpleNamespace(containers=SimpleNamespace(list=list_containers))
    monkeypatch.setattr(docker_mgr, '_get_client', lambda: client)


def container(name, project='', files='', directory=''):
    labels = {'com.docker.compose.project': project,
              'com.docker.compose.service': name,
              'com.docker.compose.project.config_files': files,
              'com.docker.compose.project.working_dir': directory}
    return SimpleNamespace(name=name, attrs={
        'Config': {'Labels': labels, 'Image': 'nginx:alpine', 'Env': ['A=x=y'],
                   'Cmd': ['nginx', '-g', 'daemon off;']},
        'HostConfig': {'RestartPolicy': {'Name': 'always'},
                       'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8080'}]}},
        'Mounts': [{'Type': 'bind', 'Source': '/srv/web', 'Destination': '/usr/share/nginx/html', 'RW': False}],
        'State': {'Running': True},
    })


def test_external_compose_loads_actual_source_and_override(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    override = tmp_path / 'compose.override.yaml'
    base.write_text('services:\n  web:\n    image: nginx\n    environment:\n      A: hello\n')
    override.write_text('services:\n  web:\n    ports: ["8080:80"]\n')
    (tmp_path / '.env').write_text('TOKEN=example\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', f'{base},{override}', str(tmp_path))])
    result = docker_mgr.get_compose('demo')
    assert result['content'] == base.read_text()
    assert [source['content'] for source in result['sources']] == [base.read_text(), override.read_text()]
    assert result['env_content'] == 'TOKEN=example\n'
    assert result['sources'][0]['document']['services']['web']['environment'] == {'A': 'hello'}


def test_unknown_project_errors_instead_of_empty_draft(monkeypatch, tmp_path):
    install_containers(monkeypatch, tmp_path, [])
    with pytest.raises(ValueError):
        docker_mgr.get_compose('missing')


def test_unreadable_source_is_not_replaced_by_incomplete_runtime_draft(monkeypatch, tmp_path):
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', str(tmp_path / 'missing.yml'))])
    with pytest.raises(ValueError):
        docker_mgr.get_compose('demo')


def test_standalone_container_is_reconstructed(monkeypatch, tmp_path):
    install_containers(monkeypatch, tmp_path, [container('web')])
    data = yaml.safe_load(docker_mgr.get_compose('container:web')['content'])
    service = data['services']['web']
    assert service['image'] == 'nginx:alpine'
    assert service['environment'] == ['A=x=y']
    assert service['ports'] == ['127.0.0.1:8080:80/tcp']
    assert service['volumes'][0]['read_only'] is True
    assert service['command'] == ['nginx', '-g', 'daemon off;']


def test_saving_external_files_keeps_original_paths_and_overrides(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    override = tmp_path / 'compose.override.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    override.write_text('services: {web: {restart: always}}\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', f'{base},{override}', str(tmp_path))])
    edited = 'services: {web: {image: "nginx:alpine"}}\n'
    docker_mgr.save_compose('demo', edited, source_contents=[edited, override.read_text()])
    assert base.read_text() == edited
    assert override.read_text() == 'services: {web: {restart: always}}\n'
    assert not (tmp_path / 'managed' / 'demo').exists()


def test_discovery_includes_external_projects(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', str(base), str(tmp_path))])
    monkeypatch.setattr(docker_mgr, '_compose_running', lambda path: True)
    assert any(p['name'] == 'demo' for p in docker_mgr.list_compose_projects())


def test_structured_conversion_preserves_nested_fields_and_types():
    document = {'services': {'web': {'image': 'nginx', 'environment': {'FLAG': 'false', 'N': '01'},
                    'healthcheck': {'test': ['CMD', 'true'], 'disable': False},
                    'deploy': {'resources': {'limits': {'cpus': '0.5'}}}},
                    'db': {'image': 'postgres'}}, 'volumes': {'db': None}}
    result = docker_mgr.convert_compose(document=json.dumps(document))
    assert yaml.safe_load(result['content']) == document
    assert docker_mgr.convert_compose(content=result['content'])['document'] == document


def test_external_actions_use_original_files_and_project_name(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', str(base), str(tmp_path))])
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(docker_mgr.subprocess, 'run', run)
    assert docker_mgr.compose_action('demo', 'restart')['ok']
    assert calls[0][0] == ['docker', 'compose', '-p', 'demo', '-f', str(base), 'restart']
    assert calls[0][1]['cwd'] == str(tmp_path)


def test_source_contents_count_must_match_before_any_write(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    override = tmp_path / 'compose.override.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    override.write_text('services: {web: {restart: always}}\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', f'{base},{override}', str(tmp_path))])
    with pytest.raises(ValueError):
        docker_mgr.save_compose('demo', 'services: {}', source_contents=['services: {}'])
    assert base.read_text() == 'services: {web: {image: nginx}}\n'


def test_top_level_only_override_can_be_edited(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    override = tmp_path / 'compose.override.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    override.write_text('networks: {default: {external: true, name: lan}}\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', f'{base},{override}', str(tmp_path))])
    result = docker_mgr.get_compose('demo')
    assert result['sources'][1]['document']['networks']['default']['external'] is True


def test_catalog_handles_broken_external_source_without_hiding_all_apps(monkeypatch, tmp_path):
    from modules import apps
    monkeypatch.setattr(apps, 'APPS_DIR', str(tmp_path / 'apps'))
    monkeypatch.setattr(apps.docker_mgr, 'list_compose_projects', lambda: [{'name': 'demo'}])
    def missing(name):
        raise ValueError('missing source')
    monkeypatch.setattr(apps.docker_mgr, 'get_compose', missing)
    assert any(app['id'] == 'compose:demo' for app in apps.get_catalog()['apps'])


def test_catalog_does_not_duplicate_installed_apps(monkeypatch, tmp_path):
    from modules import apps
    path = tmp_path / 'apps' / 'nextcloud'
    path.mkdir(parents=True)
    (path / 'docker-compose.yml').write_text('services: {nextcloud: {image: nextcloud}}')
    monkeypatch.setattr(apps, 'APPS_DIR', str(tmp_path / 'apps'))
    monkeypatch.setattr(apps, '_running', lambda name: True)
    monkeypatch.setattr(apps.docker_mgr, 'list_compose_projects', lambda: [{'name': 'nextcloud'}])
    monkeypatch.setattr(apps.docker_mgr, 'get_compose', lambda name: {'content': 'services: {}'})
    ids = [app['id'] for app in apps.get_catalog()['apps']]
    assert 'nextcloud' in ids
    assert 'compose:nextcloud' not in ids


def test_port_validation_uses_effective_override_configuration(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    base.write_text('services: {web: {image: nginx}}')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', str(base), str(tmp_path))])
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[-3:] == ['config', '--format', 'json']
        assert command.count('-f') == 2
        assert command[command.index('--project-directory') + 1] == str(tmp_path)
        env_file = command[command.index('--env-file') + 1]
        assert open(env_file).read() == 'HTTP_PORT=9090\n'
        return SimpleNamespace(returncode=0, stdout='{"services":{"web":{"ports":[{"target":80,"published":"9090"}]}}}', stderr='')
    monkeypatch.setattr(docker_mgr.subprocess, 'run', run)
    monkeypatch.setattr(docker_mgr, '_compose_owned_ports', lambda name: set())
    monkeypatch.setattr(docker_mgr, '_port_is_available', lambda port, *args: port != 9090)
    result = docker_mgr.check_compose_source_ports('demo', [base.read_text(), 'services: {web: {ports: ["${HTTP_PORT}:80"]}}'], True, 'HTTP_PORT=9090\n')
    assert result['ok'] is False
    assert result['conflicts'][0]['port'] == 9090
    assert len(calls) == 1


def test_standalone_existing_ports_are_owned(monkeypatch, tmp_path):
    c = container('web')
    c.attrs['NetworkSettings'] = {'Ports': {'80/tcp': [{'HostPort': '8080'}]}}
    install_containers(monkeypatch, tmp_path, [c])
    assert docker_mgr._compose_owned_ports('container:web') == {(8080, 'tcp', '0.0.0.0')}


def test_runtime_tmpfs_is_valid_compose_list(monkeypatch, tmp_path):
    c = container('web')
    c.attrs['HostConfig']['Tmpfs'] = {'/run': 'rw,size=65536k'}
    install_containers(monkeypatch, tmp_path, [c])
    document = docker_mgr.get_compose('container:web')['sources'][0]['document']
    assert document['services']['web']['tmpfs'] == ['/run:rw,size=65536k']


def test_save_does_not_touch_first_file_if_other_source_cannot_be_read(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    invalid = tmp_path / 'override.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    invalid.mkdir()
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', f'{base},{invalid}', str(tmp_path))])
    with pytest.raises(ValueError):
        docker_mgr.save_compose('demo', 'services: {}', source_contents=['services: {}', 'services: {}'])
    assert base.read_text() == 'services: {web: {image: nginx}}\n'


def test_saving_external_files_preserves_file_permissions(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    base.write_text('services: {web: {image: nginx}}\n')
    base.chmod(0o640)
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', str(base), str(tmp_path))])
    docker_mgr.save_compose('demo', 'services: {web: {image: "nginx:alpine"}}\n')
    assert base.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize('tag', ['!override', '!reset'])
def test_compose_yaml_tags_survive_form_roundtrip(tag):
    content = f'services:\n  web:\n    image: nginx\n    ports: {tag}\n      - "9090:80"\n'
    parsed = docker_mgr.convert_compose(content=content)
    assert parsed['document']['services']['web']['ports'] == ['9090:80']
    assert parsed['tags'] == [{'path': ['services', 'web', 'ports'], 'tag': tag}]
    parsed['document']['services']['web']['image'] = 'nginx:alpine'
    updated = docker_mgr.convert_compose(document=json.dumps(parsed['document']),
                                          yaml_tags=json.dumps(parsed['tags']))
    assert tag in updated['content']
    reparsed = docker_mgr.convert_compose(content=updated['content'])
    assert reparsed['tags'] == parsed['tags']
    assert reparsed['document']['services']['web']['image'] == 'nginx:alpine'


def test_get_compose_returns_override_tag_metadata(monkeypatch, tmp_path):
    base = tmp_path / 'compose.yaml'
    base.write_text('services:\n  web:\n    image: nginx\n    ports: !override ["9090:80"]\n')
    install_containers(monkeypatch, tmp_path, [container('web', 'demo', str(base), str(tmp_path))])
    data = docker_mgr.get_compose('demo')
    assert data['sources'][0]['tags'] == [{'path': ['services', 'web', 'ports'], 'tag': '!override'}]


@pytest.mark.parametrize('value', ['null', 'false', '42', '"42"'])
def test_tagged_scalar_types_survive_form_roundtrip(value):
    parsed = docker_mgr.convert_compose(content=f'services:\n  web:\n    ports: !reset {value}\n')
    serialized = docker_mgr.convert_compose(document=json.dumps(parsed['document']), yaml_tags=json.dumps(parsed['tags']))
    reloaded = docker_mgr.convert_compose(content=serialized['content'])
    assert reloaded['document'] == parsed['document']


def test_catalog_uses_live_compose_web_bindings_without_reading_external_files(monkeypatch, tmp_path):
    from modules import apps
    database = container('db', 'db-only')
    database.attrs['HostConfig']['PortBindings'] = {'5432/tcp': [{'HostPort': '18080'}]}
    api = container('web', 'demo')
    api.attrs['HostConfig']['PortBindings'] = {'443/tcp': [{'HostPort': ''}]}
    api.attrs['NetworkSettings'] = {'Ports': {'443/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '49000'}]}}
    install_containers(monkeypatch, tmp_path, [database, api])
    monkeypatch.setattr(apps, 'APPS_DIR', str(tmp_path / 'apps'))

    catalog = {entry['id']: entry for entry in apps.get_catalog()['apps']}
    db = catalog['compose:db-only']
    assert db['running'] and db['port'] == 18080 and db['web_scheme'] == ''
    web = catalog['compose:demo']
    assert web['running'] and web['port'] == 49000 and web['web_scheme'] == 'https'
    details = apps.get_app('compose:demo')
    assert (details['port'], details['web_scheme']) == (web['port'], web['web_scheme'])


@pytest.mark.parametrize("address,expected_scheme", [("127.0.0.1", ""), ("0.0.0.0", "https")])
def test_compose_details_use_same_runtime_endpoint_as_catalog_with_static_source(monkeypatch, tmp_path, address, expected_scheme):
    from modules import apps
    source = tmp_path / 'compose.yaml'
    source.write_text('services: {web: {image: nginx, ports: ["8080:80"]}}')
    web = container('web', 'demo', str(source), str(tmp_path))
    web.attrs['NetworkSettings'] = {'Ports': {'443/tcp': [{'HostIp': address, 'HostPort': '49000'}]}}
    install_containers(monkeypatch, tmp_path, [web])
    monkeypatch.setattr(apps, 'APPS_DIR', str(tmp_path / 'apps'))

    listing = next(entry for entry in apps.get_catalog()['apps'] if entry['id'] == 'compose:demo')
    details = apps.get_app('compose:demo')
    assert details['compose'] == source.read_text()
    for item in (listing, details):
        assert (item['port'], item['web_scheme']) == (49000, expected_scheme)
