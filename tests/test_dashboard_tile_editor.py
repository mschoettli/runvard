"""Behavior checks for the tile editor, executing its real JS in Node."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'static/index.html').read_text()
EDITOR = INDEX.split("const DASH_DEFAULT_ACCENT=", 1)[1].split("window.dashRemove=", 1)[0]
HARNESS = r"""
const assert = require('node:assert/strict');
const window = globalThis;
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {value:'',checked:false,dataset:{},style:{setProperty(){}},
    classList:{add(){},remove(){},toggle(){}},textContent:'',innerHTML:'',disabled:false,
    addEventListener(){},setAttribute(){},removeAttribute(){},querySelectorAll(){return [];},focus(){},reportValidity(){return true;},
    checkValidity(){return true;},setCustomValidity(){}});
  return nodes.get(id);
}
const $ = selector => node(selector.replace(/^#/,''));
const document = {querySelector:()=>null, activeElement:null};
const location = {hostname:'server.test'};
const dashTiles = [{id:'custom',type:'custom',name:'Example',url:'https://old.test',icon:'X',note:''}];
const dashPendingActions = new Map();
let language='en';
const uiText = text => text, tr = text => text, currentLanguage = ()=>language;
const esc = value => String(value ?? '');
let focused = '', copied = '', posts = [], closed = 0;
const openFormOverlay = selector => { focused = selector; };
const resetFormCardClasses = ()=>null;
const closeForm = ()=>{closed++;};
const toast = ()=>{}, loadDashboard = ()=>{};
const navigator = {clipboard:{writeText:async value=>{copied=value;}}};
const dashIsServiceTile = tile => tile.type==='docker';
const dashTileAddress = tile => 'server.test:'+tile.port;
const dashTileUrl = tile => 'http://'+dashTileAddress(tile);
const dashTileStatus = ()=>({cls:'run',title:'Running'});
let resolvePost;
let post = async (url,payload)=>{posts.push(payload);return new Promise(resolve=>resolvePost=resolve);};
"""


def run_js(body):
    body = body.replace('(async()=>{', '').replace('})();', '')
    script = HARNESS + "\nconst DASH_DEFAULT_ACCENT=" + EDITOR + '\n' + (
        'const watchdog=setTimeout(()=>{throw new Error("Editor action did not finish");},1000);'
        '(async()=>{' + body + '})().finally(()=>clearTimeout(watchdog));'
    )
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_url_edit_updates_preview_and_copy():
    run_js(r"""
(async()=>{
  dashEditTile('custom');
  $('#tile-edit-url').value='https://new.test/path';
  $('#tile-edit-address').dataset.address='old.test';
  $('#tile-edit-name').value='Example';
  $('#tile-edit-show-url').checked=true;
  dashUpdateEditPreview();
  assert.equal($('#tile-edit-preview-url').textContent,'new.test/path');
  await dashCopyTileAddress();
  assert.equal(copied,'new.test/path');
})();
""")


def test_editor_uses_shared_focus_and_favicon():
    run_js(r"""
  dashTiles[0]={id:'custom',type:'docker',port:8080,name:'App',icon:'X'};
  dashEditTile('custom');
  assert.equal(focused,'#tile-edit-name');
  $('#tile-edit-icon').value='X';
  dashUpdateEditPreview();
  assert.match($('#tile-edit-preview-icon').innerHTML,/server.test:8080\/favicon.ico/);
""")


def test_explicit_icon_input_cancels_pending_lookup():
    run_js(r"""
(async()=>{
  dashEditTile('custom');
  let finishSearch;
  dashFindSelfhstIcon=()=>new Promise(resolve=>finishSearch=resolve);
  $('#tile-edit-icon-search').value='plex';
  const pending=dashSearchTileIcon();
  $('#tile-edit-icon').value='https://my.test/icon.svg';
  dashHandleIconInput();
  finishSearch('https://search.test/plex.svg');
  await pending;
  assert.equal($('#tile-edit-icon').value,'https://my.test/icon.svg');
})();
""")


def test_save_rejects_blank_name_and_prevents_duplicate_requests():
    run_js(r"""
(async()=>{
  dashEditTile('custom');
  $('#tile-edit-name').value='  ';
  $('#tile-edit-url').value='https://new.test';
  await $('#tile-edit-save').onclick();
  assert.equal(posts.length,0);
  assert.notEqual($('#tile-edit-error').textContent,'');
  $('#tile-edit-name').value='Renamed';
  const pending=$('#tile-edit-save').onclick();
  assert.equal($('#tile-edit-save').disabled,true);
  await $('#tile-edit-save').onclick();
  assert.equal(posts.length,1);
  resolvePost({ok:true});
  await pending;
  assert.equal(closed,1);
})();
""")


def test_update_api_accepts_explicitly_cleared_icon(monkeypatch):
    import server
    received = []
    monkeypatch.setattr(server.dashboard, 'update_tile', lambda *args: received.append(args))
    server.dashboard_update('custom', name='Example', url='', icon='', host=None,
                            show_url=False, accent=None, note='', user='test')
    assert received[0][3] == ''


def test_http_update_can_clear_icon_without_clearing_omitted_icon(monkeypatch):
    import server
    from fastapi.testclient import TestClient

    received = []
    def update(*args):
        received.append(args)
        return {'ok': True}
    monkeypatch.setattr(server.dashboard, 'update_tile', update)
    client = TestClient(server.app)
    client.cookies.set(server.COOKIE_NAME, server.make_token('tester', 3600, 'admin'))
    response = client.post('/api/dashboard/update', data={
        'tile_id': 'custom', 'name': 'Example', 'icon': '',
    })
    assert response.status_code == 200
    assert received[-1][3] == ''
    response = client.post('/api/dashboard/update', data={'tile_id': 'custom', 'name': 'Example'})
    assert response.status_code == 200
    assert received[-1][3] is None


def test_save_error_preserves_form_and_allows_retry():
    run_js(r"""
  dashEditTile('custom');
  $('#tile-edit-name').value='Renamed';
  $('#tile-edit-url').value='https://new.test';
  post=async()=>{throw new Error('offline');};
  await $('#tile-edit-save').onclick();
  assert.equal(closed,0);
  assert.equal($('#tile-edit-name').value,'Renamed');
  assert.notEqual($('#tile-edit-error').textContent,'');
  assert.equal($('#tile-edit-save').disabled,false);
""")


def test_save_blocks_non_http_links():
    run_js(r"""
  dashEditTile('custom');
  $('#tile-edit-name').value='Renamed';
  $('#tile-edit-url').value='javascript:alert(1)';
  await $('#tile-edit-save').onclick();
  assert.equal(posts.length,0);
  assert.notEqual($('#tile-edit-error').textContent,'');
""")


def test_save_waits_for_pending_icon_selection():
    run_js(r"""
  dashEditTile('custom');
  $('#tile-edit-name').value='Example';
  $('#tile-edit-url').value='https://new.test';
  let finishSearch;
  dashFindSelfhstIcon=()=>new Promise(resolve=>finishSearch=resolve);
  $('#tile-edit-icon-search').value='plex';
  const search=dashSearchTileIcon();
  assert.equal($('#tile-edit-save').disabled,true);
  await $('#tile-edit-save').onclick();
  assert.equal(posts.length,0);
  finishSearch('https://icons.test/plex.svg');
  await search;
  assert.equal($('#tile-edit-save').disabled,false);
  const saving=$('#tile-edit-save').onclick();
  assert.equal(posts[0].icon,'https://icons.test/plex.svg');
  resolvePost({ok:true});
  await saving;
""")


def test_editor_renders_localized_sections_and_connected_labels_in_all_languages():
    run_js(r"""
  for(language of ['de','en','fr','it','es','pt']){
    dashEditTile('custom');
    const html=$('#form-body').innerHTML;
    assert.ok(html.includes(TILE_EDIT_I18N.appearance[language]));
    assert.ok(html.includes(TILE_EDIT_I18N.showAddress[language]));
    assert.ok(html.includes('maxlength="80"'));
    const labels=[...html.matchAll(/<label for="([^"]+)"/g)].map(match=>match[1]);
    assert.ok(labels.length>=7);
    for(const id of labels)assert.ok(html.includes(`id="${id}"`));
  }
""")


def test_upload_preserves_image_without_putting_base64_in_visible_input():
    run_js(r"""
  let reader;
  globalThis.FileReader=class {constructor(){reader=this;}readAsDataURL(){}};
  dashEditTile('custom');
  $('#tile-edit-name').value='Example';
  $('#tile-edit-url').value='https://new.test';
  dashReadTileIconFile({type:'image/png',size:100});
  assert.equal($('#tile-edit-save').disabled,true);
  reader.result='data:image/png;base64,aW1hZ2U=';
  reader.onload();
  assert.equal($('#tile-edit-save').disabled,false);
  assert.equal($('#tile-edit-icon').value,'');
  const saving=$('#tile-edit-save').onclick();
  assert.equal(posts[0].icon,'data:image/png;base64,aW1hZ2U=');
  resolvePost({ok:true});
  await saving;
""")


def test_delayed_upload_cannot_overwrite_a_manually_entered_icon():
    run_js(r"""
  let reader;
  globalThis.FileReader=class {constructor(){reader=this;}readAsDataURL(){}};
  dashEditTile('custom');
  dashReadTileIconFile({type:'image/png',size:100});
  $('#tile-edit-icon').value='https://my.test/icon.svg';
  dashHandleIconInput();
  reader.result='data:image/png;base64,aW1hZ2U=';
  reader.onload();
  assert.equal(dashEditedIcon(),'https://my.test/icon.svg');
  assert.equal($('#tile-edit-save').disabled,false);
""")
