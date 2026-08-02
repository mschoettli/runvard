import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

import server
from modules import apps


def _papervard():
    return next(app for app in apps.CATALOG if app["id"] == "papervard")


def _prepare_catalog(monkeypatch, tmp_path, unavailable=()):
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(apps.docker_mgr, "list_compose_projects", lambda: [])
    monkeypatch.setattr(
        apps,
        "_port_is_available",
        lambda port: port not in set(unavailable),
    )


def test_papervard_is_a_curated_productivity_app(monkeypatch, tmp_path):
    _prepare_catalog(monkeypatch, tmp_path)

    catalog = apps.get_catalog()
    papervard = next(app for app in catalog["apps"] if app["id"] == "papervard")

    assert papervard["name"] == "Papervard"
    assert papervard["category"] == "Produktivität"
    assert papervard["icon"] == "/static/papervard.svg"
    assert papervard["port"] == 3000
    assert papervard["first_party"] is True
    assert next(app for app in catalog["apps"] if app["id"] == "jellyfin")["first_party"] is False
    assert _papervard()["install_timeout"] >= 600


def test_papervard_install_draft_is_image_only_and_secure(monkeypatch, tmp_path):
    _prepare_catalog(monkeypatch, tmp_path)

    first = apps.get_app("papervard")
    second = apps.get_app("papervard")
    compose = yaml.safe_load(first["compose"])
    services = compose["services"]

    assert set(services) == {"papervard", "worker", "db", "tika", "onlyoffice"}
    assert all("build" not in service for service in services.values())
    assert services["papervard"]["image"] == "ghcr.io/mschoettli/papervard:latest"
    assert services["worker"]["image"] == "ghcr.io/mschoettli/papervard:latest"
    assert "watchtower" not in services
    assert "samba" not in services

    app_env = services["papervard"]["environment"]
    db_env = services["db"]["environment"]
    assert app_env["PAPERVARD_UPDATE_MODE"] == "external"
    assert app_env["NEXT_PUBLIC_ONLYOFFICE_URL"] == "auto:8081"
    assert len(app_env["SEED_ADMIN_PASSWORD"]) >= 32
    assert len(app_env["ONLYOFFICE_JWT_SECRET"]) >= 32
    assert len(db_env["POSTGRES_PASSWORD"]) >= 32
    assert "replace-with" not in first["compose"]
    assert first["compose"] != second["compose"]

    assert services["papervard"]["volumes"] == [
        "./config:/config",
        "./data:/data",
    ]
    health_command = services["papervard"]["healthcheck"]["test"][3]
    assert "process.env.HOSTNAME" in health_command
    assert "127.0.0.1" not in health_command
    assert services["db"]["volumes"] == ["./data:/papervard-data"]
    assert first["install_info"]["credentials"] == [
        {
            "label": "Admin email",
            "value": "admin@papervard.local",
        },
        {
            "label": "Initial admin password",
            "value": app_env["SEED_ADMIN_PASSWORD"],
            "secret": True,
        },
    ]


def test_papervard_update_migrates_loopback_healthcheck():
    old_healthcheck = (
        "fetch('http://127.0.0.1:3000/login')"
        ".then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
    )
    content = f'''services:
  papervard:
    image: ghcr.io/mschoettli/papervard:latest
    healthcheck:
      test: ["CMD", "node", "-e", "{old_healthcheck}"]
'''

    updated = apps._normalize_app_compose(_papervard(), content)

    assert "127.0.0.1" not in updated
    assert "process.env.HOSTNAME" in updated
    assert yaml.safe_load(updated)["services"]["papervard"]["healthcheck"]["test"][3]


def test_papervard_allocates_both_web_ports(monkeypatch, tmp_path):
    _prepare_catalog(monkeypatch, tmp_path, unavailable={3000, 8081})

    compose = yaml.safe_load(apps.get_app("papervard")["compose"])

    assert compose["services"]["papervard"]["ports"] == ["3001:3000"]
    assert compose["services"]["onlyoffice"]["ports"] == ["8082:80"]
    assert (
        compose["services"]["papervard"]["environment"]["NEXT_PUBLIC_ONLYOFFICE_URL"]
        == "auto:8082"
    )


def test_update_check_uses_docker_manifest_without_buildx(monkeypatch, tmp_path):
    remote_config_digest = ["sha256:new"]
    calls = []

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == str(tmp_path)
        calls.append(command)
        if command == ["docker", "compose", "config", "--images"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "ghcr.io/mschoettli/papervard:latest\n"
                    "ghcr.io/mschoettli/papervard:latest\n"
                ),
                stderr="",
            )
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout='"sha256:old"\n',
                stderr="",
            )
        if command[:4] == ["docker", "manifest", "inspect", "--verbose"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([
                    {
                        "Descriptor": {
                            "platform": {"architecture": "amd64", "os": "linux"},
                        },
                        "OCIManifest": {
                            "config": {"digest": remote_config_digest[0]},
                        },
                    },
                ]),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(apps.subprocess, "run", fake_run)

    assert apps._check_image_update(str(tmp_path)) is True
    remote_config_digest[0] = "sha256:old"
    assert apps._check_image_update(str(tmp_path)) is False
    assert calls.count([
        "docker",
        "manifest",
        "inspect",
        "--verbose",
        "ghcr.io/mschoettli/papervard:latest",
    ]) == 2
    assert not any(command[:2] == ["docker", "buildx"] for command in calls)


def test_check_updates_reports_registry_inspection_errors(monkeypatch, tmp_path):
    apps_dir = tmp_path / "apps"
    app_dir = apps_dir / "papervard"
    app_dir.mkdir(parents=True)
    (app_dir / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(apps, "APPS_DIR", str(apps_dir))
    monkeypatch.setattr(apps, "UPDATE_CACHE", str(tmp_path / "updates.json"))
    monkeypatch.setattr(
        apps,
        "_check_image_update",
        lambda path: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )

    result = apps.check_updates(force=True)

    assert result["updates"] == []
    assert result["errors"] == {"papervard": "registry unavailable"}


def test_app_install_rejects_port_conflicts_before_writing(monkeypatch, tmp_path):
    _prepare_catalog(monkeypatch, tmp_path)
    monkeypatch.setattr(
        apps.docker_mgr,
        "check_compose_ports",
        lambda name, content: {
            "ok": False,
            "conflicts": [{"service": "onlyoffice", "port": 8081}],
        },
    )

    with pytest.raises(ValueError, match="8081"):
        apps.install("papervard", apps.get_app("papervard")["compose"])

    assert not (tmp_path / "papervard" / "docker-compose.yml").exists()


def test_existing_catalog_apps_keep_their_legacy_install_path(monkeypatch, tmp_path):
    _prepare_catalog(monkeypatch, tmp_path)
    monkeypatch.setattr(
        apps.docker_mgr,
        "check_compose_ports",
        lambda name, content: {
            "ok": False,
            "conflicts": [{"service": "pihole", "port": 53}],
        },
    )

    class DormantThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(apps.threading, "Thread", DormantThread)

    result = apps.install("pihole", apps.get_app("pihole")["compose"])

    assert result["job_id"].startswith("pihole_")


def test_papervard_install_prepares_persistent_directories(monkeypatch, tmp_path):
    _prepare_catalog(monkeypatch, tmp_path)
    monkeypatch.setattr(
        apps.docker_mgr,
        "check_compose_ports",
        lambda name, content: {"ok": True, "conflicts": []},
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

    draft = apps.get_app("papervard")
    job = apps.install("papervard", draft["compose"])

    assert apps.install_status(job["job_id"])["ok"] is True
    assert (tmp_path / "papervard" / "config").is_dir()
    assert (tmp_path / "papervard" / "data").is_dir()


def test_install_form_exposes_generated_setup_information():
    html = Path("static/index.html").read_text()

    assert "draft.app.install_info" in html
    assert "Initial admin password" in html
    assert "['papervard','Lokales Dokumentenarchiv" in html


def test_app_store_installed_sort_keeps_first_party_apps_on_top():
    html = Path("static/index.html").read_text()
    match = re.search(
        r"function sortAppsCatalog\(items\)\{.*?^\}",
        html,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert match
    assert 'value="installed"' in html
    assert "appsSetSort" in html

    script = f"""
let appsSort = 'installed';
{match.group(0)}
const apps = [
  {{id:'catalog-uninstalled', first_party:false, installed:false}},
  {{id:'catalog-installed', first_party:false, installed:true}},
  {{id:'papervard', first_party:true, installed:false}},
  {{id:'second-installed', first_party:false, installed:true}},
];
const result = sortAppsCatalog(apps).map(app => app.id);
const expected = ['papervard', 'catalog-installed', 'second-installed', 'catalog-uninstalled'];
if (JSON.stringify(result) !== JSON.stringify(expected)) {{
  throw new Error(`Unexpected app order: ${{JSON.stringify(result)}}`);
}}
"""
    subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_readonly_users_cannot_read_app_compose_secrets(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.apps,
        "get_app",
        lambda app_id: {"id": app_id, "compose": "POSTGRES_PASSWORD=secret"},
    )
    client = TestClient(server.app)
    client.cookies.set(
        server.COOKIE_NAME,
        server.make_token("reader", 3600, "readonly"),
    )

    response = client.get("/api/apps/get?app_id=papervard")

    assert response.status_code == 403
