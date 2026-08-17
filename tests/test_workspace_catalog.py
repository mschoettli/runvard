from pathlib import Path
from types import SimpleNamespace

import yaml

from modules import apps


ROOT = Path(__file__).resolve().parents[1]


def _workspace():
    return next(app for app in apps.CATALOG if app["id"] == "workspace")


def _bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    web_ref = "registry.example/workspace-web@sha256:" + "a" * 64
    migrator_ref = "registry.example/workspace-migrator@sha256:" + "b" * 64
    (bundle / "compose.yaml").write_text(
        f"services:\n"
        f"  web:\n"
        f"    image: {web_ref}\n"
        f"    ports: ['3000:3000']\n"
        f"  migrator:\n"
        f"    image: {migrator_ref}\n"
    )
    (bundle / "postgres-init.sh").write_text("#!/bin/sh\nset -eu\n")
    (bundle / "restore-probe.sh").write_text("#!/bin/sh\nset -eu\n")
    return bundle


def test_workspace_is_a_fixed_first_party_catalog_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path / "apps"))
    monkeypatch.setattr(apps.docker_mgr, "list_compose_projects", lambda: [])

    entry = next(
        app for app in apps.get_catalog()["apps"] if app["id"] == "workspace"
    )

    assert entry["id"] == "workspace"
    assert entry["name"] == "Workspace"
    assert entry["first_party"] is True
    assert entry["port"] == 3100
    assert "repository_url" not in entry
    assert entry["installed"] is False
    assert entry["running"] is False
    assert _workspace()["managed_handler"] == "workspace"
    assert _workspace()["tpl"]["image"] == "managed-by-runvard"


def test_workspace_draft_comes_only_from_fixed_bundle(monkeypatch, tmp_path):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(apps, "WORKSPACE_BUNDLE_DIR", str(bundle))
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path / "apps"))

    draft = apps.get_app("workspace")

    assert draft["compose"] == (bundle / "compose.yaml").read_text()
    assert "@sha256:" in draft["compose"]
    assert "latest" not in draft["compose"]
    assert "main" not in draft["compose"]


def test_workspace_install_discards_browser_compose_and_preserves_secrets(
    monkeypatch, tmp_path
):
    bundle = _bundle(tmp_path)
    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "workspace"
    secret_dir = app_dir / "secrets"
    secret_dir.mkdir(parents=True)
    existing = secret_dir / "app-password"
    existing.write_text("keep-this-secret\n")
    monkeypatch.setattr(apps, "WORKSPACE_BUNDLE_DIR", str(bundle))
    monkeypatch.setattr(apps, "APPS_DIR", str(apps_dir))
    monkeypatch.setattr(
        apps.docker_mgr,
        "check_compose_ports",
        lambda app_id, content: {"ok": True, "conflicts": []},
    )
    monkeypatch.setattr(
        apps.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(apps, "_running", lambda app_id: True)
    monkeypatch.setattr(apps.time, "sleep", lambda seconds: None)

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(apps.threading, "Thread", ImmediateThread)

    malicious = "services:\n  web:\n    image: attacker/owned:latest\n"
    job = apps.install("workspace", malicious)

    assert apps.install_status(job["job_id"])["ok"] is True
    installed = (app_dir / "docker-compose.yml").read_text()
    assert installed == (bundle / "compose.yaml").read_text()
    assert "attacker" not in installed
    assert existing.read_text() == "keep-this-secret\n"
    migration_secret = secret_dir / "migration-password"
    assert migration_secret.read_text().strip()
    assert migration_secret.stat().st_mode & 0o777 == 0o600
    assert (app_dir / "postgres-init.sh").read_text() == "#!/bin/sh\nset -eu\n"
    assert (app_dir / "restore-probe.sh").read_text() == "#!/bin/sh\nset -eu\n"


def test_workspace_update_uses_managed_handler_without_request_release(
    monkeypatch, tmp_path
):
    app_dir = tmp_path / "workspace"
    app_dir.mkdir()
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    calls = []

    from modules import workspace_app

    monkeypatch.setattr(
        workspace_app,
        "update",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "state": "completed"},
    )

    result = apps.action("workspace", "update")

    assert result == {"ok": True, "state": "completed"}
    assert calls == [{"initiator_role": "admin"}]


def test_workspace_start_and_stop_use_managed_handler(monkeypatch, tmp_path):
    app_dir = tmp_path / "workspace"
    app_dir.mkdir()
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    from modules import workspace_app
    calls = []
    monkeypatch.setattr(workspace_app, "start", lambda **kwargs: calls.append(("start", kwargs)) or {"state": "running"})
    monkeypatch.setattr(workspace_app, "stop", lambda **kwargs: calls.append(("stop", kwargs)) or {"state": "stopped"})
    assert apps.action("workspace", "start")["state"] == "running"
    assert apps.action("workspace", "stop")["state"] == "stopped"
    assert calls == [("start", {"initiator_role": "admin"}), ("stop", {"initiator_role": "admin"})]


def test_workspace_install_status_never_returns_process_output(monkeypatch):
    monkeypatch.setitem(
        apps._install_jobs,
        "workspace-job",
        {
            "status": "error",
            "step": "pull failed: registry-token=secret",
            "step_key": "image_pull_failed",
            "ok": False,
            "app_id": "workspace",
            "app_name": "Workspace",
            "output": "/backup/private registry-token=secret",
        },
    )

    result = apps.install_status("workspace-job")

    assert result["step_key"] == "image_pull_failed"
    assert "output" not in result
    assert "step" not in result


def test_workspace_compose_cannot_be_replaced_from_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))

    try:
        apps.save_compose("workspace", "services: {web: {image: attacker:latest}}")
    except ValueError as exc:
        assert "Runvard" in str(exc)
    else:
        raise AssertionError("managed Workspace Compose was replaceable")


def test_workspace_bundle_reader_rejects_symlink(monkeypatch, tmp_path):
    bundle = _bundle(tmp_path)
    target = bundle / "real-compose.yaml"
    target.write_text("services: {}\n")
    (bundle / "compose.yaml").unlink()
    (bundle / "compose.yaml").symlink_to(target)
    monkeypatch.setattr(apps, "WORKSPACE_BUNDLE_DIR", str(bundle))

    try:
        apps.get_app("workspace")
    except ValueError as exc:
        assert "Symlinks" in str(exc)
    else:
        raise AssertionError("symlinked managed bundle was accepted")


def test_runvard_update_preinstalls_workspace_without_overwriting_secrets():
    installer = (ROOT / "scripts" / "install-full.sh").read_text()
    assert "install_workspace_catalog_app" in installer
    assert 'install -m 0600 "${bundle}/compose.yaml"' in installer
    assert 'if [ ! -e "${secret_dir}/${name}" ]' in installer
    assert 'openssl rand -base64 48' in installer
