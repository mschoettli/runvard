from pathlib import Path
import re
import subprocess

import pytest

from modules import system_mgr


@pytest.fixture
def unattended(monkeypatch, tmp_path):
    auto = tmp_path / '20auto-upgrades'
    dropin = tmp_path / '52runvard-unattended'
    monkeypatch.setattr(system_mgr, '_AUTO_UPGRADES', str(auto))
    monkeypatch.setattr(system_mgr, '_UU_DROPIN', str(dropin))
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return {'ok': True, 'stdout': 'install ok installed', 'stderr': ''}

    monkeypatch.setattr(system_mgr, '_run', run)
    return auto, dropin, calls


def test_enabling_automatic_updates_starts_daily_timers(unattended):
    auto, dropin, calls = unattended
    assert system_mgr.unattended_set(True)['ok']
    assert 'APT::Periodic::Unattended-Upgrade "1"' in auto.read_text()
    assert 'Unattended-Upgrade::Automatic-Reboot "false"' in dropin.read_text()
    assert ['systemctl', 'enable', '--now', 'apt-daily.timer', 'apt-daily-upgrade.timer'] in calls


def test_disabling_does_not_stop_shared_apt_timers(unattended):
    auto, _, calls = unattended
    assert system_mgr.unattended_set(False)['ok']
    assert 'Unattended-Upgrade "0"' in auto.read_text()
    assert not any(cmd[0] == 'systemctl' for cmd in calls)


def test_missing_package_cannot_be_enabled(unattended, monkeypatch):
    auto, dropin, _ = unattended
    monkeypatch.setattr(system_mgr, '_run', lambda cmd: {'ok': False, 'stdout': '', 'stderr': 'missing'})
    assert system_mgr.unattended_set(True)['ok'] is False
    assert not auto.exists()
    assert not dropin.exists()


def test_timer_failure_is_reported(unattended, monkeypatch):
    def run(cmd):
        if cmd[0] == 'systemctl':
            return {'ok': False, 'stdout': '', 'stderr': 'timer masked'}
        return {'ok': True, 'stdout': 'install ok installed', 'stderr': ''}
    monkeypatch.setattr(system_mgr, '_run', run)
    result = system_mgr.unattended_set(True)
    assert result['ok'] is False
    assert result['stderr'] == 'timer masked'


@pytest.mark.parametrize('value', ['25:00', '12:60', '02:00"; bad', ''])
def test_invalid_reboot_time_does_not_write(unattended, value):
    auto, dropin, _ = unattended
    assert system_mgr.unattended_set(True, True, value)['ok'] is False
    assert not auto.exists()
    assert not dropin.exists()


def test_updates_page_renders_automatic_controls_and_saves_in_place():
    html = Path('static/index.html').read_text()
    update_view = html.split('const renderSystemOriginalFallback=')[1].split("} else if(tab===")[0]
    assert "api('/sysmgr/unattended')" in update_view
    assert "automaticUpdatesCard(uu,'Updates')" in update_view
    helper = re.search(r'function automaticUpdatesCard\(.*?\n\}', html, re.S)
    assert helper
    # Execute real rendering and save handler without running the rest of the app.
    save = re.search(r'window.uuSave=async.*?^};', html, re.M | re.S)
    assert save
    script = """
const assert = require('node:assert/strict');
const window = globalThis;
const uiText = s => s;
const esc = s => String(s).replaceAll('"', '&quot;');
const fields = {'#uu-enable':{checked:true}, '#uu-reboot':{checked:false}, '#uu-time':{value:'03:15'}, '#uu-save':{}, '#modal-body':{}};
const $ = s => fields[s];
let saved, tab;
const post = async (path, data) => {saved={path,data};return {ok:true};};
const toast = () => {};
const renderSystem = async (body, value) => {tab=value;};
""" + helper.group(0) + '\n' + save.group(0) + r"""
(async () => {
  const installed = automaticUpdatesCard({installed:true,enabled:true,auto_reboot:false,reboot_time:'02:00'},'Updates');
  assert.match(installed, /id="uu-enable" checked/);
  assert.match(installed, /uuSave\('Updates'\)/);
  assert.match(automaticUpdatesCard({installed:false},'Updates'), /uuInstall\('Updates'\)/);
  await uuSave('Updates');
  assert.equal(tab,'Updates');
  assert.deepEqual(saved,{path:'/sysmgr/unattended/set',data:{enable:'true',auto_reboot:'false',reboot_time:'03:15'}});
  assert.equal(fields['#uu-save'].disabled,false);
})().catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


def test_commented_reboot_setting_is_not_treated_as_enabled(tmp_path):
    config = tmp_path / '50unattended-upgrades'
    config.write_text('// Unattended-Upgrade::Automatic-Reboot "true";\n'
                      '/* Unattended-Upgrade::Automatic-Reboot "true"; */\n'
                      'Unattended-Upgrade::Automatic-Reboot "false";\n')
    assert system_mgr._conf_flag(str(config), 'Unattended-Upgrade::Automatic-Reboot') == 'false'


def test_package_install_waits_for_job_and_keeps_updates_tab():
    html = Path('static/index.html').read_text()
    install = re.search(r'window.uuInstall=async.*?^};', html, re.M | re.S)
    assert install
    script = """
const assert=require('node:assert/strict');
const window=globalThis;
const uiText=s=>s;
const toast=()=>{};
const button={};
const $=s=>s==='#uu-install'?button:{};
let polls=0,tab;
const post=async(path,data)=>{assert.equal(data.name,'unattended-upgrades');return {job_id:'install-job'};};
const api=async(path)=>{
  assert.equal(path,'/sysmgr/job?id=install-job');
  return ++polls===1?{status:'running'}:{status:'succeeded',result:{ok:true}};
};
const renderSystem=async(body,value)=>{tab=value;};
const setTimeout=fn=>fn();
""" + install.group(0) + """
(async()=>{
  await uuInstall('Updates');
  assert.equal(polls,2);
  assert.equal(tab,'Updates');
  assert.equal(button.disabled,false);
})().catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
