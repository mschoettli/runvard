from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "static" / "index.html"
).read_text()


def test_docker_compose_edit_uses_compose_modal_path():
    dash_compose = INDEX_HTML.split(
        "window.dashCompose=async id=>{", 1
    )[1].split("window.dashFiles=", 1)[0]

    assert "if(isCompose)return composeEdit(project);" in dash_compose


def _function_source(name: str, next_name: str) -> str:
    return INDEX_HTML.split(f"function {name}", 1)[1].split(next_name, 1)[0]


def test_app_install_modal_uses_simple_form_and_yaml_modes():
    modal = _function_source("appComposeFormOpen", "window.appCopyInstallValue")

    assert "uiText('Form')" in modal
    assert "uiText('YAML')" in modal
    assert "Raw YAML" not in INDEX_HTML
    assert 'class="service-summary"' not in modal
    assert "app-compose-preview-card" not in modal
    assert 'class="compose-install-close"' in modal


def test_app_install_form_keeps_all_simple_compose_fields_editable():
    modal = _function_source("appComposeFormOpen", "window.appCopyInstallValue")
    volumes = _function_source("appInstallRenderVolumes", "function appInstallRenderReadonlyVolumes")
    refresh = _function_source("appInstallRefresh", "window.appInstallSetVolumeMount")

    for field_id in (
        "app-compose-service",
        "app-compose-container",
        "app-compose-image",
        "app-compose-restart",
        "app-port-host-${i}",
        "app-port-container-${i}",
        "app-env-key-${i}",
        "app-env-value-${i}",
    ):
        assert field_id in modal

    assert "app-volume-drive-${i}" in volumes
    assert "app-volume-source-${i}" in volumes
    assert "app-volume-target-${i}" in volumes
    assert "app-volume-mode-${i}" in volumes
    assert "isExpertMode()?'':'readonly'" not in volumes
    assert "e.key=$(`#app-env-key-${i}`)?.value.trim()||e.key" in refresh
