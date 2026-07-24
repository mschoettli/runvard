import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_supported_locales_are_complete():
    result = subprocess.run(
        ["node", "scripts/check-i18n.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
