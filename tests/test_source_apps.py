from pathlib import Path
import json
import subprocess

import pytest
import yaml

from modules import apps


def git(directory, *args):
    result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path):
    origin = tmp_path / 'printvard.git'
    origin.mkdir()
    git(origin, 'init', '--bare', '--initial-branch=main')
    seed = tmp_path / 'seed'
    subprocess.run(['git', 'clone', str(origin), str(seed)], capture_output=True, check=True)
    git(seed, 'config', 'user.name', 'Deployment test')
    git(seed, 'config', 'user.email', 'deployment@example.invalid')
    (seed / 'Dockerfile').write_text('FROM scratch\n')
    git(seed, 'add', 'Dockerfile')
    git(seed, 'commit', '-m', 'Initial source')
    git(seed, 'push', 'origin', 'main')
    path = tmp_path / 'installation'
    path.mkdir()
    content = yaml.safe_dump({'name': 'printvard', 'x-runvard-source': {
        'repository': str(origin), 'branch': 'main', 'checkout': 'source',
        'service': 'printvard', 'image': 'printvard:local',
    }, 'services': {'printvard': {'image': 'printvard:local', 'build': {'context': './source'},
                                'volumes': ['data:/var/lib/printvard'], 'env_file': '.env'}},
        'volumes': {'data': {}}})
    return path, seed, content


def test_printvard_is_single_container_catalog_app():
    app = next((app for app in apps.CATALOG if app['id'] == 'printvard'), None)
    assert app is not None
    compose = yaml.safe_load(apps.build_compose(app))
    assert list(compose['services']) == ['printvard']
    assert compose['x-runvard-source']['branch'] == 'main'


def test_gitea_commit_change_is_detected_and_prepared_without_touching_data(project, monkeypatch):
    from modules import source_apps
    path, seed, content = project
    prepared = source_apps.prepare(str(path), content)
    before = git(seed, 'rev-parse', 'HEAD')
    monkeypatch.setattr(source_apps, '_docker_revision', lambda path, image: before)
    assert source_apps.check(str(path), prepared) is False
    (path / 'persistent-data').write_text('keep me')
    (seed / 'feature.php').write_text('<?php // A real update\n')
    git(seed, 'add', 'feature.php')
    git(seed, 'commit', '-m', 'Add feature')
    git(seed, 'push', 'origin', 'main')
    assert source_apps.check(str(path), prepared) is True
    updated = yaml.safe_load(source_apps.prepare(str(path), prepared, fetch=True))
    assert updated['services']['printvard']['build']['args']['PRINTVARD_BUILD_COMMIT'] == git(seed, 'rev-parse', 'HEAD')
    assert (path / 'persistent-data').read_text() == 'keep me'
    assert (path / '.env').stat().st_mode & 0o777 == 0o600
    assert updated['services']['printvard']['volumes'][0] == 'data:/var/lib/printvard'
    assert updated['services']['printvard']['volumes'][-1].endswith(':/run/printvard-updates:ro')
    state=json.loads((path / 'update-state' / 'state.json').read_text())
    assert state['latest_commit'] == git(seed, 'rev-parse', 'HEAD')
    assert state['comparison'] == 'available'


def test_dirty_checkout_is_never_overwritten(project):
    from modules import source_apps
    path, _, content = project
    source_apps.prepare(str(path), content)
    (path / 'source' / 'Dockerfile').write_text('local modification\n')
    with pytest.raises(RuntimeError, match='uncommitted'):
        source_apps.prepare(str(path), content, fetch=True)
    assert (path / 'source' / 'Dockerfile').read_text() == 'local modification\n'


def test_checkout_cannot_escape_app_directory(project):
    from modules import source_apps
    path, _, content = project
    data = yaml.safe_load(content)
    data['x-runvard-source']['checkout'] = '../escape'
    with pytest.raises(ValueError):
        source_apps.prepare(str(path), yaml.safe_dump(data))


def test_source_image_check_does_not_query_a_registry(project, monkeypatch):
    from modules import source_apps
    path, _, content = project
    (path / 'docker-compose.yml').write_text(content)
    monkeypatch.setattr(source_apps, 'check', lambda path, content: True)
    assert apps._check_image_update(str(path)) is True


def test_service_control_does_not_hide_runvard_after_the_eightieth_service():
    html=(Path(__file__).parents[1] / 'static' / 'index.html').read_text()
    function=html.split('async function renderServicesSimple(body){',1)[1].split('const renderSystemOriginalFallback',1)[0]
    script="""const rows=Array.from({length:80},(_,i)=>({name:'a'+i+'.service',active:'active'}));
rows.push({name:'runvard.service',active:'active'});
const api=async()=>rows, viewIntro=()=>'', esc=String, statusBadge=()=>'', isExpertMode=()=>true;
const body={innerHTML:''};
async function renderServicesSimple(body){"""+function+"\nrenderServicesSimple(body).then(()=>{if(!body.innerHTML.includes(\"svcAct('runvard.service','restart')\")) throw new Error('Runvard restart control is hidden');});"
    result=subprocess.run(['node','-e',script],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


def test_repository_trust_is_scoped_to_temporary_git_process_configuration(project, monkeypatch):
    from modules import source_apps
    path, _, content = project
    original=source_apps.subprocess.run
    files=[]
    def run(command, **kwargs):
        if command and command[0]=='git' and 'config' not in command:
            config=(kwargs.get('env') or {}).get('GIT_CONFIG_GLOBAL')
            files.append(config)
            if config:
                assert Path(config).is_file()
                assert '[safe]' in Path(config).read_text()
        return original(command, **kwargs)
    monkeypatch.setattr(source_apps.subprocess,'run',run)
    source_apps.prepare(str(path),content)
    assert files and all(files)
    assert all(not Path(file).exists() for file in files)


def test_targeted_update_check_preserves_other_apps_and_checks_only_printvard(tmp_path, monkeypatch):
    root=tmp_path/'printvard'; root.mkdir(); (root/'docker-compose.yml').write_text('services: {}\n')
    monkeypatch.setattr(apps,'APPS_DIR',str(tmp_path))
    monkeypatch.setattr(apps,'UPDATE_CACHE',str(tmp_path/'updates.json'))
    monkeypatch.setattr(apps,'_load_updates',lambda:{'checked':123,'updates':['other'],'errors':{'other':'keep'}})
    checked=[]
    monkeypatch.setattr(apps,'_check_image_update',lambda path: checked.append(path) or True)
    result=apps.check_updates(force=True,app_id='printvard')
    assert checked==[str(root)]
    assert set(result['updates'])=={'printvard','other'}
    assert result['errors']=={'other':'keep'}


def test_source_app_does_not_require_legacy_catalog_template():
    app = next(app for app in apps.CATALOG if app['id'] == 'printvard')
    content = apps.build_compose(app)
    assert apps._prepare_host_network_content(app, content) == content
