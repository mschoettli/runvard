from pathlib import Path


ROOT = Path(__file__).parents[1]
HTML = ROOT / "static" / "index.html"
MODERN = ROOT / "static" / "modern-theme.css"
I18N_CHECK = ROOT / "scripts" / "check-i18n.mjs"


def test_shared_path_picker_has_accessible_modal_and_focus_management():
    source = HTML.read_text(encoding="utf-8")

    for marker in (
        'id="path-picker-overlay"',
        'role="dialog"',
        'aria-modal="true"',
        'aria-labelledby="path-picker-title"',
        'id="path-picker-status"',
        'aria-live="polite"',
        "function pathPickerKeydown",
        "pathPickerReturnFocus",
    ):
        assert marker in source


def test_open_form_supports_folder_file_device_and_remote_picker_fields():
    source = HTML.read_text(encoding="utf-8")

    assert "f.picker" in source
    assert "openPathPickerForField" in source
    assert "mode:'folder'" in source
    assert "mode:'file'" in source
    assert "mode:'device'" in source
    assert "mode:'remote'" in source
    assert "isExpertMode()?'' :'readonly'" in source


def test_all_known_manual_server_path_flows_are_connected_to_picker_policies():
    source = HTML.read_text(encoding="utf-8")

    for purpose in (
        "mountpoint",
        "nfs-export",
        "swap-file",
        "share",
        "backup-source",
        "backup-destination",
        "container-host",
        "vm-image",
        "vm-pool",
        "time-machine",
        "system-file",
        "app-host",
    ):
        assert f"purpose:'{purpose}'" in source


def test_path_picker_text_is_complete_for_all_six_locales_and_checked_by_gate():
    source = HTML.read_text(encoding="utf-8")
    checker = I18N_CHECK.read_text(encoding="utf-8")

    assert "const PATH_PICKER_I18N=" in source
    assert "function pathPickerText" in source
    assert '"PATH_PICKER_I18N"' in checker


def test_path_picker_is_mobile_first_and_touch_friendly():
    source = HTML.read_text(encoding="utf-8")

    assert ".path-picker-layout{display:grid;grid-template-columns:1fr" in source
    assert "min-height:2.75rem" in source
    assert "@media(min-width:768px)" in source
    assert "grid-template-columns:minmax(11rem,.42fr) minmax(0,1fr)" in source


def test_modern_theme_scopes_path_picker_surfaces_and_states():
    source = MODERN.read_text(encoding="utf-8")

    for selector in (
        ".path-picker-card",
        ".path-picker-root",
        ".path-picker-entry",
        ".path-picker-entry.selected",
        ".path-picker-status.good",
        ".path-picker-status.bad",
    ):
        assert f'html[data-ui-theme="modern"] {selector}' in source
