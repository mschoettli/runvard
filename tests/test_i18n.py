import subprocess
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


def test_all_supported_locales_are_complete():
    result = subprocess.run(
        ["node", "scripts/check-i18n.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_i18n_checker_rejects_placeholder_drift(tmp_path):
    scripts = tmp_path / "scripts"
    static = tmp_path / "static"
    scripts.mkdir()
    static.mkdir()
    shutil.copy(ROOT / "scripts" / "check-i18n.mjs", scripts / "check-i18n.mjs")
    shutil.copy(ROOT / "static" / "login.html", static / "login.html")

    source = (ROOT / "static" / "index.html").read_text()
    broken = source.replace(
        "pasteManyHere:{de:'$1 Elemente hier einfügen'",
        "pasteManyHere:{de:'Elemente hier einfügen'",
        1,
    )
    assert broken != source
    (static / "index.html").write_text(broken)

    result = subprocess.run(
        ["node", "scripts/check-i18n.mjs"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "FILE_I18N.pasteManyHere.de placeholders differ" in result.stderr
