import shutil

from modules import terminal


def test_persistent_terminal_uses_tmux_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    rcfile = tmp_path / "terminal.bashrc"
    monkeypatch.setattr(terminal, "TERMINAL_BASHRC", str(rcfile))

    session = terminal.TerminalSession(persistent=True)

    assert session.command() == [
        "tmux", "new-session", "-A", "-s", "runvard",
        f"/bin/bash --rcfile {rcfile}",
    ]
    assert rcfile.exists()
    assert "history -a" in rcfile.read_text()


def test_persistent_terminal_falls_back_to_shell_without_tmux(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    session = terminal.TerminalSession(persistent=True)

    assert session.command() == ["/bin/bash"]


def test_explicit_terminal_command_is_not_replaced_by_tmux(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tmux")

    session = terminal.TerminalSession(argv=["docker", "exec", "-it", "abc", "/bin/sh"], persistent=True)

    assert session.command() == ["docker", "exec", "-it", "abc", "/bin/sh"]
