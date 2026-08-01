from pathlib import Path
import hashlib
import re


INDEX_HTML = Path(__file__).resolve().parents[1] / "static" / "index.html"
MODERN_THEME_CSS = INDEX_HTML.with_name("modern-theme.css")


def test_checkbox_style_resets_generic_input_chrome():
    html = INDEX_HTML.read_text()
    match = re.search(r"input\[type=checkbox\]\{([^}]*)\}", html)

    assert match is not None
    declarations = match.group(1).replace(" ", "")
    assert "padding:0" in declarations
    assert "border:0" in declarations
    assert "background:transparent" in declarations
    assert (
        ".external-server-form-card .form-field "
        "input:not([type=checkbox])"
        in html
    )
    assert ".external-server-form-card .form-field input," not in html


def test_modern_theme_form_styles_exclude_checkboxes():
    css = MODERN_THEME_CSS.read_text()

    assert 'html[data-ui-theme="modern"] input:not([type="checkbox"]),' in css
    assert (
        'html[data-ui-theme="modern"] input:not([type="checkbox"]):focus,'
        in css
    )
    assert 'html[data-ui-theme="modern"] input,' not in css
    assert 'html[data-ui-theme="modern"] input:focus,' not in css


def test_modern_theme_cache_buster_matches_stylesheet():
    html = INDEX_HTML.read_text()
    css = MODERN_THEME_CSS.read_bytes()
    version = hashlib.sha256(css).hexdigest()[:12]

    assert f'/static/modern-theme.css?v={version}' in html
