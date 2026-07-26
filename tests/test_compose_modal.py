from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "static" / "index.html"
).read_text()


def test_docker_compose_edit_uses_compose_modal_path():
    dash_compose = INDEX_HTML.split(
        "window.dashCompose=async id=>{", 1
    )[1].split("window.dashFiles=", 1)[0]

    assert "if(isCompose)return composeEdit(project);" in dash_compose
