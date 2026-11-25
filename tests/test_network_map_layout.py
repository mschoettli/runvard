from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_large_network_layout_uses_width_and_preserves_manual_positions():
    result = subprocess.run(['node', '-e', r'''
const assert=require('assert');
const {layoutNetwork}=require('./static/network-map-layout.js');
const devices=Array.from({length:49},(_,i)=>({id:'lan:'+i,network:'lan',name:'192.168.1.'+(i+1),ip:'192.168.1.'+(i+1)}));
for(const width of [340,800,1240,1600]){
 const layout=layoutNetwork(devices,[],width);
 assert.equal(Object.keys(layout.positions).length,49);
 assert(layout.width<=Math.max(width,270));
 assert.equal(layout.groups[0].count,49);
 for(const [id,p] of Object.entries(layout.positions)){
  assert(p.x>=0&&p.x+layout.cardWidth<=layout.width,id+' outside stage');
  for(const [other,q] of Object.entries(layout.positions))if(id!==other)
   assert(Math.abs(p.x-q.x)>=layout.cardWidth||Math.abs(p.y-q.y)>=layout.cardHeight,id+' overlaps '+other);
 }
}
const narrow=layoutNetwork(devices,[],800),wide=layoutNetwork(devices,[],1600);
assert(wide.columns>narrow.columns);assert(wide.height<narrow.height);
const moved=devices.map(d=>({...d}));moved[0].x=300;moved[0].y=150;
const layout=layoutNetwork(moved,[],1240);assert.deepEqual(layout.positions['lan:0'],{x:300,y:150});
for(const [id,q] of Object.entries(layout.positions))if(id!=='lan:0')assert(Math.abs(q.x-300)>=layout.cardWidth||Math.abs(q.y-150)>=layout.cardHeight);
assert(!devices[0].x,'input mutated');
const zigbee=[{id:'z1',network:'zigbee',name:'Sensor',kind:'EndDevice'}];
const mixed=layoutNetwork([...devices,...zigbee],[],1240);assert.equal(mixed.groups.length,2);assert(mixed.positions.z1.y>Math.max(...Object.values(layoutNetwork(devices,[],1240).positions).map(p=>p.y)));
assert.equal(layoutNetwork([],[],340).height>=200,true);
'''],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_group_topology_respects_actual_links_filters_and_folding():
    result = subprocess.run(['node', '-e', r'''
const assert = require('node:assert/strict');
const {classifyTopology, layoutGroups} = require('./static/network-map-layout.js');
const devices = [
 {id:'g', name:'Gateway', gateway:true, network:'lan'},
 {id:'s', model:'TL-SG108', network:'lan', connection_type:'lan'},
 {id:'ap', model:'FRITZ!Box 7530', network:'lan', connection_type:'wlan'},
 {id:'r', model:'FRITZ!Repeater 1200', network:'lan', connection_type:'wlan'},
 {id:'n', network:'lan'},
 ...Array.from({length:15}, (_,i) => ({id:'z'+i, network:'zigbee', kind:i===0?'Coordinator':i===1?'Router':'EndDevice'}))
];
const edges = [
 {source:'g',target:'s',origin:'fritz',type:'lan'},
 {source:'g',target:'ap',origin:'fritz',type:'wlan'},
 {source:'ap',target:'r',origin:'fritz',type:'wlan'},
 {source:'z0',target:'z1',origin:'zigbee'},
 {source:'z1',target:'z2',origin:'zigbee'},
 {source:'z2',target:'z3',origin:'zigbee'},
 {source:'z3',target:'z1',origin:'zigbee'}
];
const topology = classifyTopology(devices, edges);
assert.equal(topology.deviceKinds.s, 'switch');
assert.equal(topology.deviceKinds.r, 'repeater');
assert.equal(topology.deviceKinds.z0, 'coordinator');
assert.equal(topology.depth.get('z2'), 2);
assert.equal(topology.neighbors.get('n').size, 0);
function assertBounds(layout) {
 const entries = Object.entries(layout.positions);
 for (const [id, p] of entries) {
  const size = layout.sizes[id];
  assert(p.x>=0 && p.x+size.width<=layout.width, 'x '+id);
  assert(p.y>=0 && p.y+size.height<=layout.height, 'y '+id);
  for (const [other, q] of entries) if(id!==other) {
   const theirs = layout.sizes[other];
   assert(p.x+size.width<=q.x || q.x+theirs.width<=p.x || p.y+size.height<=q.y || q.y+theirs.height<=p.y,
    'overlap '+id+' '+other);
  }
 }
}
const allOpen = new Set(['lan','wlan','zigbee','unknown']);
for (const width of [320, 340, 600, 800, 860, 1400]) {
 const layout = layoutGroups(devices, edges, width, allOpen, {collapsed:new Set(),topology});
 assert.equal(layout.gateway, 'g');
 assert.equal(Object.keys(layout.positions).length, devices.length);
 assert(!layout.edges, 'layout must not invent evidence');
 assert.deepEqual([...topology.neighbors.get('r')], ['ap']);
 assert(layout.positions.z1.y > layout.positions.z0.y && layout.positions.z2.y > layout.positions.z1.y);
 assert(layout.sizes.z2.height < layout.sizes.z1.height);
 assertBounds(layout);
 const preview = layoutGroups(devices, edges, width, new Set(), {collapsed:new Set(),topology});
 const zigbee = preview.groups.find(g=>g.network==='zigbee');
 assert(zigbee.hidden>0 && zigbee.control && zigbee.ids.includes('z0') && zigbee.ids.includes('z1'));
}
const desktop = layoutGroups(devices, edges, 1200);
assert(desktop.groups.find(g=>g.network==='unknown').collapsed);
assert(!desktop.positions.n);
const mobile = layoutGroups(devices, edges, 350);
assert.deepEqual(mobile.groups.filter(g=>!g.collapsed).map(g=>g.network), ['lan']);
const zigbeeOnly = layoutGroups(devices.filter(d=>d.network==='zigbee'), edges, 350, new Set(), {filtering:true,topology});
assert.deepEqual(zigbeeOnly.groups.map(g=>g.network), ['zigbee']);
assert.equal(zigbeeOnly.groups[0].hidden, 0);
assert(zigbeeOnly.positions.z0.y<100);
const wlanOnly = layoutGroups(devices.filter(d=>d.id==='ap'), edges, 850, new Set(), {filtering:true,allDevices:devices});
assert.equal(wlanOnly.deviceGroups.ap, 'wlan');
assert(!wlanOnly.positions.g);
assert.equal(wlanOnly.groups[0].network,'wlan');
const none = layoutGroups([], edges, 850, new Set(), {filtering:true,topology});
assert.deepEqual(none.groups, []);
'''], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_focused_groups_use_width_and_validate_saved_positions():
    result = subprocess.run(['node', '-e', r'''
const assert = require('node:assert/strict');
const {layoutGroups} = require('./static/network-map-layout.js');
const devices = Array.from({length:30}, (_,i) => ({id:'w'+i,network:'lan',connection_type:'wlan'}));
const filter = {filtering:true};
const wide = layoutGroups(devices, [], 1280, new Set(), filter);
const narrow = layoutGroups(devices, [], 350, new Set(), filter);
assert(new Set(Object.values(wide.positions).map(p=>p.x)).size > 1);
assert(wide.height < narrow.height/2);
const options = {collapsed:new Set()}, expanded = new Set(['wlan']);
const before = layoutGroups(devices, [], 800, expanded, options);
const at = before.positions.w6;
const moved = devices.map(d=>d.id==='w0' || d.id==='w1' ? {...d,x:at.x,y:at.y,layout_group:'wlan'} : d);
const after = layoutGroups(moved, [], 800, expanded, options);
assert.deepEqual(after.positions.w0, at);
assert.notDeepEqual(after.positions.w1, at);
for(const [id,p] of Object.entries(after.positions)) {
 for(const [other,q] of Object.entries(after.positions)) if(id!==other) {
  const a=after.sizes[id], b=after.sizes[other];
  assert(p.x+a.width<=q.x || q.x+b.width<=p.x || p.y+a.height<=q.y || q.y+b.height<=p.y, id+' overlaps '+other);
 }
}
'''], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_edge_routes_avoid_visible_cards_and_preserve_reported_relationships():
    result = subprocess.run(['node', '-e', r'''
const assert = require('node:assert/strict');
const {layoutGroups} = require('./static/network-map-layout.js');
const {routes} = require('./static/network-map-edges.js');
function points(path) {
 const parts=path.split(' ');
 assert.equal(parts[0], 'M');
 let x=Number(parts[1]), y=Number(parts[2]);
 const list=[{x,y}];
 for (let i=3;i<parts.length;i+=2) {
  if (parts[i]==='H') x=Number(parts[i+1]);
  else { assert.equal(parts[i], 'V'); y=Number(parts[i+1]); }
  assert(Number.isFinite(x) && Number.isFinite(y));
  list.push({x,y});
 }
 return list;
}
function onCardBorder(point,p,s) {
 return ((point.x===p.x || point.x===p.x+s.width) && point.y>=p.y && point.y<=p.y+s.height) ||
  ((point.y===p.y || point.y===p.y+s.height) && point.x>=p.x && point.x<=p.x+s.width);
}
const devices=Array.from({length:40},(_,i)=>({
 id:String(i),gateway:i===0,network:i>25?'zigbee':'lan',connection_type:i<15?'wlan':'lan',
 kind:i===26?'Coordinator':i%7===1?'Router':'device'
}));
const links=devices.slice(1).flatMap((d,i)=>[1,4].map((skip,j)=>({
 source:String(Math.max(0,i-skip)),target:d.id,origin:j?'manual':'fritz'
})));
for(const width of [325,850,1290]) {
 const stage=layoutGroups(devices,links,width,new Set(['lan','wlan','zigbee']),{collapsed:new Set()});
 const edges=[...links,{source:'1',target:'0',origin:'fritz'}, {source:'0',target:'missing',origin:'manual'}];
 const output=routes(stage,edges);
 assert.equal(output.length,links.length);
 assert.deepEqual(output,routes(stage,edges));
 for(const {edge,path} of output) {
  const list=points(path);
  assert(onCardBorder(list[0],stage.positions[edge.source],stage.sizes[edge.source]));
  assert(onCardBorder(list.at(-1),stage.positions[edge.target],stage.sizes[edge.target]));
  for(let i=1;i<list.length;i++) {
   const a=list[i-1],b=list[i];
   for(const [id,p] of Object.entries(stage.positions)) {
    if(id===edge.source || id===edge.target) continue;
    const s=stage.sizes[id];
    const crosses=a.x===b.x
     ? a.x>p.x && a.x<p.x+s.width && Math.min(a.y,b.y)<p.y+s.height && Math.max(a.y,b.y)>p.y
     : a.y>p.y && a.y<p.y+s.height && Math.min(a.x,b.x)<p.x+s.width && Math.max(a.x,b.x)>p.x;
    assert(!crosses,'route '+JSON.stringify(edge)+' crosses '+id);
   }
  }
 }
}
'''], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
