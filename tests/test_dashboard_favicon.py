from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_app_dashboard_tiles_prefer_the_apps_favicon():
    assert "function dashTileFaviconUrl(t)" in INDEX
    assert "url.pathname='/favicon.ico'" in INDEX
    assert "dashTileIconHtml(t.icon,t.name,dashTileFaviconUrl(t))" in INDEX


def test_dashboard_favicon_keeps_the_configured_icon_as_fallback():
    assert "function dashApplyTileIconFallback(img)" in INDEX
    assert "data-fallback-icon" in INDEX
    assert "data-fallback-name" in INDEX
