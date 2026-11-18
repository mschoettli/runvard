from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docker-apps" / "workspace"
COMPOSE_TEXT = (BUNDLE / "compose.yaml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TEXT)


def test_preserves_workspace_database_secret_and_volume_names():
    postgres = COMPOSE["services"]["postgres"]

    assert postgres["environment"] == {
        "POSTGRES_DB": "workspace",
        "POSTGRES_USER": "nushira_migration",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/workspace_migration_password",
    }
    assert set(COMPOSE["secrets"]) == {
        "workspace_migration_password",
        "workspace_app_password",
    }
    assert COMPOSE["volumes"]["workspace_postgres"]["name"] == "nushira_workspace_postgres"
    assert "workspace_postgres:/var/lib/postgresql" in postgres["volumes"]


def test_release_images_are_required_as_a_digest_bound_pair():
    services = COMPOSE["services"]

    assert services["web"]["image"].startswith("${WORKSPACE_WEB_IMAGE:?")
    assert services["migrator"]["image"].startswith("${WORKSPACE_MIGRATOR_IMAGE:?")
    assert services["migration-probe"]["image"] == services["migrator"]["image"]
    assert set(services["migrator"]["profiles"]) == {"update"}
    assert "workspace-web:" not in COMPOSE_TEXT
    assert "workspace-migrator:" not in COMPOSE_TEXT
    assert not re.search(r"(?:image:|:-)[^\n]*(?::latest|:main)(?:\s|$)", COMPOSE_TEXT)


def test_all_fixed_third_party_images_are_digest_pinned():
    variable_images = {"web", "migrator", "migration-probe", "bootstrap-development"}
    for name, service in COMPOSE["services"].items():
        if name not in variable_images:
            assert re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", service["image"])


def test_runtime_has_no_docker_git_or_registry_credentials():
    forbidden_mounts = ("docker.sock", ".git", "known_hosts", ".ssh", "docker/config.json")
    forbidden_environment = (
        "DOCKER_HOST",
        "GIT_ASKPASS",
        "GIT_SSH_COMMAND",
        "REGISTRY_PASSWORD",
        "REGISTRY_TOKEN",
    )

    for service in COMPOSE["services"].values():
        mounts = "\n".join(service.get("volumes", []))
        environment = service.get("environment", {})
        assert all(value not in mounts for value in forbidden_mounts)
        assert all(name not in environment for name in forbidden_environment)


def test_web_only_receives_application_secret_and_migrator_only_migration_secret():
    services = COMPOSE["services"]

    assert services["web"]["secrets"] == ["workspace_app_password"]
    assert services["migrator"]["secrets"] == ["workspace_migration_password"]
    assert "workspace_migration_password" not in services["web"]["secrets"]
    assert "workspace_app_password" not in services["migrator"]["secrets"]


def test_application_services_are_least_privilege_and_database_is_internal():
    services = COMPOSE["services"]

    for name in ("web", "migrator", "migration-probe", "bootstrap-development", "gateway"):
        service = services[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]

    assert COMPOSE["networks"]["workspace_internal"]["internal"] is True
    assert "ports" not in services["postgres"]
    assert services["gateway"]["ports"] == [
        "${WORKSPACE_BIND_ADDRESS:-127.0.0.1}:${WORKSPACE_PORT:-3100}:3100"
    ]
    assert "workspace_frontend" not in services["web"]["networks"]


def test_migration_probe_restores_into_an_isolated_ephemeral_database():
    services = COMPOSE["services"]
    probe_db = services["postgres-probe"]
    probe = services["migration-probe"]
    restore = (BUNDLE / "restore-probe.sh").read_text(encoding="utf-8")

    assert probe_db["profiles"] == ["update"]
    assert probe["profiles"] == ["update"]
    assert "workspace_postgres" not in "\n".join(probe_db["volumes"])
    assert any(value.startswith("/var/lib/postgresql:") for value in probe_db["tmpfs"])
    assert probe_db["networks"] == {"workspace_probe": {"aliases": ["postgres"]}}
    assert probe["networks"] == ["workspace_probe"]
    assert COMPOSE["networks"]["workspace_probe"]["internal"] is True
    assert "./probe/source.dump:/run/workspace-probe/source.dump:ro" in probe_db["volumes"]
    assert "pg_restore" in restore
    assert "--exit-on-error" in restore
    assert "--no-owner" in restore
    assert "--no-privileges" in restore


def test_postgres_initialization_is_read_only_and_fail_closed():
    postgres = COMPOSE["services"]["postgres"]
    init_script = (BUNDLE / "postgres-init.sh").read_text(encoding="utf-8")

    assert "./postgres-init.sh:/docker-entrypoint-initdb.d/010-workspace-roles.sh:ro" in postgres["volumes"]
    assert init_script.startswith("#!/bin/sh\nset -eu\n")
    assert "--set=ON_ERROR_STOP=1" in init_script
    assert "NOBYPASSRLS" in init_script
    assert "ALTER ROLE nushira_app PASSWORD :'app_password';" in init_script


def test_web_and_gateway_have_healthchecks():
    for name in ("web", "gateway"):
        healthcheck = COMPOSE["services"][name]["healthcheck"]
        assert "/health" in " ".join(healthcheck["test"])
        assert healthcheck["retries"] >= 10
