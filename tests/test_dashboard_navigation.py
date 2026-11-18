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
window.open = (...args) => {{opened.push(args[0]);return {{opener: null}};}};
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


@pytest.mark.parametrize("blocked", [True, False])
def test_dashboard_navigation_falls_back_when_browser_refuses_new_tab(blocked):
    index = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    open_tile = index.split("window.dashOpen=", 1)[1].split("\n};", 1)[0]
    script = f"""
const assert = require('node:assert/strict');
const window = globalThis;
const fallback = [], opened = [], details = [];
const newTab = {{opener: window}};
const blocked = {str(blocked).lower()};
const document = {{querySelector: () => null}};
const dashTiles = [
  {{id:'web', type:'docker', port:8088}},
  {{id:'custom', type:'custom', url:'https://example.test'}},
  {{id:'no-port', type:'docker', port:0}},
];
window.location = {{assign: url => fallback.push(url)}};
window.open = (url, target) => {{
  assert.equal(target, '_blank');
  opened.push(url);
  return blocked ? null : newTab;
}};
function dashIsServiceTile(t) {{return t.type === 'docker';}}
function dashTileUrl(t) {{return 'http://server.test:' + t.port;}}
window.dashLogs = id => details.push(id);
window.dashOpen = {open_tile}\n}};
dashOpen('web');
dashOpen('custom');
dashOpen('no-port');
dashOpen('missing');
assert.deepEqual(opened, ['http://server.test:8088', 'https://example.test']);
assert.deepEqual(fallback, blocked ? opened : []);
assert.deepEqual(details, ['no-port']);
if (!blocked) assert.equal(newTab.opener, null);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_native_dashboard_links_preserve_modified_clicks():
    index = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    click = index.split("window.dashClick=", 1)[1].split("\n};", 1)[0]
    script = f"""
const assert = require('node:assert/strict');
const window = globalThis;
const opened = [];
let prevented = 0;
window.dashOpen = id => opened.push(id);
window.dashClick = {click}\n}};
const event = {{
  target: {{closest: selector => selector === '.dash-tile-link' ? {{}} : null}},
  preventDefault: () => prevented++,
}};
dashClick(event, 'web');
assert.deepEqual(opened, []);
assert.equal(prevented, 0);
for (const modifier of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey']) {{
  dashClick({{...event, [modifier]:true}}, 'web');
}}
dashClick({{...event, button:1}}, 'web');
assert.deepEqual(opened, []);
assert.equal(prevented, 0);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("renderer_index", [0, 1])
def test_dashboard_renderers_emit_native_app_links(renderer_index):
    import json
    from html.parser import HTMLParser

    index = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    renderer = index.split("function renderDashboard(){")[renderer_index + 1].split("\n}", 1)[0]
    script = f"""
const grid = {{innerHTML:''}}, section = {{style:{{}}}};
const $ = selector => selector === '#dash-grid' ? grid : section;
const esc = value => String(value).replaceAll('&','&amp;').replaceAll('"','&quot;');
const dashTiles = [
  {{id:'app',name:'App',type:'app',port:8096}},
  {{id:'compose',name:'Compose',type:'compose',port:8088}},
  {{id:'docker',name:'Docker',type:'docker',port:8123,host:'remote.test'}},
  {{id:'custom',name:'Custom',type:'custom',url:'https://example.test/?a=1&b=2'}},
  {{id:'no-port',name:'Logs',type:'docker',port:0}},
];
function dashIsServiceTile(t) {{return ['app','compose','docker'].includes(t.type);}}
function dashTileAddress(t) {{return (t.host||'server.test')+':'+t.port;}}
function dashTileUrl(t) {{return 'http://'+dashTileAddress(t);}}
function dashTileStatus() {{return {{cls:'run',title:'Running'}};}}
function dashTileIconHtml() {{return '';}}
function dashTileFaviconUrl() {{return '';}}
function renderDashboard() {{{renderer}\n}}
renderDashboard();
process.stdout.write(JSON.stringify(grid.innerHTML));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    class TileParser(HTMLParser):
        tiles = []

        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            if 'dash-tile' in attrs.get('class', '').split():
                self.tiles.append((tag, attrs))

    parser = TileParser()
    parser.feed(json.loads(result.stdout))
    assert len(parser.tiles) == 5
    for (tag, attrs), url in zip(parser.tiles[:4], [
        'http://server.test:8096', 'http://server.test:8088',
        'http://remote.test:8123', 'https://example.test/?a=1&b=2',
    ]):
        assert tag == 'a'
        assert attrs['href'] == url
        assert attrs['target'] == '_blank'
        assert {'noopener', 'noreferrer'} <= set(attrs['rel'].split())
        assert 'dash-tile-link' in attrs['class'].split()
    assert parser.tiles[-1][0] == 'div'


def test_explicit_main_url_overrides_inferred_port():
    index = (Path(__file__).resolve().parents[1] / 'static/index.html').read_text()
    helpers = 'function dashTileHost(t){' + index.split('function dashTileHost(t){', 1)[1].split('function dashTileStatus(t){', 1)[0]
    script = """
const assert = require('node:assert/strict');
const location = {hostname:'server.test'};
""" + helpers + """
const tile = {type:'docker',port:8080,url:'https://server.test:8001/admin'};
assert.equal(dashTileUrl(tile),'https://server.test:8001/admin');
assert.equal(dashTileAddress(tile),'server.test:8001/admin');
assert.equal(dashTileUrl({type:'docker',port:8001}),'http://server.test:8001');
"""
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
