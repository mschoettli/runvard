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
