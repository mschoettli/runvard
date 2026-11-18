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
 function layoutGroups(devices,edges,width,expanded=new Set()){
  const cardWidth=210,cardHeight=96,gap=20,pad=20;
  width=Math.max(270,Math.floor(width));
  const gateway=devices.find(d=>d.gateway)||devices.find(d=>/FRITZ!Box/i.test(d.model||d.name||''));
  const groups=[],positions=Object.create(null),deviceGroups=Object.create(null);
  const types=new Map(edges.filter(e=>e.type==='lan'||e.type==='wlan').map(e=>[e.target,e.type]));
  for(const d of devices)deviceGroups[d.id]=d.id===gateway?.id?'gateway':d.network==='zigbee'?'zigbee':d.connection_type||types.get(d.id)||'unknown';
  const keys=['lan','wlan','zigbee'];const horizontal=width>=860;
  const groupWidth=horizontal?(width-40-32)/3:width-32;
  const top=gateway?168:20;let bottom=top,stackY=top;
  if(gateway)positions[gateway.id]={x:(width-cardWidth)/2,y:24};
  for(const [index,key] of keys.entries()){
   const members=devices.filter(d=>deviceGroups[d.id]===key);
   const ids=new Set(members.map(d=>d.id)),adj=new Map(members.map(d=>[d.id,[]]));
   for(const edge of edges){if(ids.has(edge.source)&&ids.has(edge.target)){adj.get(edge.source).push(edge.target);adj.get(edge.target).push(edge.source);}}
   const memberOrder=new Map(members.map((d,i)=>[d.id,i]));for(const neighbors of adj.values())neighbors.sort((a,b)=>memberOrder.get(a)-memberOrder.get(b));
   const roots=members.filter(d=>d.kind==='Coordinator'||edges.some(e=>e.target===d.id&&!ids.has(e.source)));
   const ordered=[],seen=new Set(),queue=roots.map(d=>d.id);
   while(ordered.length<members.length){if(!queue.length)queue.push(members.find(d=>!seen.has(d.id)).id);const id=queue.shift();if(seen.has(id))continue;seen.add(id);ordered.push(members.find(d=>d.id===id));queue.push(...adj.get(id).filter(x=>!seen.has(x)));}
   if(key==='zigbee'){const routers=ordered.filter(d=>d.kind==='Router'),coordinators=ordered.filter(d=>d.kind==='Coordinator'),samples=routers.map(d=>ordered.find(x=>x.kind!=='Coordinator'&&x.kind!=='Router'&&adj.get(d.id).includes(x.id))).filter(Boolean);const priority=[...coordinators,...routers,...samples,...ordered];ordered.splice(0,ordered.length,...priority.filter((d,i)=>priority.findIndex(x=>x.id===d.id)===i));}
   const cap=key==='zigbee'?5:3;const limit=expanded.has(key)?ordered.length:cap,shown=ordered.slice(0,limit),hidden=ordered.length-shown.length;
   const x=horizontal?16+index*(groupWidth+20):16,y=horizontal?top:stackY;
   const height=Math.max(190,80+shown.length*(cardHeight+gap)+(hidden||expanded.has(key)&&members.length>cap?50:0));
   const g={network:key,x,y,width:groupWidth,height,count:members.length,hidden,ids:shown.map(d=>d.id)};
   groups.push(g);
   const reserved=shown.filter(d=>d.layout_group===key&&Number.isFinite(d.x)&&Number.isFinite(d.y)&&d.x>=x+12&&d.x+cardWidth<=x+groupWidth-12&&d.y>=y+68).map(d=>({id:d.id,x:d.x,y:d.y}));
   const used=[...reserved];let slot=0;
   shown.forEach(d=>{
    const saved=reserved.find(p=>p.id===d.id);let p;
    if(saved)p={x:saved.x,y:saved.y};else{
     do{p={x:x+(groupWidth-cardWidth)/2,y:y+72+slot*(cardHeight+gap)};slot++;}while(used.some(o=>Math.abs(o.x-p.x)<cardWidth+8&&Math.abs(o.y-p.y)<cardHeight+8));used.push(p);
    }
    positions[d.id]=p;g.height=Math.max(g.height,p.y-y+cardHeight+24+(hidden||expanded.has(key)&&members.length>cap?50:0));
   });
   if(hidden)g.control={x:x+20,y:y+g.height-56,width:groupWidth-40,height:36};
   else if(expanded.has(key)&&members.length>cap)g.control={x:x+20,y:y+g.height-42,width:groupWidth-40,height:32};
   bottom=Math.max(bottom,y+g.height);stackY=y+g.height+20;
  }
  const unknown=devices.filter(d=>deviceGroups[d.id]==='unknown');
  if(unknown.length){
   const x=16,y=bottom+24,w=width-32,columns=Math.max(1,Math.floor((w-40+20)/(cardWidth+20))),shown=expanded.has('unknown')?unknown:unknown.slice(0,Math.max(columns,5)),hidden=unknown.length-shown.length;
   const unknownHeader=width<600?148:100;const rows=Math.ceil(shown.length/columns),height=unknownHeader+8+rows*(cardHeight+20)+(hidden||expanded.has('unknown')&&unknown.length>5?50:0);
   const group={network:'unknown',x,y,width:w,height,count:unknown.length,hidden,ids:shown.map(d=>d.id)};
   groups.push(group);
   const reserved=shown.filter(d=>d.layout_group==='unknown'&&Number.isFinite(d.x)&&Number.isFinite(d.y)&&d.x>=x+12&&d.x+cardWidth<=x+w-12&&d.y>=y+unknownHeader).map(d=>({id:d.id,x:d.x,y:d.y}));
   const occupied=[...reserved];let slot=0;
   shown.forEach(d=>{let p=reserved.find(p=>p.id===d.id);
    if(!p){do{p={x:x+(w-columns*cardWidth-(columns-1)*20)/2+(slot%columns)*(cardWidth+20),y:y+unknownHeader+Math.floor(slot/columns)*(cardHeight+20)};slot++;}while(occupied.some(o=>Math.abs(o.x-p.x)<cardWidth+8&&Math.abs(o.y-p.y)<cardHeight+8));occupied.push(p);}
    positions[d.id]={x:p.x,y:p.y};group.height=Math.max(group.height,p.y-y+cardHeight+24+(hidden||expanded.has('unknown')&&unknown.length>5?50:0));
   });
   if(hidden||unknown.length>shown.length||expanded.has('unknown')&&unknown.length>5)group.control={x:x+20,y:y+group.height-48,width:w-40,height:32};
   bottom=y+group.height;
  }
  return {positions,groups,deviceGroups,gateway:gateway?.id,width,height:Math.max(bottom+20,200),cardWidth,cardHeight,columns:horizontal?3:1};
 }
 if(typeof module!=='undefined'&&module.exports)module.exports={layoutNetwork,layoutGroups};
 else root.RunvardMapLayout={layoutNetwork,layoutGroups};
})(globalThis);
