from types import SimpleNamespace

from modules import docker_mgr


def _container(name, labels=None):
    return SimpleNamespace(
        name=name,
        labels=labels or {},
        attrs={"Config": {"Labels": labels or {}}},
    )


def test_container_app_group_uses_compose_project_label():
    app = _container("papervard-app-1", {
        "com.docker.compose.project": "papervard",
        "com.docker.compose.service": "app",
    })
    db = _container("papervard-db-1", {
        "com.docker.compose.project": "papervard",
        "com.docker.compose.service": "db",
    })

    assert docker_mgr._container_app_group(app)["id"] == "compose:papervard"
    assert docker_mgr._container_app_group(db)["id"] == "compose:papervard"


def test_container_app_group_keeps_standalone_container_separate():
    jellyfin = _container("jellyfin")
    code_server = _container("code-server")

    assert docker_mgr._container_app_group(jellyfin)["id"] == "container:jellyfin"
    assert docker_mgr._container_app_group(code_server)["id"] == "container:code-server"


def test_check_compose_ports_reports_occupied_host_port(monkeypatch):
    monkeypatch.setattr(docker_mgr, "_compose_owned_ports", lambda name: set())
    monkeypatch.setattr(docker_mgr, "_port_is_available", lambda port: False)

    result = docker_mgr.check_compose_ports("demo", """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
""")

    assert result["ok"] is False
    assert result["conflicts"] == [{
        "service": "web",
        "port": 8080,
        "target": 80,
        "reason": "in_use",
    }]


def test_check_compose_ports_allows_existing_project_port(monkeypatch):
    monkeypatch.setattr(docker_mgr, "_compose_owned_ports", lambda name: {8080})
    monkeypatch.setattr(docker_mgr, "_port_is_available", lambda port: False)

    result = docker_mgr.check_compose_ports("demo", """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
""")

    assert result["ok"] is True
    assert result["conflicts"] == []


def test_check_compose_ports_reports_duplicate_yaml_ports(monkeypatch):
    monkeypatch.setattr(docker_mgr, "_compose_owned_ports", lambda name: set())
    monkeypatch.setattr(docker_mgr, "_port_is_available", lambda port: True)

    result = docker_mgr.check_compose_ports("demo", """
services:
  web:
    image: nginx
    ports:
      - "8080:80"
  api:
    image: nginx
    ports:
      - "8080:8080"
""")

    assert result["ok"] is False
    assert [item["reason"] for item in result["conflicts"]] == ["duplicate", "duplicate"]
    assert [item["service"] for item in result["conflicts"]] == ["web", "api"]


def test_list_networks_marks_builtin_and_unused(monkeypatch):
    class FakeNetwork:
        def __init__(self, name, containers=None, subnet=""):
            self.attrs = {
                "Id": f"{name}-1234567890",
                "Name": name,
                "Driver": "bridge",
                "Scope": "local",
                "Containers": containers or {},
                "IPAM": {"Config": [{"Subnet": subnet}]} if subnet else {"Config": []},
            }

    client = SimpleNamespace(networks=SimpleNamespace(list=lambda: [
        FakeNetwork("project_default", subnet="172.20.0.0/16"),
        FakeNetwork("bridge"),
        FakeNetwork("active_net", {"abc": {"Name": "web"}}),
    ]))
    monkeypatch.setattr(docker_mgr, "_get_client", lambda: client)

    result = docker_mgr.list_networks()

    assert [n["name"] for n in result] == ["bridge", "active_net", "project_default"]
    assert result[0]["builtin"] is True
    assert result[0]["unused"] is False
    assert result[1]["unused"] is False
    assert result[2]["unused"] is True
    assert result[2]["subnets"] == ["172.20.0.0/16"]


def test_list_networks_falls_back_to_cli_when_sdk_sees_stale_network(monkeypatch):
    monkeypatch.setattr(
        docker_mgr,
        "_list_networks_sdk",
        lambda: (_ for _ in ()).throw(RuntimeError("did not find cr")),
    )

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[:3] == ["docker", "network", "ls"]:
            return SimpleNamespace(
                returncode=0,
                stdout='{"ID":"abc123","Name":"bridge","Driver":"bridge","Scope":"local"}\n'
                       '{"ID":"def456","Name":"old_default","Driver":"bridge","Scope":"local"}\n'
                       '{"ID":"gone","Name":"cr","Driver":"bridge","Scope":"local"}\n',
                stderr="",
            )
        if cmd[:3] == ["docker", "network", "inspect"] and cmd[3] == "bridge":
            return SimpleNamespace(
                returncode=0,
                stdout='[{"Id":"abc123","Name":"bridge","Driver":"bridge","Scope":"local","Containers":{"x":{}},"IPAM":{"Config":[]}}]',
                stderr="",
            )
        if cmd[:3] == ["docker", "network", "inspect"] and cmd[3] == "old_default":
            return SimpleNamespace(
                returncode=0,
                stdout='[{"Id":"def456","Name":"old_default","Driver":"bridge","Scope":"local","Containers":{},"IPAM":{"Config":[{"Subnet":"172.20.0.0/16"}]}}]',
                stderr="",
            )
        if cmd[:3] == ["docker", "network", "inspect"] and cmd[3] == "cr":
            return SimpleNamespace(returncode=1, stdout="", stderr="did not find cr")
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_mgr.subprocess, "run", fake_run)

    result = docker_mgr.list_networks()

    assert [n["name"] for n in result] == ["bridge", "old_default"]
    assert result[0]["builtin"] is True
    assert result[1]["unused"] is True
    assert result[1]["subnets"] == ["172.20.0.0/16"]


def test_prune_networks_returns_deleted_count(monkeypatch):
    monkeypatch.setattr(docker_mgr, "list_networks", lambda: [
        {"name": "bridge", "builtin": True, "unused": False},
        {"name": "active_net", "builtin": False, "unused": False},
        {"name": "old_default", "builtin": False, "unused": True},
        {"name": "legacy_net", "builtin": False, "unused": True},
    ])
    removed = []

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[:3] == ["docker", "network", "rm"]
        removed.append(cmd[3])
        return SimpleNamespace(returncode=0, stdout=cmd[3] + "\n", stderr="")

    monkeypatch.setattr(docker_mgr.subprocess, "run", fake_run)

    result = docker_mgr.prune_networks()

    assert removed == ["old_default", "legacy_net"]
    assert result == {
        "ok": True,
        "deleted": ["old_default", "legacy_net"],
        "deleted_count": 2,
        "skipped": [],
    }


def test_prune_networks_skips_already_removed_network(monkeypatch):
    monkeypatch.setattr(docker_mgr, "list_networks", lambda: [
        {"name": "old_default", "builtin": False, "unused": True},
    ])

    def fake_run(cmd, capture_output, text, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="did not found cr at the end bound")

    monkeypatch.setattr(docker_mgr.subprocess, "run", fake_run)

    assert docker_mgr.prune_networks() == {
        "ok": True,
        "deleted": [],
        "deleted_count": 0,
        "skipped": ["old_default"],
    }


def test_prune_networks_treats_stale_list_error_as_clean(monkeypatch):
    monkeypatch.setattr(
        docker_mgr,
        "list_networks",
        lambda: (_ for _ in ()).throw(RuntimeError("did not found cr at the end bound")),
    )

    assert docker_mgr.prune_networks() == {
        "ok": True,
        "deleted": [],
        "deleted_count": 0,
        "skipped": [],
    }


def test_prune_networks_raises_unexpected_cli_error(monkeypatch):
    monkeypatch.setattr(docker_mgr, "list_networks", lambda: [
        {"name": "old_default", "builtin": False, "unused": True},
    ])

    def fake_run(cmd, capture_output, text, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="permission denied")

    monkeypatch.setattr(docker_mgr.subprocess, "run", fake_run)

    try:
        docker_mgr.prune_networks()
    except RuntimeError as exc:
        assert str(exc) == "permission denied"
    else:
        raise AssertionError("expected RuntimeError")
