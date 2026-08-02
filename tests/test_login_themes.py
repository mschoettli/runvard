from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOGIN_HTML = ROOT / "static" / "login.html"


def login_html():
    return LOGIN_HTML.read_text(encoding="utf-8")


def test_login_bootstraps_the_saved_theme_before_styles_render():
    html = login_html()

    bootstrap = html.index("localStorage.getItem('runvard_ui_theme')")
    styles = html.index("<style>")

    assert bootstrap < styles
    assert "document.documentElement.dataset.uiTheme" in html[:styles]
    assert "theme==='modern'?'modern':'original'" in html[:styles]


def test_login_exposes_an_accessible_original_modern_switch():
    html = login_html()

    assert 'class="theme-picker"' in html
    assert 'role="group"' in html
    assert 'data-theme="original"' in html
    assert 'data-theme="modern"' in html
    assert 'aria-pressed="true"' in html
    assert "localStorage.setItem(UI_THEME_KEY,theme)" in html


def test_modern_login_styles_are_scoped_and_original_login_is_retained():
    html = login_html()

    assert ".card{background:var(--bg2)" in html
    assert 'html[data-ui-theme="modern"] body' in html
    assert 'html[data-ui-theme="modern"] .card' in html
    assert 'html[data-ui-theme="modern"] .field input' in html
    assert 'html[data-ui-theme="modern"] .btn-login' in html


def test_login_theme_controls_are_responsive_and_respect_reduced_motion():
    html = login_html()

    assert "min-height:44px" in html
    assert "@media(min-width:768px)" in html
    assert "@media(prefers-reduced-motion:reduce)" in html


def test_login_keeps_one_authentication_form_and_endpoint():
    html = login_html()

    assert html.count('id="login-form"') == 1
    assert html.count("fetch('/api/login'") == 1
