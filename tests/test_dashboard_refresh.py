import json
import re
import subprocess
from pathlib import Path


INDEX = Path(__file__).resolve().parents[1] / 'static/index.html'


def test_dashboard_metrics_start_even_without_working_charts():
    html = INDEX.read_text()
    start = html.index('// ═══ Sparklines')
    end = html.index("api('/system/disks')", start)
    formatters = html[html.index("function fmtNumber("):html.index("const fmtVmMemory")]
    metrics = formatters + "\n" + html[start:end]
    harness = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
for (const Chart of [undefined, class { constructor() { throw Error('unavailable canvas'); } }]) {
  const fields = new Map();
  const context = {Chart, isModernUi: () => true, uiText: x => x, currentLanguage: () => 'de',
    $: id => { if (!fields.has(id)) fields.set(id, {}); return fields.get(id); },
    api: async () => ({cpu:{percent:42},memory:{used:0,total:1,percent:29},
                        network:{down_rate:1048576,up_rate:0}})};
  vm.createContext(context);
  await vm.runInContext(source + '\\npoll()', context);
  assert.equal(fields.get('#cpu-val').textContent, '42');
  assert.equal(fields.get('#net-val').textContent, '1,0');
  assert.equal(vm.runInContext('fmtBytes(1536)', context), '1,5 KB');
}
"""
    result = subprocess.run(
        ["node", "-e", "const source=" + json.dumps(metrics) + ";(async()=>{" + harness + "})()"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_chart_library_is_served_with_runvard():
    scripts = re.findall(r'<script src="([^"]+)"', INDEX.read_text())
    chart, = [url for url in scripts if "chart" in url.lower()]
    assert chart.startswith("/static/")
    bundle = INDEX.parent.parent / chart.split("?")[0].lstrip("/")
    result = subprocess.run(
        ["node", "-p", "require(process.argv[1]).version", str(bundle)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4.4.1"


def test_refresh_preserves_pressed_links_and_releases_old_sortable_instances():
    index = INDEX.read_text()
    shared = index.split('let dashTiles=[];', 1)[1].split('async function loadDashboard()', 1)[0]
    render = index[index.rindex('function renderDashboard(){'):].split('// ═══ Lösch-X')[0]
    script = r'''
const assert=require('node:assert/strict');
const listeners={},gridListeners={},timers=[];
let writes=0,created=0,destroyed=0,markup='';
const grid={addEventListener:(event,fn)=>gridListeners[event]=fn,querySelector:()=>true,
  querySelectorAll:()=>[{dataset:{id:'web'}}]};
Object.defineProperty(grid,'innerHTML',{get:()=>markup,set:value=>{writes++;markup=value;}});
const section={style:{}},$=s=>s==='#dash-grid'?grid:section;
const document={addEventListener:(event,fn)=>listeners[event]=fn};
const setTimeout=fn=>timers.push(fn);
const Sortable={create:()=>{created++;return {destroy:()=>destroyed++};}};
let dashTiles=[{id:'web',name:'Web',type:'docker',port:8088}];
const esc=s=>String(s??''),dashIsServiceTile=()=>true;
const dashTileStatus=t=>({cls:'run',title:'Running'}),dashTileAddress=()=> 'server:8088';
const dashTileFaviconUrl=()=>'',dashTileIconHtml=()=>'',dashTileLinkHtml=()=>'<a href="http://server:8088">Web</a>';
const post=()=>Promise.resolve(),loadDashboard=()=>{};
''' + shared + '\n' + render + r'''
renderDashboard();
renderDashboard();
assert.equal(writes,1,'an unchanged refresh must preserve the link DOM');
assert.equal(created,1,'an unchanged refresh must preserve the sorting instance');
gridListeners.pointerdown();
dashTiles[0].note='Changed while pointer is held';
renderDashboard();
assert.equal(writes,1,'a refresh must not remove the pressed link');
listeners.pointerup();
assert.equal(writes,1,'the click event must run before deferred rendering');
while(timers.length)timers.shift()();
assert.equal(writes,2,'changed data must render after pointer release');
assert.equal(destroyed,1,'the replaced Sortable must release its event handlers');
assert.equal(created,2);
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
