import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "static" / "index.html"
MODERN_JS = ROOT / "static" / "docker-modern.js"
MODERN_CSS = ROOT / "static" / "modern-theme.css"


def run_modern_docker_helper(expression: str):
    assert MODERN_JS.exists(), "the modern Docker grouping helper must exist"
    script = f"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync({json.dumps(str(MODERN_JS))}, 'utf8'));
const result = {expression};
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_modern_docker_groups_compose_services_as_one_app():
    groups = run_modern_docker_helper(
        "RunvardDockerModern.groupContainers(["
        "{id:'web',name:'papervard-web',state:'running',app_group:{id:'compose:papervard',type:'compose',project:'papervard',service:'web'}},"
        "{id:'db',name:'papervard-db',state:'running',app_group:{id:'compose:papervard',type:'compose',project:'papervard',service:'db'}},"
        "{id:'proxy',name:'caddy',state:'exited',app_group:{id:'container:caddy',type:'container'}}"
        "])"
    )

    assert [group["key"] for group in groups] == [
        "compose:papervard",
        "container:caddy",
    ]
    assert [container["id"] for container in groups[0]["containers"]] == [
        "web",
        "db",
    ]
    assert groups[0]["kind"] == "compose"
    assert groups[0]["name"] == "papervard"


def test_modern_docker_reports_partial_and_error_group_states():
    states = run_modern_docker_helper(
        "["
        "RunvardDockerModern.groupState([{state:'running'},{state:'exited'}]),"
        "RunvardDockerModern.groupState([{state:'running'},{state:'dead'}]),"
        "RunvardDockerModern.groupState([{state:'running'},{state:'running'}]),"
        "RunvardDockerModern.groupState([{state:'exited'}])"
        "]"
    )

    assert states == ["partial", "error", "running", "stopped"]


def test_modern_docker_aggregates_app_resource_values():
    totals = run_modern_docker_helper(
        "RunvardDockerModern.aggregateStats("
        "[{id:'web'},{id:'db'}],"
        "{web:{cpu_percent:2.5,mem_used:100,mem_limit:400},db:{cpu_percent:1.5,mem_used:200,mem_limit:600}}"
        ")"
    )

    assert totals == {
        "cpu_percent": 4,
        "mem_used": 300,
        "mem_limit": 1000,
        "mem_percent": 30,
        "available": 2,
    }


def test_modern_docker_renderer_is_isolated_from_original_theme():
    html = INDEX_HTML.read_text()
    css = MODERN_CSS.read_text()

    assert '<script src="/static/docker-modern.js"></script>' in html
    assert "if(isModernUi())return renderDockerModern(body,tab);" in html
    assert "async function renderDockerModern(body,tab)" in html
    assert 'class="modern-docker-app-card"' in html
    assert '<details class="modern-docker-app-details"' in html
    assert 'html[data-ui-theme="modern"] .modern-docker-app-card' in css
    assert 'html[data-ui-theme="modern"] .modern-docker-app-details' in css


def test_modern_docker_advanced_tabs_use_quiet_tool_rows():
    html = INDEX_HTML.read_text()
    css = MODERN_CSS.read_text()

    assert "function modernComposeProjectRows(projects)" in html
    assert "function modernDockerImageRows(images)" in html
    assert "function modernDockerVolumeRows(volumes)" in html
    assert "return renderDockerAdvanced(body,tab);" not in html.split(
        "async function renderDockerModern(body,tab){", 1
    )[1].split("async function renderDocker(body,tab){", 1)[0]
    assert 'class="modern-docker-tool-row"' in html
    assert 'html[data-ui-theme="modern"] .modern-docker-tool-row' in css


def test_modern_docker_mobile_summary_stays_compact():
    html = INDEX_HTML.read_text()
    css = MODERN_CSS.read_text()

    assert 'class="nx-summary-grid modern-docker-summary"' in html
    assert 'html[data-ui-theme="modern"] .modern-docker-summary {' in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in css
    assert 'class="nx-summary-grid modern-docker-tool-summary"' in html
    assert 'html[data-ui-theme="modern"] .modern-docker-tool-summary {' in css
