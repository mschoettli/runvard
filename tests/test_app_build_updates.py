import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from modules import apps


BASE = "nextcloud:34-apache"
IMAGE = "nextcloud-runvard:34"


def test_existing_nextcloud_custom_image_is_migrated_without_changing_data():
    original = {"services": {"nextcloud": {
        "image": "nextcloud-runvard:34.0.5",
        "ports": ["192.168.178.60:2121:80"],
        "volumes": ["/srv/storage/apps/nextcloud/data:/var/www/html"],
        "environment": {"LANG": "C.UTF-8"},
        "networks": ["default", "nextcloud-db"],
    }, "redis": {"image": "redis:7.4-alpine"}},
        "networks": {"nextcloud-db": {"external": True}}}
    app = next(app for app in apps.CATALOG if app["id"] == "nextcloud")
    result = yaml.safe_load(apps._normalize_app_compose(app, yaml.safe_dump(original)))
    service = result["services"]["nextcloud"]
    assert service["x-runvard-base-image"] == BASE
    assert service["pull_policy"] == "never"
    dockerfile = Path(service["build"]["context"]) / "Dockerfile"
    assert dockerfile.is_file()
    assert 'LABEL io.runvard.base.image="${RUNVARD_BASE_IMAGE}"' in dockerfile.read_text()
    for key, value in original["services"]["nextcloud"].items():
        assert service[key] == value
    assert result["services"]["redis"] == original["services"]["redis"]
    assert result["networks"] == original["networks"]


@pytest.mark.parametrize("service", [
    {"image": "nextcloud:latest"},
    {"image": "nextcloud-runvard:34.0.5", "build": "./custom"},
    {"image": "another-custom-nextcloud:34"},
])
def test_nextcloud_migration_keeps_other_installations_unchanged(service):
    content = yaml.safe_dump({"services": {"nextcloud": service}})
    app = next(app for app in apps.CATALOG if app["id"] == "nextcloud")
    assert apps._normalize_app_compose(app, content) == content


def setup_compose(tmp_path):
    path = tmp_path / "nextcloud"
    path.mkdir()
    content = yaml.safe_dump({"services": {
        "nextcloud": {"image": IMAGE, "build": {"context": "."},
                      "x-runvard-base-image": BASE},
        "redis": {"image": "redis:7.4-alpine"},
    }})
    (path / "docker-compose.yml").write_text(content)
    return path


def docker_stub(monkeypatch, recorded_base, fail_pull=False, fail_build=False):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        out = ""
        code = 0
        if command[:4] == ["docker", "compose", "config", "--images"]:
            out = IMAGE + "\nredis:7.4-alpine\n"
        elif command[:3] == ["docker", "image", "inspect"]:
            if command[3] == IMAGE:
                out = json.dumps({"Os": "linux", "Architecture": "amd64",
                                  "Config": {"Labels": {"io.runvard.base.image": recorded_base}}})
            else:
                out = json.dumps("sha256:redis")
        elif command[:4] == ["docker", "manifest", "inspect", "--verbose"]:
            if command[4] == IMAGE:
                raise AssertionError("local build must not be queried in the registry")
            config = "sha256:base" if command[4] == BASE else "sha256:redis"
            out = json.dumps([
                {"Descriptor": {"digest": "sha256:amd64", "platform": {"os": "linux", "architecture": "amd64"}},
                 "OCIManifest": {"config": {"digest": config}}},
                {"Descriptor": {"digest": "sha256:arm64", "platform": {"os": "linux", "architecture": "arm64"}},
                 "OCIManifest": {"config": {"digest": "sha256:arm"}}},
            ])
        elif command[:3] == ["docker", "compose", "pull"] and fail_pull:
            code = 1
        elif command[:3] == ["docker", "compose", "build"] and fail_build:
            code = 1
        return SimpleNamespace(returncode=code, stdout=out, stderr="failure" if code else "")

    monkeypatch.setattr(apps.subprocess, "run", run)
    monkeypatch.setattr(apps.time, "sleep", lambda _: None)
    monkeypatch.setattr(apps, "_running", lambda _: True)
    return calls


@pytest.mark.parametrize("recorded,expected", [
    (BASE + "@sha256:amd64", False),
    (BASE + "@sha256:old", True),
    (None, True),
])
def test_build_update_check_compares_upstream_and_checks_registry_services(monkeypatch, tmp_path, recorded, expected):
    path = setup_compose(tmp_path)
    calls = docker_stub(monkeypatch, recorded)
    assert apps._check_image_update(str(path)) is expected
    if not expected:
        assert ["docker", "manifest", "inspect", "--verbose", "redis:7.4-alpine"] in calls


def test_build_update_pins_platform_and_pulls_other_services(monkeypatch, tmp_path):
    setup_compose(tmp_path)
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(apps, "UPDATE_CACHE", str(tmp_path / "updates.json"))
    calls = docker_stub(monkeypatch, BASE + "@sha256:old")
    assert apps.action("nextcloud", "update")["ok"] is True
    pull = ["docker", "compose", "pull", "--ignore-buildable"]
    build = ["docker", "compose", "build", "--pull", "--build-arg",
             "RUNVARD_BASE_IMAGE=" + BASE + "@sha256:amd64", "nextcloud"]
    up = ["docker", "compose", "up", "-d"]
    assert pull in calls
    assert build in calls
    assert calls.index(pull) < calls.index(build) < calls.index(up)


@pytest.mark.parametrize("failure", ["pull", "build"])
def test_failed_build_update_does_not_recreate_or_clear_update(monkeypatch, tmp_path, failure):
    setup_compose(tmp_path)
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    cache = tmp_path / "updates.json"
    cache.write_text(json.dumps({"updates": ["nextcloud"]}))
    monkeypatch.setattr(apps, "UPDATE_CACHE", str(cache))
    calls = docker_stub(monkeypatch, BASE + "@sha256:old",
                        fail_pull=failure == "pull", fail_build=failure == "build")
    assert apps.action("nextcloud", "update")["ok"] is False
    assert ["docker", "compose", "up", "-d"] not in calls
    assert json.loads(cache.read_text())["updates"] == ["nextcloud"]


@pytest.mark.parametrize("action,command", [
    ("start", ["docker", "compose", "up", "--no-build", "-d"]),
    ("restart", ["docker", "compose", "restart"]),
])
def test_start_and_restart_keep_tracked_image_without_updating(monkeypatch, tmp_path, action, command):
    setup_compose(tmp_path)
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    calls = docker_stub(monkeypatch, BASE + "@sha256:amd64")
    assert apps.action("nextcloud", action)["ok"] is True
    assert calls == [command]


def test_upstream_without_matching_platform_stops_before_changes(monkeypatch, tmp_path):
    setup_compose(tmp_path)
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    calls = docker_stub(monkeypatch, BASE + "@sha256:old")
    run = apps.subprocess.run

    def no_platform(command, **kwargs):
        result = run(command, **kwargs)
        if command[:4] == ["docker", "manifest", "inspect", "--verbose"]:
            result.stdout = "[]"
        return result

    monkeypatch.setattr(apps.subprocess, "run", no_platform)
    with pytest.raises(RuntimeError, match="platform unavailable"):
        apps.action("nextcloud", "update")
    assert not any(command[:2] == ["docker", "compose"] for command in calls)


def test_update_check_migrates_legacy_nextcloud_in_memory(monkeypatch, tmp_path):
    path = setup_compose(tmp_path)
    content = yaml.safe_load((path / "docker-compose.yml").read_text())
    service = content["services"]["nextcloud"]
    del service["build"]
    del service["x-runvard-base-image"]
    original = yaml.safe_dump(content)
    (path / "docker-compose.yml").write_text(original)
    docker_stub(monkeypatch, None)
    assert apps._check_image_update(str(path)) is True
    assert (path / "docker-compose.yml").read_text() == original


def test_legacy_nextcloud_update_preserves_extra_runtime_bind_mounts(monkeypatch, tmp_path):
    path = setup_compose(tmp_path)
    data = yaml.safe_load((path / "docker-compose.yml").read_text())
    service = data["services"]["nextcloud"]
    del service["build"]
    del service["x-runvard-base-image"]
    service["container_name"] = "nextcloud"
    service["volumes"] = ["/srv/cloud:/var/www/html"]
    (path / "docker-compose.yml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(apps, "APPS_DIR", str(tmp_path))
    monkeypatch.setattr(apps, "UPDATE_CACHE", str(tmp_path / "updates.json"))
    docker_stub(monkeypatch, None)
    run = apps.subprocess.run

    def with_mounts(command, **kwargs):
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps([
                {"Type": "bind", "Source": "/srv/cloud", "Destination": "/var/www/html", "RW": True},
                {"Type": "bind", "Source": "/srv/private", "Destination": "/external/private", "RW": False},
            ]))
        return run(command, **kwargs)

    monkeypatch.setattr(apps.subprocess, "run", with_mounts)
    assert apps.action("nextcloud", "update")["ok"] is True
    migrated = yaml.safe_load((path / "docker-compose.yml").read_text())
    assert migrated["services"]["nextcloud"]["volumes"] == [
        "/srv/cloud:/var/www/html",
        {"type": "bind", "source": "/srv/private", "target": "/external/private", "read_only": True},
    ]
