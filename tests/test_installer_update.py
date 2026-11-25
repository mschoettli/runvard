import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / 'scripts' / 'installer-settings.py'
COMMON = ROOT / 'scripts' / 'install-common.sh'


def settings(*args):
    return subprocess.run([sys.executable, str(SETTINGS), *map(str, args)],
                          capture_output=True, text=True, check=True).stdout


def test_existing_environment_is_merged_without_resetting_custom_settings(tmp_path):
    env_file = tmp_path / 'runvard.env'
    custom = '# Local settings\nRUNVARD_TRUSTED_PROXIES=10.0.0.5\nCUSTOM="two words"\n'
    env_file.write_text(custom + 'RUNVARD_LANG=de\nRUNVARD_PORT=8000\n'
                        'RUNVARD_DATA_DIR="/srv/local data"\nRUNVARD_USER=old\nRUNVARD_PASS="old value"\n')
    assert settings('get', env_file, 'RUNVARD_LANG').strip() == 'de'
    assert settings('get', env_file, 'RUNVARD_DATA_DIR').strip() == '/srv/local data'
    settings('merge', env_file, '8088', 'de', '/opt/runvard/data')
    updated = env_file.read_text()
    assert custom in updated
    assert 'RUNVARD_DATA_DIR="/srv/local data"' in updated
    assert 'RUNVARD_PASS' not in updated and 'RUNVARD_USER' not in updated
    assert 'RUNVARD_PORT=8088\n' in updated and '8000' not in updated
    assert env_file.stat().st_mode & 0o777 == 0o600
    settings('merge', env_file, '8088', 'de', '/opt/runvard/data')
    assert env_file.read_text() == updated


def test_systemd_values_and_linked_config_survive_update(tmp_path):
    target = tmp_path / 'site.env'
    env_file = tmp_path / 'runvard.env'
    env_file.symlink_to(target)
    custom = "LABEL=server's settings\nTEXT='multiple\nRUNVARD_PORT=inside a value\nlines'\n"
    target.write_text(custom + 'RUNVARD_PORT=8765\n')
    assert settings('get', env_file, 'RUNVARD_PORT').strip() == '8765'
    settings('merge', env_file, '8766', 'en', '/opt/runvard/data')
    assert env_file.is_symlink()
    assert custom in target.read_text() and 'RUNVARD_PORT=8766\n' in target.read_text()


def test_fresh_environment_has_only_managed_defaults(tmp_path):
    env_file = tmp_path / 'runvard.env'
    settings('merge', env_file, '8080', 'en', '/opt/runvard/data')
    lines = [line for line in env_file.read_text().splitlines() if not line.startswith('#')]
    assert set(lines) == {'RUNVARD_PORT=8080', 'RUNVARD_LANG=en', 'RUNVARD_DATA_DIR=/opt/runvard/data'}
    assert env_file.stat().st_mode & 0o777 == 0o600


def bash(common_function, *args, env=None):
    return subprocess.run(['bash', '-c', 'set -eu; source "$1"; shift; "$@"', 'bash',
                           str(COMMON), common_function, *map(str, args)], env=env,
                          capture_output=True, text=True)


@pytest.mark.parametrize('compatible', [True, False])
def test_pip_checks_local_resolution_before_selecting_install_mode(tmp_path, compatible):
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    (wheels / 'package.whl').touch()
    (tmp_path / 'requirements.txt').write_text('package==1.0\n')
    pip = tmp_path / 'pip'
    pip.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$0.args"\n' + ('exit 0\n' if compatible else 'exit 1\n'))
    pip.chmod(0o700)
    result = bash('bundled_wheels_ready', pip, tmp_path)
    assert (result.returncode == 0) == compatible
    args = (tmp_path / 'pip.args').read_text().splitlines()
    assert '--dry-run' in args and '--no-index' in args and '--find-links' in args
    assert str(wheels) in args and str(tmp_path / 'requirements.txt') in args


def test_custom_tls_unit_is_kept_and_legacy_default_uses_configured_port(tmp_path):
    template = ROOT / 'runvard.service'
    unit = tmp_path / 'runvard.service'
    custom = ('[Service]\n# Local TLS\nExecStart=/opt/runvard/venv/bin/uvicorn server:app '
              '--host 0.0.0.0 --port 8443 --workers 1 --ssl-keyfile /etc/local.key\nRestartSec=15\n')
    unit.write_text(custom)
    settings('service', unit, template, '8088', '8088')
    assert unit.read_text() == custom
    unit.write_text('[Service]\nExecStart=/opt/runvard/venv/bin/uvicorn server:app '
                    '--host 0.0.0.0 --port 8088 --workers 1\nRestartSec=15\n')
    settings('service', unit, template, '8088', '8088')
    assert '--port ${RUNVARD_PORT} --workers 1' in unit.read_text()
    assert 'RestartSec=15' in unit.read_text()
    unit.unlink()
    settings('service', unit, template, '8088', '8088')
    assert unit.read_text() == template.read_text()
    assert '--port ${RUNVARD_PORT}' in unit.read_text()


def test_local_healthcheck_accepts_an_https_only_service(tmp_path):
    curl = tmp_path / 'curl'
    curl.write_text("""#!/bin/sh
case "$*" in
  *https://127.0.0.1:8443/login*) printf '%s\\n200' '<title>Runvard</title><form id="login-form">';;
  *) exit 52;;
esac
""")
    curl.chmod(0o700)
    systemctl = tmp_path / 'systemctl'
    systemctl.write_text('#!/bin/sh\ncase "$1" in show) echo 99999999;; is-active) exit 0;; esac\n')
    systemctl.chmod(0o700)
    result = bash('probe_local_login', '8443', env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'})
    assert result.returncode == 0 and result.stdout.strip() == 'https 200 8443'

    curl.write_text('#!/bin/sh\nprintf "different service\\n200"\n')
    assert bash('probe_local_login', '8443', env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'}).returncode != 0
    systemctl.write_text('#!/bin/sh\necho 0\n')
    assert bash('probe_local_login', '8443', env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'}).returncode != 0


@pytest.mark.parametrize('ready,local_wheels', [(True, True), (True, False), (False, True)])
def test_update_records_only_a_ready_version_and_selects_the_right_packages(tmp_path, ready, local_wheels):
    checkout = tmp_path / 'release'
    checkout.mkdir()
    (checkout / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
    for folder in ('modules', 'static'):
        (checkout / folder).mkdir()
    for name in ('requirements.txt', 'server.py'):
        (checkout / name).touch()
    installation = tmp_path / 'installed'
    venv = installation / 'venv/bin'
    data = installation / 'data'
    for path in (venv, data, installation / 'wheels'):
        path.mkdir(parents=True)
    (venv / 'python').symlink_to(sys.executable)
    (data / 'runvard.env').write_text('RUNVARD_PORT=8765\n')
    (data / 'runvard.version').write_text('a' * 40 + '\n')
    (venv / 'pip').write_text('#!/bin/sh\necho "$*" >> "$PIP_LOG"\n'
                              'case "$*" in *--dry-run*) exit "$DRY_RUN";; esac\n')
    (venv / 'pip').chmod(0o700)
    fake_bin = tmp_path / 'commands'
    fake_bin.mkdir()
    for name, action in {
        'rsync': ':', 'systemctl': 'case "$1" in show) echo 99999999;; esac', 'journalctl': ':', 'sleep': ':',
        'id': 'printf 0', 'curl': """case "$*" in *http://127.0.0.1:8765/login*) printf '%s\\n%s' '<title>Runvard</title><form id="login-form">' "$STATUS";; esac""",
    }.items():
        executable = fake_bin / name
        executable.write_text('#!/bin/sh\n' + action + '\n')
        executable.chmod(0o700)
    updater = checkout / 'update.sh'
    updater.write_text((ROOT / 'update.sh').read_text().replace('INSTALL_DIR="/opt/runvard"',
                                                               f'INSTALL_DIR="{installation}"'))
    pip_log = tmp_path / 'pip.log'
    result = subprocess.run(['bash', str(updater)], capture_output=True, text=True, env={
        'PATH': f'{fake_bin}:{os.environ["PATH"]}', 'RUNVARD_SOURCE_COMMIT': 'b' * 40,
        'STATUS': '200' if ready else '503', 'DRY_RUN': '0' if local_wheels else '1',
        'PIP_LOG': str(pip_log),
    })
    assert (result.returncode == 0) == ready, result.stderr + result.stdout
    expected_commit = 'b' if ready else 'a'
    assert (data / 'runvard.version').read_text() == expected_commit * 40 + '\n'
    commands = pip_log.read_text().splitlines()
    assert '--dry-run' in commands[0]
    requirements, = (command for command in commands[1:] if 'requirements.txt' in command)
    assert ('--no-index' in requirements) == local_wheels


def test_systemd_double_quotes_decode_special_characters(tmp_path):
    env_file = tmp_path / 'runvard.env'
    env_file.write_text('RUNVARD_DATA_DIR="/srv/archive\\$/data\\\nbase"\n'
                        'RUNVARD_PASS="prefix\\`\\$suffix"\n')
    assert settings('get', env_file, 'RUNVARD_DATA_DIR').strip() == '/srv/archive$/database'
    assert settings('get', env_file, 'RUNVARD_PASS').strip() == 'prefix`$suffix'


def test_health_uses_the_actual_process_port(tmp_path):
    cmdline = tmp_path / 'cmdline'
    cmdline.write_bytes(b'/opt/runvard/venv/bin/uvicorn\0server:app\0--port\x008443\0')
    assert bash('uvicorn_port', cmdline, '8088').stdout.strip() == '8443'
    cmdline.write_bytes(b'/bin/start-service\0')
    assert bash('uvicorn_port', cmdline, '8088').stdout.strip() == '8088'


def test_sync_keeps_custom_data_and_links_and_removes_stale_program_files(tmp_path):
    source = tmp_path / 'release'
    installed = tmp_path / 'install'
    source.mkdir()
    installed.mkdir()
    custom = installed / 'operator [data]'
    custom.mkdir()
    (custom / 'users.json').write_text('old accounts')
    (installed / 'link').symlink_to(custom.name, target_is_directory=True)
    configured = installed / 'my settings'
    configured.symlink_to('link', target_is_directory=True)
    (source / 'server.py').write_text('new')
    (installed / 'old.py').write_text('stale')
    settings('sync', source, installed, configured)
    assert (configured / 'users.json').read_text() == 'old accounts'
    assert (installed / 'server.py').read_text() == 'new'
    assert not (installed / 'old.py').exists()
