/* Geometry shared by the map and its regression checks. No inferred connections. */
(function(root){
 'use strict';
 function layoutNetwork(devices,edges,width){
  const cardWidth=210,cardHeight=96,gap=20,padding=24;
  width=Math.max(270,Math.floor(width));
  const columns=Math.max(1,Math.min(8,Math.floor((width-padding*2+gap)/(cardWidth+gap))));
  const spacing=columns>1?Math.max(gap,(width-padding*2-columns*cardWidth)/(columns-1)):0;
  const positions=Object.create(null),groups=[],occupied=devices.filter(d=>Number.isFinite(d.x)&&Number.isFinite(d.y)).map(d=>({x:d.x,y:d.y}));
  const linked=new Set(edges.flatMap(e=>[e.source,e.target]));
  const rank=d=>d.kind==='Coordinator'?0:d.kind==='router'||d.kind==='bridge'||d.kind==='Router'?1:linked.has(d.id)?2:3;
  let y=24,maxX=width,maxY=200;
  for(const network of ['lan','zigbee']){
   const members=devices.filter(d=>d.network===network).sort((a,b)=>rank(a)-rank(b)||String(a.ip||a.ieee||a.id).localeCompare(String(b.ip||b.ieee||b.id),'en',{numeric:true}));
   if(!members.length)continue;
   groups.push({network,y,count:members.length});let slot=0,bottom=y+50;
   for(const d of members){
    let p;
    if(Number.isFinite(d.x)&&Number.isFinite(d.y))p={x:d.x,y:d.y};
    else{
     do{p={x:columns===1?Math.max(padding,(width-cardWidth)/2):padding+(slot%columns)*(cardWidth+spacing),y:y+58+Math.floor(slot/columns)*(cardHeight+gap)};slot++;}
     while(occupied.some(o=>Math.abs(o.x-p.x)<cardWidth+12&&Math.abs(o.y-p.y)<cardHeight+12));
     occupied.push(p);
    }
    positions[d.id]=p;bottom=Math.max(bottom,p.y+cardHeight);maxX=Math.max(maxX,p.x+cardWidth+padding);
   }
   y=bottom+48;maxY=y;
  }
  return {positions,groups,width:maxX,height:Math.max(200,maxY),columns,cardWidth,cardHeight};
 }
 if(typeof module!=='undefined'&&module.exports)module.exports={layoutNetwork};
 else root.RunvardMapLayout={layoutNetwork};
})(globalThis);
