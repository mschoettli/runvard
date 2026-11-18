import shutil
from pathlib import Path

from modules import terminal


ROOT = Path(__file__).resolve().parents[1]


def test_terminal_uses_random_isolated_tmux_session(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    rcfile = tmp_path / "terminal.bashrc"
    monkeypatch.setattr(terminal, "TERMINAL_BASHRC", str(rcfile))

    session = terminal.TerminalSession(owner="alice")

    cmd = session.command()
    assert cmd[:2] == ["/bin/bash", "-lc"]
    assert "tmux new-session -d -s rv-" in cmd[2]
    assert "runvard" not in session.session_id
    assert f"/bin/bash --rcfile {rcfile}" in cmd[2]
    assert f"tmux set-option -t {session.session_id} status off" in cmd[2]
    assert f"tmux set-option -t {session.session_id} mouse off" in cmd[2]
    assert f"exec tmux attach-session -t {session.session_id}" in cmd[2]
    assert rcfile.exists()
    assert "history -a" in rcfile.read_text()


def test_persistent_terminal_falls_back_to_shell_without_tmux(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    session = terminal.TerminalSession(owner="alice")

    assert session.command() == ["/bin/bash"]


def test_explicit_terminal_command_is_not_replaced_by_tmux(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tmux")

    session = terminal.TerminalSession(argv=["docker", "exec", "-it", "abc", "/bin/sh"], persistent=True)

    assert session.command() == ["docker", "exec", "-it", "abc", "/bin/sh"]


def test_terminal_opens_without_password_prompt_and_requests_one_time_grant():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    terminal_section = html.split("// ═══ Terminal ═══", 1)[1].split(
        "function renderAuthorizedTerminal", 1
    )[0]

    assert "openForm(" not in terminal_section
    assert "type:'password'" not in terminal_section
    assert "await post('/terminal/authorize',{})" in terminal_section


def test_only_one_root_terminal_per_user_and_users_are_isolated():
    terminal.kill_all_sessions()
    alice = terminal.TerminalSession(owner="alice")
    bob = terminal.TerminalSession(owner="bob")
    terminal.register_session(alice)
    terminal.register_session(bob)
    assert alice.session_id != bob.session_id
    with __import__("pytest").raises(PermissionError, match="already active"):
        terminal.register_session(terminal.TerminalSession(owner="alice"))
    terminal.unregister_session(alice)
    terminal.unregister_session(bob)


def test_logout_kills_owned_terminal(monkeypatch):
    terminal.kill_all_sessions()
    session = terminal.TerminalSession(owner="alice")
    killed = []
    monkeypatch.setattr(session, "kill", lambda: killed.append(True))
    terminal.register_session(session)
    terminal.kill_user_sessions("alice")
    assert killed == [True]
    assert terminal.active_session_count("alice") == 0
