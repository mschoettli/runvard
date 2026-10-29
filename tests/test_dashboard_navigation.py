import subprocess
from pathlib import Path

import pytest

from modules import dashboard


@pytest.mark.parametrize("tile_id,ports,expected", [
    ("container:web", ["8088:80/tcp"], 8088),
    ("compose:example", ["5432:5432/tcp", "8123:80/tcp"], 8123),
    ("container:web", ["80/tcp", "8088:80/udp"], 0),
    ("container:web", ["5432:5432/tcp"], 0),
])
def test_docker_tile_recovers_missing_web_port(monkeypatch, tile_id, ports, expected):
    monkeypatch.setattr(dashboard, "_load", lambda: {"tiles": [
        {"id": tile_id, "type": "docker", "port": 0},
    ]})
    monkeypatch.setattr(dashboard.docker_mgr, "list_containers", lambda: [
        {"name": "unrelated", "state": "running", "ports": ["9999:80/tcp"]},
        {"name": "web", "state": "running", "ports": ports,
         "app_group": {"id": tile_id}},
    ])
    assert dashboard.get_dashboard()["tiles"][0]["port"] == expected


def test_compose_tile_keeps_saved_port_if_compose_has_no_resolvable_port(monkeypatch, tmp_path):
    project = tmp_path / "example"
    project.mkdir()
    (project / "docker-compose.yml").write_text("services: {}")
    monkeypatch.setattr(dashboard, "COMPOSE_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard, "_compose_running", lambda path: True)
    monkeypatch.setattr(dashboard, "_load", lambda: {"tiles": [
        {"id": "compose:example", "type": "compose", "port": 8088},
    ]})
    assert dashboard.get_dashboard()["tiles"][0]["port"] == 8088


def test_dashboard_click_and_open_share_navigation_and_handle_missing_ports():
    index = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    click = index.split("window.dashClick=", 1)[1].split("\n};", 1)[0]
    open_tile = index.split("window.dashOpen=", 1)[1].split("\n};", 1)[0]
    helpers = index.split("function dashIsServiceTile(t){", 1)[1].split("\n}", 1)[0]
    script = f"""
const assert = require('node:assert/strict');
const window = globalThis;
const opened = [], details = [];
const document = {{querySelector: () => null}};
const dashTiles = [
  {{id:'web', type:'docker', port:8088}},
  {{id:'no-port', type:'docker', port:0}},
  {{id:'custom', type:'custom', url:'https://example.test'}},
];
function dashIsServiceTile(t) {{{helpers}}}
function dashTileUrl(t) {{return 'http://server.test:' + t.port;}}
window.open = (...args) => opened.push(args[0]);
window.dashLogs = id => details.push(id);
window.dashOpen = {open_tile}\n}};
window.dashClick = {click}\n}};
const event = {{target: {{closest: () => null}}}};
dashClick(event, 'web');
dashOpen('web');
dashClick(event, 'custom');
dashClick(event, 'no-port');
dashOpen('no-port');
dashClick({{target: {{closest: () => true}}}}, 'web');
dashClick(event, 'missing');
assert.deepEqual(opened, ['http://server.test:8088', 'http://server.test:8088', 'https://example.test']);
assert.deepEqual(details, ['no-port', 'no-port']);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_dashboard_renders_native_links_without_javascript_popup_dependency():
    import json
    from html.parser import HTMLParser

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                self.links.append(dict(attrs))

    index = (Path(__file__).resolve().parents[1] / 'static/index.html').read_text()
    render = index[index.rindex('function renderDashboard(){'):].split('// ═══ Lösch-X')[0]
    helper = index.split('function dashTileLinkHtml(t){', 1)
    helper_body = helper[1].split('\n}', 1)[0] if len(helper) == 2 else 'return "";'
    script = f"""
const grid={{innerHTML:''}},section={{style:{{}}}};
const $=s=>s==='#dash-grid'?grid:section;
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
const dashTiles=[{{id:'web',name:'Web',type:'docker',port:8088}},{{id:'custom',name:'Custom',type:'custom',url:'https://example.test/?a=1&b=2'}},{{id:'no-port',name:'No port',type:'docker',port:0}}];
const dashIsServiceTile=t=>['app','compose','docker'].includes(t.type);
const dashTileUrl=t=>'http://server.test:'+t.port;
const dashTileStatus=t=>({{cls:'run',title:'Running'}}),dashTileAddress=t=>'server.test:'+t.port;
const dashTileIconHtml=()=>'',dashTileFaviconUrl=()=>'';
function dashTileLinkHtml(t) {{{helper_body}\n}}
{render}
renderDashboard();console.log(JSON.stringify(grid.innerHTML));
"""
    result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    parser = Links()
    parser.feed(json.loads(result.stdout))
    assert [link['href'] for link in parser.links] == [
        'http://server.test:8088', 'https://example.test/?a=1&b=2',
    ]
    assert all(link.get('target') == '_blank' for link in parser.links)
    assert all('noopener' in link.get('rel', '') for link in parser.links)
    assert all('onclick' not in link for link in parser.links)
