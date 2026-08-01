from pathlib import Path


HTML = Path(__file__).parents[1] / "static" / "index.html"
MODERN_THEME = Path(__file__).parents[1] / "static" / "modern-theme.css"


def test_backup_uses_a_four_step_tile_wizard_instead_of_a_path_form():
    source = HTML.read_text(encoding="utf-8")

    assert "backup-wizard-card" in source
    assert "backupWizardRender" in source
    assert "backup-source-tile" in source
    assert "backup-target-tile" in source
    assert "backup-schedule-tile" in source
    assert "backupWizardSummary" in source
    assert "'/backup/locations'" in source
    assert "'/backup/validate'" in source


def test_backup_wizard_includes_a_directory_browser_and_accessible_step_state():
    source = HTML.read_text(encoding="utf-8")

    assert "backupDirectoryBrowser" in source
    assert "'/backup/browse?path='" in source
    assert 'aria-current="step"' in source
    assert 'role="radiogroup"' in source
    assert "min-height:2.75rem" in source


def test_backup_wizard_explains_mirror_deletion_and_same_disk_risk():
    source = HTML.read_text(encoding="utf-8")

    assert "Files deleted from the source can also be deleted from the destination" in source
    assert "Not protected against a disk failure" in source


def test_backup_tile_text_layers_are_block_elements():
    source = HTML.read_text(encoding="utf-8")

    assert ".backup-choice-title{display:block" in source
    assert ".backup-choice-meta{display:block" in source
    assert ".backup-choice-path{display:block" in source


def test_backup_browser_only_shows_hidden_folders_in_expert_mode():
    source = HTML.read_text(encoding="utf-8")

    assert "function backupVisibleBrowserEntries" in source
    assert "isExpertMode()||!entry.name.startsWith('.')" in source


def test_backup_wizard_resets_the_modal_scroll_position_between_views():
    source = HTML.read_text(encoding="utf-8")

    assert source.count("body.scrollTop=0") >= 2


def test_backup_common_source_labels_are_localized_in_the_wizard():
    source = HTML.read_text(encoding="utf-8")

    assert "function backupWizardSourceLabel" in source
    assert "homes:'Benutzerordner'" in source


def test_backup_wizard_has_scoped_modern_theme_surfaces_and_typography():
    source = MODERN_THEME.read_text(encoding="utf-8")

    for selector in (
        ".form-card.backup-wizard-card",
        ".backup-wizard-card .form-head",
        ".backup-wizard-heading h3",
        ".backup-wizard-heading p",
        ".backup-wizard-kicker",
        ".backup-choice-tile",
        ".backup-choice-title",
        ".backup-choice-meta",
        ".backup-choice-path",
    ):
        assert f'html[data-ui-theme="modern"] {selector}' in source
    assert "--backup-wizard-accent:" in source
    assert "--backup-wizard-surface:" in source


def test_backup_wizard_has_modern_states_notices_and_browser_controls():
    source = MODERN_THEME.read_text(encoding="utf-8")

    for selector in (
        '.backup-wizard-step[aria-current="step"]::before',
        ".backup-choice-tile.selected",
        ".backup-choice-tile.selected .backup-choice-check",
        ".backup-choice-badge.warn",
        ".backup-wizard-notice",
        ".backup-wizard-notice.good",
        ".backup-summary-row",
        ".backup-browser-up",
        ".backup-browser-row",
        ".backup-browser-row:hover",
    ):
        assert f'html[data-ui-theme="modern"] {selector}' in source
