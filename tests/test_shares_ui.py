from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "static" / "index.html"
MODERN_THEME_CSS = ROOT / "static" / "modern-theme.css"


def test_modern_shares_uses_progressive_protocol_selection():
    html = INDEX_HTML.read_text()
    css = MODERN_THEME_CSS.read_text()

    assert "function renderSharesModern" in html
    assert "window.selectShareProtocol" in html
    assert 'class="modern-share-protocol-grid"' in html
    assert 'class="modern-share-detail"' in html
    assert 'data-share-protocol="smb"' in html
    assert 'data-share-protocol="nfs"' in html
    assert 'data-share-protocol="ftp"' in html
    assert 'aria-pressed="true"' in html
    assert 'html[data-ui-theme="modern"] .modern-share-protocol-grid' in css
    assert 'html[data-ui-theme="modern"] .modern-share-protocol-card' in css
    assert 'html[data-ui-theme="modern"] .modern-share-detail' in css


def test_original_shares_keeps_its_compact_tables():
    html = INDEX_HTML.read_text()

    assert "function renderSharesOriginal" in html
    assert "if(!isModernUi())return renderSharesOriginal" in html
    assert 'class="shares-original"' in html
    assert '<h3>Samba (SMB)</h3>' in html
    assert '<h3>NFS</h3>' in html


def test_share_protocol_failures_are_isolated():
    html = INDEX_HTML.read_text()

    assert "api('/shares/samba').catch(()=>[])" in html
    assert "api('/shares/nfs').catch(()=>[])" in html
    assert "api('/shares/ftp').catch(()=>({active:false,unavailable:true}))" in html


def test_modern_share_rows_become_cards_on_mobile():
    css = MODERN_THEME_CSS.read_text()

    assert 'html[data-ui-theme="modern"] .modern-share-detail thead {' in css
    assert 'html[data-ui-theme="modern"] .modern-share-detail tr {' in css
    assert 'html[data-ui-theme="modern"] .modern-share-detail td::before {' in css
    assert 'content: attr(data-label);' in css


def test_modern_active_protocol_card_uses_quiet_glass_selection():
    css = MODERN_THEME_CSS.read_text()

    assert 'html[data-ui-theme="modern"] .modern-share-protocol-card.is-active .modern-share-protocol-mark {' in css
    assert 'html[data-ui-theme="modern"] .modern-share-protocol-card.is-active::after {' not in css
    assert 'background: rgb(255 255 255 / 82%);' in css
    assert 'background: var(--accent);\n  color: #fff;' in css
