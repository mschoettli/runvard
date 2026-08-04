from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "static" / "index.html"
MODERN_THEME_CSS = ROOT / "static" / "modern-theme.css"


def test_modern_network_uses_quiet_panels_and_interface_cards():
    html = INDEX_HTML.read_text()
    css = MODERN_THEME_CSS.read_text()

    assert "function modernNetworkInterfaceCard" in html
    assert "function modernNetworkTable" in html
    assert "function modernNetworkPanel" in html
    assert "function modernNetworkMenu" in html
    assert 'class="modern-network-panel"' in html
    assert 'class="modern-network-interface-card"' in html
    assert 'class="modern-network-table-wrap"' in html
    assert 'class="modern-share-protocol-grid modern-network-menu"' in html
    assert 'data-network-tab="${esc(item.key)}"' in html
    assert "onclick=\"renderNetwork($('#modal-body'),'${item.key}')\"" in html
    assert "isModernUi()\n      ? modernNetworkPanel(" in html
    assert 'html[data-ui-theme="modern"] .modern-network-panel' in css
    assert 'html[data-ui-theme="modern"] .modern-network-interface-card' in css
    assert 'html[data-ui-theme="modern"] .modern-network-panel td::before {' in css


def test_original_network_rendering_path_is_preserved():
    html = INDEX_HTML.read_text()

    assert "const ifaceCard=i=>`<div class=\"nx-action-card\">" in html
    assert "const originalInterfaceContent=viewIntro('Network interfaces'" in html
    assert "simpleTable(['No.','To','Action','From',''],firewallRows.join(''))" in html
