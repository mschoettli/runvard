from pathlib import Path


GUIDE_PATH = Path("static/server-connection-guide.html")


def guide_html():
    return GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.exists() else ""


def test_connection_guide_covers_every_supported_server_type():
    html = guide_html()
    assert '<main id="guide-content">' in html
    for section_id in ("runvard", "proxmox", "linux", "windows", "generic"):
        assert f'id="guide-{section_id}"' in html


def test_connection_guide_commands_have_accessible_copy_actions():
    html = guide_html()
    assert 'class="command-copy"' in html
    assert 'data-copy-command=' in html
    assert "navigator.clipboard.writeText" in html
    assert 'id="copy-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html


def test_connection_guide_has_complete_detail_translations():
    html = guide_html()
    start = html.index("const DETAIL_TRANSLATIONS={")
    translations = html[start:html.index("\n};", start)]
    for language in ("fr", "it", "es", "pt"):
        language_start = translations.index(f"  {language}:{{")
        next_language = min(
            (
                translations.find(f"  {candidate}:{{", language_start + 1)
                for candidate in ("fr", "it", "es", "pt")
                if translations.find(f"  {candidate}:{{", language_start + 1) >= 0
            ),
            default=len(translations),
        )
        locale = translations[language_start:next_language]
        for kind in ("runvard", "proxmox", "linux", "windows", "generic"):
            assert f"{kind}:{{" in locale
        assert locale.count("steps:") == 5
        assert locale.count("fields:") == 5
        assert locale.count("checks:") == 5
        assert locale.count("security:") == 5


def test_connection_guide_is_mobile_first_and_keyboard_accessible():
    html = guide_html()
    assert '<nav class="guide-nav" aria-label=' in html
    assert '<label class="language" for="guide-language">' in html
    assert ".command-copy{min-width:2.75rem;min-height:2.75rem" in html
    assert "@media (min-width:48rem)" in html
    assert "@media (prefers-reduced-motion:reduce)" in html


def test_connection_guide_uses_placeholders_and_protected_source_links():
    html = guide_html()
    for placeholder in (
        "RUNVARD-IP",
        "PROXMOX-IP",
        "LINUX-IP",
        "WINDOWS-IP",
        "SERVER-IP",
    ):
        assert placeholder in html
    assert "BEGIN OPENSSH PRIVATE KEY" not in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
