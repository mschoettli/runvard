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


def test_connected_server_layout_has_equal_cards_and_wide_alignment_hook():
    assert ".connected-server-card{" in INDEX
    assert "--connected-server-card-height" in INDEX
    assert "alignConnectedServerCards" in INDEX
    assert "widgets.getBoundingClientRect().bottom" in INDEX


def test_connected_server_labels_exist_for_every_supported_language():
    for language in ("en", "de", "fr", "it", "es", "pt"):
        marker = f"{language}:{{"
        start = INDEX.index(marker, INDEX.index("const FED_I18N"))
        end = INDEX.index("},", start)
        locale = INDEX[start:end]
        assert "connectedServers:" in locale
        assert "network:" in locale
        assert "showMore:" in locale


def test_mobile_topbar_can_shrink_without_horizontal_overflow():
    assert ".topbar{grid-template-columns:minmax(0,1fr) auto" in INDEX
    assert ".logo{min-width:0;overflow:hidden}" in INDEX
    assert ".logo #hostname{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" in INDEX
