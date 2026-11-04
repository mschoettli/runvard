"""Theme selection must match the current dashboard, including legacy values."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_network_map_theme_defaults_legacy_values_and_cross_tab_sync():
    result = subprocess.run(['node', '-e', r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('static/network-map-theme.js','utf8');
function setup(value,denied=false){
 const events={},docEvents={},root={dataset:{}};
 const context={document:{documentElement:root,hidden:false,addEventListener:(k,f)=>docEvents[k]=f},localStorage:{getItem:()=>{if(denied)throw Error('blocked');return value;}},window:{addEventListener:(k,f)=>events[k]=f}};
 vm.runInNewContext(source,context);
 return {root,events,docEvents,set:v=>value=v};
}
for(const [stored,expected] of [[null,'modern'],['modern','modern'],['tech','tech'],['original','tech'],['bad','modern']]){
 const s=setup(stored);assert.equal(s.root.dataset.uiTheme,expected,`stored=${stored}`);
}
assert.equal(setup(null,true).root.dataset.uiTheme,'modern');
const s=setup('modern');s.set('tech');s.events.storage({key:'runvard_ui_theme'});assert.equal(s.root.dataset.uiTheme,'tech');
s.set('modern');s.events.focus();assert.equal(s.root.dataset.uiTheme,'modern');
s.set('original');s.events.pageshow();assert.equal(s.root.dataset.uiTheme,'tech');
s.set(null);s.events.storage({key:null});assert.equal(s.root.dataset.uiTheme,'modern');
s.set('tech');s.docEvents.visibilitychange();assert.equal(s.root.dataset.uiTheme,'tech');
assert(fs.readFileSync('static/network-map.html','utf8').includes('/static/network-map-theme.js'));
'''], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
