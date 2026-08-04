from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENERGY_INFO = ROOT / "static" / "energy-info.html"


def test_energy_info_uses_modern_lightweight_power_guide_layout():
    html = ENERGY_INFO.read_text(encoding="utf-8")

    assert ".hero-panel{" in html
    assert ".hero-visual{" in html
    assert ".page-shell{" in html
    assert "linear-gradient(145deg,#f4f8fc 0%,#e7eef7 52%,#edf4f8 100%)" in html
    assert '<div class="brand-mark" aria-hidden="true">⚡</div>' in html
    assert '<div class="page-shell">' in html
    assert '<section class="hero-panel">' in html
    assert 'class="hero-kicker"' in html


def test_power_tab_opens_energy_info_in_a_new_tab():
    index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "window.open('/static/energy-info.html','_blank','noopener')" in index_html
    assert "location.href='/static/energy-info.html'" not in index_html
