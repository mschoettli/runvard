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
