from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
MODERN = (ROOT / "static" / "modern-theme.css").read_text(encoding="utf-8")


def mobile_styles():
    start = INDEX.index("@media (max-width: 760px)")
    end = INDEX.index("@keyframes runvard-shift", start)
    return INDEX[start:end]


def test_account_menu_uses_an_accessible_button():
    assert '<button id="topbar-user"' in INDEX
    assert 'aria-controls="user-menu"' in INDEX
    assert 'aria-expanded="false"' in INDEX
    assert 'class="topbar-user-label"' in INDEX
    assert "trigger.setAttribute('aria-expanded'" in INDEX


def test_secondary_header_tools_are_available_in_the_mobile_menu():
    assert 'class="mobile-header-tools"' in INDEX
    assert 'closeUserMenu();openPortsView()' in INDEX
    assert 'closeUserMenu();openBtop()' in INDEX
    assert 'closeUserMenu();toggleBgPanel()' in INDEX


def test_mobile_header_has_one_stable_touch_friendly_row():
    css = mobile_styles()

    assert "height:3.75rem" in css
    assert ".desktop-header-action,.topbar-sep{display:none}" in css
    assert ".topbar-user-button{width:2.75rem;height:2.75rem" in css
    assert ".mobile-header-tools{display:block}" in css
    assert ".logo{min-width:0;overflow:hidden}" in css
    assert ".logo{flex-direction:column" in css
    assert ".logo-host{display:block" in css


def test_mobile_progress_is_pinned_without_changing_header_height():
    css = mobile_styles()

    assert ".topbar-center{position:absolute;left:0;right:0;bottom:0" in css
    assert ".top-progress-track{width:100%;height:3px" in css
    assert ".top-progress-label{position:absolute;width:1px;height:1px" in css


def test_modern_theme_keeps_the_shared_mobile_geometry():
    start = MODERN.index("@media (max-width: 760px)")
    mobile = MODERN[start:]

    assert 'html[data-ui-theme="modern"] .topbar {' in mobile
    assert "height: 3.75rem" in mobile
    assert 'html[data-ui-theme="modern"] .user-menu {' in mobile
    assert "width: min(calc(100vw - 1.5rem), 22rem)" in mobile
    assert "background: rgb(248 251 255 / 98%)" in mobile
