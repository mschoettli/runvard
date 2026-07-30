from pathlib import Path

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
