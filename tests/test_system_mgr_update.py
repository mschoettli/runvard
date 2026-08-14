import subprocess

from modules import system_mgr


def test_start_runvard_update_uses_systemd(monkeypatch, tmp_path):
    log_path = tmp_path / "runvard-update.log"
    monkeypatch.setattr(system_mgr, "RUNVARD_UPDATE_LOG", str(log_path))

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "systemd-run"
        assert cmd[1].startswith("--unit=runvard-self-update-")
        assert cmd[2] == "--collect"
        script = open(cmd[-1], encoding="utf-8").read()
        assert 'bash "/opt/runvard/install.sh" --verified-release --yes' in script
        assert "flock -n 9" in script
        assert 'export HOME="${HOME:-/root}"' in script
        assert 'export GH_CONFIG_DIR="${GH_CONFIG_DIR:-${HOME}/.config/gh}"' in script
        assert "write_status running 0" in script
        assert "raw.githubusercontent.com" not in script
        return subprocess.CompletedProcess(cmd, 0, stdout="started\n", stderr="")

    monkeypatch.setattr(system_mgr.subprocess, "run", fake_run)

    result = system_mgr.start_runvard_update()

    assert result["ok"] is True
    assert result["method"] == "systemd"
    assert result["stdout"] == "started\n"
    assert result["warning"] == ""
    assert result["unit"].startswith("runvard-self-update-")


def test_start_runvard_update_falls_back_to_detached_process(monkeypatch, tmp_path):
    log_path = tmp_path / "runvard-update.log"
    monkeypatch.setattr(system_mgr, "RUNVARD_UPDATE_LOG", str(log_path))
    popen_calls = []

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Failed to connect to bus")

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append((cmd, kwargs))

    monkeypatch.setattr(system_mgr.subprocess, "run", fake_run)
    monkeypatch.setattr(system_mgr.subprocess, "Popen", FakePopen)

    result = system_mgr.start_runvard_update()

    assert result["ok"] is True
    assert result["method"] == "detached"
    assert result["warning"] == "Failed to connect to bus"
    assert popen_calls
    assert popen_calls[0][0][0] == "/bin/bash"
    assert popen_calls[0][1]["start_new_session"] is True


def test_runvard_update_status_reads_durable_state(monkeypatch, tmp_path):
    status_path = tmp_path / "runvard-update.status.json"
    status_path.write_text(
        '{"status":"failed","updated_at":"2026-08-14T10:00:00+02:00","exit_code":23}',
        encoding="utf-8",
    )
    monkeypatch.setattr(system_mgr, "RUNVARD_UPDATE_STATUS", str(status_path))

    assert system_mgr.runvard_update_status() == {
        "ok": True,
        "status": "failed",
        "updated_at": "2026-08-14T10:00:00+02:00",
        "exit_code": 23,
    }
