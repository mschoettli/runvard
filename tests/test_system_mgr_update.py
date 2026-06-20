import subprocess

from modules import system_mgr


def test_start_runvard_update_uses_systemd(monkeypatch, tmp_path):
    log_path = tmp_path / "runvard-update.log"
    monkeypatch.setattr(system_mgr, "RUNVARD_UPDATE_LOG", str(log_path))

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["systemd-run", "--unit=runvard-self-update", "--collect"]
        return subprocess.CompletedProcess(cmd, 0, stdout="started\n", stderr="")

    monkeypatch.setattr(system_mgr.subprocess, "run", fake_run)

    result = system_mgr.start_runvard_update()

    assert result["ok"] is True
    assert result["method"] == "systemd"
    assert result["stdout"] == "started\n"
    assert result["warning"] == ""


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
