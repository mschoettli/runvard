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
