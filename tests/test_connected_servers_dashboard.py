from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "static" / "index.html"
).read_text()


def test_server_admin_is_not_a_main_dashboard_tile():
    start = INDEX.index("const TILES=[")
    end = INDEX.index("];", start)
    assert "{id:'servers'" not in INDEX[start:end]


def test_connected_servers_have_a_conditional_dashboard_section():
    assert 'id="connected-servers"' in INDEX
    assert 'id="connected-servers-list"' in INDEX
    assert "const remoteNodes=(overview.nodes||[]).filter(node=>!node.current)" in INDEX
    assert "section.hidden=remoteNodes.length===0" in INDEX


def test_connected_server_cards_are_switchable_and_show_compact_metrics():
    assert "connectedServerCardHtml" in INDEX
    assert "fedSwitch(node.node_id)" in INDEX
    assert "connectedServerMetricHtml('cpu'" in INDEX
    assert "connectedServerMetricHtml('ram'" in INDEX
    assert "connectedServerMetricHtml('network'" in INDEX
    assert "connectedServerSparkline" in INDEX


def test_connected_server_section_exposes_connect_and_invisible_sorting():
    assert "openConnectedServersModal" in INDEX
    assert "connected-servers-add" in INDEX
    assert "Sortable.create(list" in INDEX
    assert "connected-server-drag-handle" not in INDEX


def test_status_refresh_does_not_rebuild_the_complete_card_list():
    assert "list.innerHTML=nodes.map" not in INDEX
    assert "const existingCards=new Map" in INDEX
    assert "card.dataset.renderKey" in INDEX


def test_connected_servers_refresh_independently_from_main_tile_badges():
    assert "async function loadConnectedServers()" in INDEX
    assert "setInterval(loadConnectedServers,10000)" in INDEX


def test_connected_servers_merge_runvard_and_external_overviews():
    assert "api('/external-servers/v1/overview')" in INDEX
    assert "external.nodes||[]" in INDEX
    assert "federation.nodes||[]" in INDEX
    assert "external:true" in INDEX


def test_external_server_cards_open_safe_http_links_without_sso():
    assert "safeExternalServerUrl" in INDEX
    assert "window.open(target,'_blank','noopener,noreferrer')" in INDEX
    assert "if(node.external)" in INDEX
    assert "fedSwitch(node.node_id)" in INDEX
    url_guard = INDEX[
        INDEX.index("function safeExternalServerUrl"):
        INDEX.index("window.openServerTypePicker", INDEX.index("function safeExternalServerUrl"))
    ]
    assert "location.origin" not in url_guard
    assert "new URL(String(value||''))" in url_guard


def test_add_button_offers_runvard_and_external_server_types():
    assert "openServerTypePicker" in INDEX
    for kind in ("runvard", "proxmox", "linux", "windows", "generic"):
        assert f"['{kind}'" in INDEX
    assert "externalServerTypeSelect('${kind}')" in INDEX


def test_server_type_picker_separates_trusted_runvard_from_status_links():
    assert "server-type-trusted" in INDEX
    assert "server-type-divider" in INDEX
    assert "server-type-external-grid" in INDEX
    assert "runvardAccessDescription" in INDEX
    assert "statusAndLinks" in INDEX
    for key in (
        "proxmoxStatusDescription",
        "linuxStatusDescription",
        "windowsStatusDescription",
        "genericStatusDescription",
    ):
        assert key in INDEX


def test_server_type_picker_opens_connection_guide_in_a_protected_new_tab():
    assert 'class="server-connection-guide-link"' in INDEX
    assert 'href="/static/server-connection-guide.html"' in INDEX
    assert 'target="_blank"' in INDEX
    assert 'rel="noopener noreferrer"' in INDEX

    external_start = INDEX.index("const EXTERNAL_SERVER_I18N")
    for language in ("en", "de", "fr", "it", "es", "pt"):
        start = INDEX.index(f"{language}:{{", external_start)
        end = INDEX.index("},", start)
        assert "connectionGuide:" in INDEX[start:end]


def test_server_type_picker_uses_readable_copy_and_one_icon_language():
    assert ".server-type-choice-copy strong{" in INDEX
    assert "color:var(--text)" in INDEX[
        INDEX.index(".server-type-choice-copy strong{"):
        INDEX.index("}", INDEX.index(".server-type-choice-copy strong{"))
    ]
    assert ".server-type-choice-copy>span{" in INDEX
    assert ".server-type-choice span{" not in INDEX
    assert "function serverTypeIcon(kind)" in INDEX
    assert 'class="server-type-choice-svg"' in INDEX


def test_server_forms_have_visible_navigation_and_keyboard_escape():
    assert 'id="server-form-back"' in INDEX
    assert 'id="server-form-close"' in INDEX
    assert "externalServerFormBack" in INDEX
    assert "event.key==='Escape'" in INDEX
    assert "document.addEventListener('keydown',formOverlayKeydown)" in INDEX
    assert "formOverlayFocusable" in INDEX
    assert "formReturnFocus" in INDEX


def test_external_form_keeps_type_context_and_groups_related_fields():
    assert "externalServerFormTitle" in INDEX
    assert "fedText(kind)" in INDEX
    assert "{type:'section',label:fedText('general')}" in INDEX
    assert "{type:'section',label:fedText('statusConnection')}" in INDEX
    assert "{type:'section',label:fedText('credentials')}" in INDEX
    assert 'class="form-section-heading"' in INDEX
    assert '<label for="f_${f.name}">' in INDEX


def test_connection_test_has_persistent_inline_state():
    assert "external-connection-result" in INDEX
    assert "testingConnection" in INDEX
    assert "connectionOk" in INDEX
    assert "aria-live" in INDEX


def test_external_admin_rows_expose_status_and_label_icon_actions():
    assert "external-server-health-text" in INDEX
    for key in ("refreshStatus", "editServer", "deleteServer"):
        assert f"fedText('{key}')" in INDEX
    assert "fedText(enabled?'disable':'enable')" in INDEX
    assert 'aria-label="${esc(fedText(' in INDEX


def test_external_server_admin_supports_all_lifecycle_actions():
    assert "externalServerForm" in INDEX
    assert "'/external-servers/v1/admin/create'" in INDEX
    assert "'/external-servers/v1/admin/update'" in INDEX
    assert "'/external-servers/v1/admin/test'" in INDEX
    assert "'/external-servers/v1/admin/refresh'" in INDEX
    assert "'/external-servers/v1/admin/enabled'" in INDEX
    assert "'/external-servers/v1/admin/delete'" in INDEX


def test_connected_server_layout_keeps_equal_compact_cards():
    assert ".connected-server-card{" in INDEX
    assert "--connected-server-card-height:6.5rem" in INDEX
    assert "alignConnectedServerCards" not in INDEX
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in INDEX
    wide_layout = INDEX[
        INDEX.index("@media (min-width: 1740px)"):
        INDEX.index("@media (hover:hover)", INDEX.index("@media (min-width: 1740px)"))
    ]
    assert ".widgets{margin-bottom:.75rem}" in wide_layout


def test_connected_server_labels_exist_for_every_supported_language():
    for language in ("en", "de", "fr", "it", "es", "pt"):
        marker = f"{language}:{{"
        start = INDEX.index(marker, INDEX.index("const FED_I18N"))
        end = INDEX.index("},", start)
        locale = INDEX[start:end]
        assert "connectedServers:" in locale
        assert "network:" in locale
        assert "showMore:" in locale
        assert "externalServer:" in locale
        assert "serverType:" in locale
        assert "testConnection:" in locale


def test_mobile_topbar_can_shrink_without_horizontal_overflow():
    assert ".topbar{grid-template-columns:minmax(0,1fr) auto" in INDEX
    assert ".logo{min-width:0;overflow:hidden}" in INDEX
    assert ".logo #hostname{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" in INDEX


def test_external_server_form_is_scrollable_with_touch_sized_sticky_actions():
    assert ".external-server-form-card{" in INDEX
    assert "overflow:hidden" in INDEX[
        INDEX.index(".external-server-form-card{"):
        INDEX.index("}", INDEX.index(".external-server-form-card{"))
    ]
    assert ".external-server-form-card .form-body{" in INDEX
    assert ".external-server-form-card .form-actions{" in INDEX
    assert "position:sticky" in INDEX
    assert "min-height:2.75rem" in INDEX


def test_mobile_server_admin_actions_have_touch_sized_targets():
    assert ".external-server-admin-actions .btn{min-height:2.75rem}" in INDEX
    assert ".external-server-admin-actions .btn.icon{width:2.75rem;height:2.75rem}" in INDEX
    assert ".modal.servers-modal .modal-close{width:2.75rem;height:2.75rem}" in INDEX


def test_new_modal_copy_exists_for_every_supported_language():
    external_start = INDEX.index("const EXTERNAL_SERVER_I18N")
    for language in ("en", "de", "fr", "it", "es", "pt"):
        marker = f"{language}:{{"
        start = INDEX.index(marker, external_start)
        end = INDEX.index("},", start)
        locale = INDEX[start:end]
        for key in (
            "statusAndLinks",
            "runvardAccessDescription",
            "proxmoxStatusDescription",
            "linuxStatusDescription",
            "windowsStatusDescription",
            "genericStatusDescription",
            "general",
            "statusConnection",
            "credentials",
            "testingConnection",
        ):
            assert f"{key}:" in locale


def test_external_server_choices_have_no_information_actions_or_guide_modals():
    for marker in (
        "server-type-choice-wrap",
        "server-type-info",
        "server-guide",
        "SERVER_GUIDE_I18N",
        "SERVER_GUIDE_COMMANDS",
        "SERVER_GUIDE_SOURCES",
        "externalServerGuideLabel",
        "externalServerInfo",
        "openExternalServerGuide",
        "copyServerGuideCommand",
        "informationAbout",
        "setupGuide",
        "officialDocs",
    ):
        assert marker not in INDEX
