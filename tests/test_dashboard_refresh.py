import subprocess
from pathlib import Path


INDEX = Path(__file__).resolve().parents[1] / 'static/index.html'


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
const dashTileIconHtml=()=>'',dashTileLinkHtml=()=>'<a href="http://server:8088">Web</a>';
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
