"""Opt-in, single-container source deployments managed by Runvard."""
import base64
from datetime import datetime, timezone
import tempfile
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

import yaml


def enabled(content):
    data = yaml.safe_load(content) or {}
    return isinstance(data, dict) and isinstance(data.get('x-runvard-source'), dict)


def configuration(path, content):
    data = yaml.safe_load(content) or {}
    source = data.get('x-runvard-source') or {}
    root = Path(path).resolve()
    relative = source.get('checkout', 'source')
    if not isinstance(relative, str) or relative in ('', '.', '..') or Path(relative).is_absolute():
        raise ValueError('Invalid source checkout')
    checkout = (root / relative).resolve()
    if root not in checkout.parents or (root / relative).is_symlink():
        raise ValueError('Source checkout must remain inside the app directory')
    repository = source.get('repository', '')
    if not isinstance(repository, str) or not Path(repository).is_absolute():
        raise ValueError('Source repository must be an absolute local Gitea repository path')
    branch = source.get('branch', 'main')
    if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,239}', branch) or '..' in branch:
        raise ValueError('Invalid source branch')
    service = source.get('service', 'printvard')
    services = data.get('services', {})
    if list(services) != [service] or not isinstance(services.get(service), dict):
        raise ValueError('Source deployments require exactly one app service')
    image = source.get('image', '')
    if not image or services[service].get('image') != image:
        raise ValueError('Source image does not match the app service')
    return data, source, checkout, Path(repository).resolve(), branch, service, image


def _run(command, cwd=None, timeout=120, env=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Source deployment command failed').strip()[-1800:])
    return result.stdout.strip()


def _git(repository, *arguments, cwd=None):
    # Local Git upload-pack removes command-line config from its environment.
    # A temporary process-scoped file also applies to that child, without changing global Git settings.
    with tempfile.NamedTemporaryFile(mode='w', prefix='runvard-git-', suffix='.conf') as config:
        config.write('[safe]\n')
        for directory in dict.fromkeys([str(repository), str(cwd) if cwd else str(repository)]):
            config.write('\tdirectory = ' + json.dumps(directory) + '\n')
        config.flush()
        env = dict(os.environ, GIT_CONFIG_GLOBAL=config.name)
        return _run(['git', '-c', 'protocol.file.allow=always', '-c', 'core.hooksPath=/dev/null',
                     *arguments], cwd=cwd, env=env)


def _head(repository, branch):
    output = _git(repository, 'ls-remote', str(repository), 'refs/heads/' + branch)
    revision = output.split()[0] if output else ''
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('Gitea branch revision is unavailable')
    return revision


def _docker_revision(path, image):
    identifiers = _run(['docker', 'compose', 'ps', '-a', '-q'], cwd=path).splitlines()
    if len(identifiers) > 1:
        raise RuntimeError('More than one app container exists')
    if identifiers:
        revision = _run(['docker', 'inspect', identifiers[0], '--format',
                         '{{index .Config.Labels "org.opencontainers.image.revision"}}'], cwd=path)
    else:
        revision = _run(['docker', 'image', 'inspect', image, '--format',
                         '{{index .Config.Labels "org.opencontainers.image.revision"}}'], cwd=path)
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('Installed image revision is unavailable')
    return revision


def _comparison(repository, installed, latest):
    if not installed:
        return 'pending', 0
    if installed == latest:
        return 'current', 0
    def ancestor(base, head):
        result = subprocess.run(['git', '-c', 'safe.directory=' + str(repository), '--git-dir', str(repository),
                                 'merge-base', '--is-ancestor', base, head], capture_output=True, text=True, timeout=30)
        return result.returncode
    forward, backward = ancestor(installed, latest), ancestor(latest, installed)
    if forward == 0:
        count = int(_git(repository, '--git-dir', str(repository), 'rev-list', '--count', installed + '..' + latest))
        return 'available', count
    if backward == 0:
        return 'ahead', 0
    return ('diverged', 0) if forward == backward == 1 else ('unknown', 0)


def _record_state(path, repository, latest=None, installed=None, error=None):
    directory = Path(path) / 'update-state'
    if directory.is_symlink():
        raise ValueError('Update state directory must not be a symlink')
    directory.mkdir(mode=0o755, exist_ok=True)
    target = directory / 'state.json'
    try:
        previous = json.loads(target.read_text())
    except (OSError, ValueError):
        previous = {}
    installed = installed or previous.get('installed_commit')
    latest = latest or previous.get('latest_commit')
    comparison, count = _comparison(repository, installed, latest) if latest and not error else (previous.get('comparison', 'pending'), previous.get('commits_ahead', 0))
    now = datetime.now(timezone.utc).isoformat()
    data = {'installed_commit': installed, 'latest_commit': latest, 'comparison': comparison,
            'commits_ahead': count, 'checked_at': now,
            'succeeded_at': previous.get('succeeded_at') if error else now, 'error': error}
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return data


def check(path, content):
    _, _, _, repository, branch, _, image = configuration(path, content)
    try:
        latest = _head(repository, branch)
        installed = _docker_revision(path, image)
        state = _record_state(path, repository, latest, installed)
        return state['comparison'] == 'available'
    except Exception:
        _record_state(path, repository, error='connection')
        raise


def prepare(path, content, fetch=False):
    data, _, checkout, repository, branch, service, _ = configuration(path, content)
    if not (checkout / '.git').is_dir():
        if checkout.exists() and any(checkout.iterdir()):
            raise RuntimeError('Source directory is not an empty Git checkout')
        _git(repository, 'clone', '--no-hardlinks', '--single-branch', '--branch', branch, str(repository), str(checkout))
    if _git(repository, 'status', '--porcelain', '--untracked-files=normal', cwd=checkout):
        raise RuntimeError('Source checkout contains uncommitted changes; refusing to overwrite them')
    origin = _git(repository, 'remote', 'get-url', 'origin', cwd=checkout)
    if Path(origin).resolve() != repository:
        raise RuntimeError('Source origin no longer matches the configured Gitea repository')
    if fetch:
        _git(repository, 'fetch', '--no-tags', 'origin', 'refs/heads/' + branch, cwd=checkout)
        _git(repository, 'merge', '--ff-only', 'FETCH_HEAD', cwd=checkout)
    revision = _git(repository, 'rev-parse', '--verify', 'HEAD^{commit}', cwd=checkout)
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('Invalid source revision')
    build = data['services'][service].get('build') or {}
    if isinstance(build, str):
        build = {'context': build}
    build['context'] = str(checkout)
    build.setdefault('args', {})['PRINTVARD_BUILD_COMMIT'] = revision
    data['services'][service]['build'] = build
    env_file = Path(path) / '.env'
    if env_file.is_symlink():
        raise ValueError('Runtime environment must not be a symlink')
    if not env_file.exists():
        key = base64.b64encode(secrets.token_bytes(32)).decode()
        with os.fdopen(os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            stream.write('APP_NAME=Printvard\nAPP_KEY=base64:' + key + '\nAPP_LOCALE=de\nAPP_URL=http://192.168.178.60:8784\nPRINTVARD_RUNVARD_URL=' + os.environ.get('RUNVARD_PUBLIC_URL', 'http://192.168.178.60:8080') + '\n')
    _record_state(path, repository, latest=_head(repository, branch))
    service_data = data['services'][service]
    service_data.setdefault('environment', {})['PRINTVARD_RUNVARD_STATE'] = '/run/printvard-updates/state.json'
    mount = str(Path(path).resolve() / 'update-state') + ':/run/printvard-updates:ro'
    volumes = service_data.setdefault('volumes', [])
    if mount not in volumes: volumes.append(mount)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def wait_healthy(path, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        identifiers = _run(['docker', 'compose', 'ps', '-a', '-q'], cwd=path).splitlines()
        if len(identifiers) != 1:
            raise RuntimeError('Expected exactly one Printvard container')
        state = json.loads(_run(['docker', 'inspect', identifiers[0], '--format', '{{json .State}}'], cwd=path))
        health = (state.get('Health') or {}).get('Status')
        if state.get('Running') and health == 'healthy':
            return
        if not state.get('Running') or health == 'unhealthy':
            raise RuntimeError('Printvard failed its startup health check')
        time.sleep(1)
    raise RuntimeError('Printvard startup health check timed out')


def draft():
    repository = os.environ.get('RUNVARD_PRINTVARD_REPOSITORY', '/srv/appdata/gitea/data/git/repositories/mschoettli/printvard.git')
    data = {
        'name': 'printvard',
        'x-runvard-source': {'repository': repository, 'branch': 'main', 'checkout': 'source', 'service': 'printvard', 'image': 'printvard:local'},
        'services': {'printvard': {
            'image': 'printvard:local', 'container_name': 'printvard', 'pull_policy': 'never',
            'build': {'context': './source'}, 'restart': 'unless-stopped', 'stop_grace_period': '150s',
            'env_file': '.env',
            'environment': {'APP_ENV': 'production', 'APP_DEBUG': 'false', 'PRINTVARD_SINGLE_CONTAINER': 'true',
                            'DB_CONNECTION': 'sqlite', 'DB_DATABASE': '/var/lib/printvard/database.sqlite',
                            'QUEUE_CONNECTION': 'database', 'CACHE_STORE': 'database', 'SESSION_DRIVER': 'database',
                            'LOG_CHANNEL': 'stderr', 'PRINTVARD_LIBRARY_ROOT': '/var/lib/printvard/library',
                            'PRINTVARD_ARCHIVE_ROOT': '/var/lib/printvard/versions', 'PRINTVARD_CACHE_ROOT': '/var/lib/printvard/previews'},
            'ports': ['8784:8080'], 'volumes': ['data:/var/lib/printvard', 'storage:/var/www/html/storage'],
            'security_opt': ['no-new-privileges:true'],
            'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
        }},
        'volumes': {'data': {}, 'storage': {}},
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True), None
