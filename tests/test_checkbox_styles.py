from pathlib import Path
import re


INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"


def test_checkbox_style_resets_generic_input_chrome():
    html = INDEX_HTML.read_text()
    match = re.search(r"input\[type=checkbox\]\{([^}]*)\}", html)

    assert match is not None
    declarations = match.group(1).replace(" ", "")
    assert "padding:0" in declarations
    assert "border:0" in declarations
    assert "background:transparent" in declarations
