from pathlib import Path


HTML = Path(__file__).parents[1] / "static" / "index.html"


def test_backup_tile_exposes_time_machine_tab_and_workflow():
    source = HTML.read_text(encoding="utf-8")

    assert "tabs:['Jobs','History','Time Machine']" in source
    assert "async function renderTimeMachine" in source
    assert "'/time-machine/overview'" in source
    assert "'/time-machine/system'" in source
    assert "'/time-machine/targets'" in source
    assert "'/time-machine/replications'" in source
    assert "tmReplicationForm" in source
    assert "tmPromoteReplica" in source
    assert "tmImportReplicaForm" in source
    assert "time-machine:promote-replica" in source
    assert "tmReconcileConfig" in source
    assert "'/time-machine/jobs?limit=10'" in source
    assert "tmJobCards" in source
    assert "client_encryption_required" in source
    assert "tmTargetPolicyForm" in source
    assert "'/time-machine/targets/policy'" in source
    assert "tmReplicationPolicyForm" in source
    assert "'/time-machine/replications/policy'" in source
    assert "'/time-machine/events?limit=10'" in source
    assert "tmEventCards" in source
    assert "target.allocated_bytes" in source
    assert "tmGeneratePassword" in source
    assert "crypto.getRandomValues" in source
    assert "tmUpdateCreatePreview" in source
    assert "tm-create-preview" in source
    assert "placeholder:'1785580000'" in source
    assert "/srv/runvard-replicas" in source
    assert "20260801T020000Z-abcdef" not in source


def test_time_machine_ui_has_all_supported_locales_and_safe_rendering():
    source = HTML.read_text(encoding="utf-8")

    for language in ("en", "de", "fr", "it", "es", "pt"):
        assert f"{language}:{{title:" in source
        assert f"Object.assign(TM_I18N.{language},{{configurationPreview:" in source
    assert "esc(target.display_name" in source
    assert "esc(target.share_name" in source
    assert "esc(guide.lan.url" in source
