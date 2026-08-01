import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_load_tool_documents_limits_and_rejects_more_than_twenty_clients(tmp_path):
    script = ROOT / "scripts" / "time-machine-load-test.py"
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True,
    )
    assert help_result.returncode == 0
    assert "--clients" in help_result.stdout
    assert "--size-mib" in help_result.stdout
    result = subprocess.run(
        [sys.executable, str(script), "--mount", str(tmp_path), "--clients", "21"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "20" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_macos_acceptance_tool_is_syntax_valid_and_safe_by_default():
    script = ROOT / "scripts" / "time-machine-macos-acceptance.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0
    source = script.read_text(encoding="utf-8")
    assert "tmutil verifybackups" in source
    assert "--apply-destination" in source
    assert "No Time Machine destination was changed" in source


def test_linux_acceptance_tool_checks_encrypted_access_and_is_read_only():
    script = ROOT / "scripts" / "time-machine-linux-acceptance.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0
    source = script.read_text(encoding="utf-8")
    assert "testparm" in source
    assert "smbclient" in source
    assert "--client-protection=encrypt" in source
    assert "avahi-browse" in source
    assert "runvard-time-machine-maintenance.timer" in source
    assert "chmod 0600" in source
    assert "mktemp" in source
    assert "trap cleanup EXIT" in source
    assert "-c 'ls'" in source
    assert "-c 'put" not in source


def test_acceptance_runbook_covers_twenty_macs_and_restore_gate():
    runbook = ROOT / "docs" / "TIME_MACHINE_ACCEPTANCE.md"
    source = runbook.read_text(encoding="utf-8")
    assert "10 concurrent" in source
    assert "20 registered" in source
    assert "Migration Assistant" in source
    assert "time-machine-load-test.py" in source
    assert "time-machine-macos-acceptance.sh" in source
    assert "time-machine-linux-acceptance.sh" in source


def test_replication_account_accepts_keys_but_has_no_known_password():
    installer = (ROOT / "scripts" / "install-full.sh").read_text(encoding="utf-8")
    assert "PasswordAuthentication no" in (
        ROOT / "systemd" / "90-runvard-time-machine-replication.conf"
    ).read_text(encoding="utf-8")
    assert "openssl rand -hex 48" in installer
    assert "passwd -l runvard-replica" not in installer
