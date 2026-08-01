from pathlib import Path
import re
import subprocess

from fastapi.testclient import TestClient

import server
from modules import apps, jobs


def _client(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: False)
    return TestClient(server.app)


def test_app_update_starts_background_job(monkeypatch):
    started = {}

    def fake_start_job(name, func, *args):
        started.update(name=name, func=func, args=args)
        return {"ok": True, "job_id": "app-update-job"}

    monkeypatch.setattr(jobs, "start_job", fake_start_job)
    monkeypatch.setattr(
        apps,
        "action",
        lambda app_id, action: (_ for _ in ()).throw(
            AssertionError("update ran inside the request")
        ),
    )

    response = _client(monkeypatch).post(
        "/api/apps/action",
        data={"app_id": "papervard", "action": "update"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "job_id": "app-update-job"}
    assert started == {
        "name": "app-update:papervard",
        "func": apps.action,
        "args": ("papervard", "update"),
    }


def test_app_update_job_status_is_available(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "get_job",
        lambda job_id: {
            "id": job_id,
            "status": "succeeded",
            "result": {"ok": True, "output": "updated"},
            "error": None,
        },
    )

    response = _client(monkeypatch).get(
        "/api/apps/action-job?id=app-update-job",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["result"]["ok"] is True


def test_non_update_app_action_remains_synchronous(monkeypatch):
    monkeypatch.setattr(
        apps,
        "action",
        lambda app_id, action: {
            "ok": True,
            "output": f"{app_id}:{action}",
        },
    )

    response = _client(monkeypatch).post(
        "/api/apps/action",
        data={"app_id": "papervard", "action": "restart"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "output": "papervard:restart",
    }


def test_app_store_polls_update_job_and_labels_running_state():
    html = Path("static/index.html").read_text()

    assert "async function waitForAppActionJob(jobId)" in html
    assert "api('/apps/action-job?id='+encodeURIComponent(jobId))" in html
    assert "r=await waitForAppActionJob(r.job_id)" in html
    assert "updating:{de:'Wird aktualisiert…'" in html


def test_app_update_indicator_tracks_running_and_terminal_states():
    html = Path("static/index.html").read_text()
    helpers = []
    for name, args in (
        ("startAppUpdateIndicator", "appId,appName"),
        ("advanceAppUpdateIndicator", "tracker"),
        ("finishAppUpdateIndicator", "tracker,ok,detail=''"),
    ):
        match = re.search(
            rf"function {name}\({re.escape(args)}\)\{{.*?^\}}",
            html,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert match
        helpers.append(match.group(0))

    script = f"""
function makeIndicator() {{
  const classes = new Set();
  const nodes = new Map();
  return {{
    className: '',
    title: '',
    removed: false,
    _innerHTML: '',
    classList: {{add: value => classes.add(value), contains: value => classes.has(value)}},
    set innerHTML(value) {{ this._innerHTML = value; }},
    get innerHTML() {{ return this._innerHTML; }},
    querySelector(selector) {{
      if (!nodes.has(selector)) nodes.set(selector, {{textContent: '', style: {{}}}});
      return nodes.get(selector);
    }},
    remove() {{ this.removed = true; }},
  }};
}}
const inserted = [];
const host = {{firstChild: null, insertBefore: indicator => inserted.push(indicator)}};
const document = {{createElement: () => makeIndicator()}};
const ensureInstallIndicatorHost = () => host;
const esc = value => String(value);
const appUi = key => key;
let _activeInstalls = 0;
let badgeUpdates = 0;
const updateAppsBadge = () => {{ badgeUpdates += 1; }};
globalThis.setTimeout = fn => fn();
{''.join(helpers)}

const success = startAppUpdateIndicator('demo', 'Demo App');
if (_activeInstalls !== 1 || inserted.length !== 1) throw new Error('Update indicator did not start');
if (!success.indicator.innerHTML.includes('Demo App')) throw new Error('App name missing');
const before = Number.parseFloat(success.indicator.querySelector('.install-bar-fill').style.width);
advanceAppUpdateIndicator(success);
const after = Number.parseFloat(success.indicator.querySelector('.install-bar-fill').style.width);
if (!(after > before)) throw new Error('Progress did not advance');
finishAppUpdateIndicator(success, true);
if (_activeInstalls !== 0 || !success.indicator.classList.contains('done') || !success.indicator.removed) {{
  throw new Error('Successful update indicator did not finish');
}}

const failure = startAppUpdateIndicator('demo-2', 'Broken App');
finishAppUpdateIndicator(failure, false, 'pull failed');
if (_activeInstalls !== 0 || !failure.indicator.classList.contains('error')) {{
  throw new Error('Failed update indicator did not finish');
}}
if (failure.indicator.title !== 'pull failed' || !failure.indicator.removed) {{
  throw new Error('Failed update details were not preserved');
}}
if (badgeUpdates !== 4) throw new Error(`Expected four badge updates, got ${{badgeUpdates}}`);
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_app_update_polling_recovers_after_transient_status_failure():
    html = Path("static/index.html").read_text()
    polling = re.search(
        r"async function waitForAppActionJob\(jobId\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert polling

    script = f"""
let attempts = 0;
const api = async () => {{
  attempts += 1;
  if (attempts === 1) throw new Error('temporary disconnect');
  return {{status:'succeeded', result:{{ok:true, output:'updated'}}}};
}};
globalThis.setTimeout = fn => fn();
{polling.group(0)}

(async () => {{
  const result = await waitForAppActionJob('app-update-job');
  if (attempts !== 2) throw new Error(`Expected polling retry, got ${{attempts}} attempt(s)`);
  if (!result.ok || result.output !== 'updated') {{
    throw new Error(`Expected successful update result, got ${{JSON.stringify(result)}}`);
  }}
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_app_update_polling_surfaces_missing_job():
    html = Path("static/index.html").read_text()
    polling = re.search(
        r"async function waitForAppActionJob\(jobId\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert polling

    script = f"""
let attempts = 0;
const api = async () => {{
  attempts += 1;
  const error = new Error('Job not found');
  error.status = 404;
  throw error;
}};
globalThis.setTimeout = () => {{ throw new Error('Unexpected retry'); }};
{polling.group(0)}

(async () => {{
  try {{
    await waitForAppActionJob('missing-job');
    throw new Error('Expected missing job to fail');
  }} catch (error) {{
    if (error.message !== 'Job not found') throw error;
    if (attempts !== 1) throw new Error(`Expected one attempt, got ${{attempts}}`);
  }}
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_system_update_polling_recovers_after_transient_status_failure():
    html = Path("static/index.html").read_text()
    button_state = re.search(
        r"function setUpdateButtonState\(state\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )
    polling = re.search(
        r"function pollUpdateJob\(id,reboot\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert button_state
    assert polling

    script = f"""
const button = {{
  disabled: false,
  textContent: '',
  removeAttribute() {{}},
  setAttribute() {{}},
  onclick: null,
}};
const output = {{textContent: ''}};
const $ = selector => selector === '#upd-go' ? button : output;
const uiText = text => text;
const toast = () => {{}};
const refreshSystemUpdateBadge = async () => {{}};
let _systemUpdateBadgeNext = 0;
let attempts = 0;
const api = async () => {{
  attempts += 1;
  if (attempts === 1) throw new Error('temporary disconnect');
  return {{status:'succeeded', result:{{ok:true, stdout:'updated', stderr:''}}}};
}};
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = fn => realSetTimeout(fn, 0);
{button_state.group(0)}
{polling.group(0)}

setUpdateButtonState('running');
pollUpdateJob('system-update-job', false);
realSetTimeout(() => {{
  if (attempts !== 2) throw new Error(`Expected polling retry, got ${{attempts}} attempt(s)`);
  if (button.textContent !== '✓ Done' || button.disabled !== true) {{
    throw new Error(`Expected completed button, got ${{button.textContent}} / ${{button.disabled}}`);
  }}
}}, 25);
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_apps_main_tile_shows_update_dot_and_keeps_install_count_priority():
    html = Path("static/index.html").read_text()
    match = re.search(
        r"function updateAppsBadge\(\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert match
    assert "async function refreshAppsUpdateBadge" in html
    assert "api('/apps/check-updates')" in html

    script = f"""
const badge = {{textContent:'', className:''}};
const $ = selector => selector === '#badge-apps' ? badge : null;
let _activeInstalls = 0;
let _appsUpdateBadgeHasUpdates = true;
{match.group(0)}

updateAppsBadge();
if (badge.className !== 'tile-badge red dot-only' || badge.textContent !== '') {{
  throw new Error(`Expected red update dot, got ${{badge.className}} / ${{badge.textContent}}`);
}}

_activeInstalls = 2;
updateAppsBadge();
if (badge.className !== 'tile-badge blue' || badge.textContent !== 2) {{
  throw new Error(`Expected active install count, got ${{badge.className}} / ${{badge.textContent}}`);
}}
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_updates_tab_dot_has_clean_inset_from_top_right_corner():
    html = Path("static/index.html").read_text()

    assert (
        ".modal-tab.has-update::after{content:'';position:absolute;"
        "top:6px;right:6px;width:8px;height:8px;"
        in html
    )
