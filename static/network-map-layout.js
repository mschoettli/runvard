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
 const dimensions = {
  gateway: {width: 200, height: 80},
  infrastructure: {width: 184, height: 76},
  endpoint: {width: 152, height: 54}
 };
 const domains = ['lan', 'wlan', 'zigbee', 'unknown'];

 function infrastructureKind(device) {
  const kind = String(device.kind || '').toLowerCase();
  if (device.network === 'zigbee') {
   return ['coordinator', 'router'].includes(kind) ? kind : 'endpoint';
  }
  if (['router', 'repeater', 'switch', 'bridge'].includes(kind)) return kind;
  if (['ap', 'accesspoint', 'access_point'].includes(kind)) return 'repeater';
  const model = String(device.model || '');
  const identity = model + ' ' + (device.name || '');
  if (/FRITZ!\s*Repeater|\bmesh repeater\b|\baccess point\b/i.test(identity)) return 'repeater';
  if (/\b(ethernet|network|managed|unmanaged|gigabit|poe)[ -]+switch\b|\bTL-SG\d/i.test(identity)) return 'switch';
  if (/^switch$/i.test(model)) return 'switch';
  if (/FRITZ!Box/i.test(identity)) return 'router';
  return 'endpoint';
 }

 function classifyTopology(devices, edges) {
  const byId = new Map(devices.map(device => [device.id, device]));
  const gateway = devices.find(device => device.gateway) ||
   devices.find(device => device.network !== 'zigbee' && /FRITZ!Box/i.test(device.model || device.name || ''));
  const deviceGroups = Object.create(null), deviceRoles = Object.create(null), deviceKinds = Object.create(null);
  const neighbors = new Map(devices.map(device => [device.id, new Set()]));
  const connectionTypes = new Map();
  for (const edge of edges) {
   if (edge.source === edge.target || !byId.has(edge.source) || !byId.has(edge.target)) continue;
   neighbors.get(edge.source).add(edge.target);
   neighbors.get(edge.target).add(edge.source);
   if (edge.type === 'lan' || edge.type === 'wlan') connectionTypes.set(edge.target, edge.type);
  }
  for (const device of devices) {
   const kind = device.id === gateway?.id ? 'gateway' : infrastructureKind(device);
   deviceKinds[device.id] = kind;
   deviceRoles[device.id] = kind === 'gateway' || kind === 'endpoint' ? kind : 'infrastructure';
   let group = device.connection_type || connectionTypes.get(device.id);
   if (kind === 'gateway') group = 'gateway';
   else if (device.network === 'zigbee') group = 'zigbee';
   else if (group !== 'lan' && group !== 'wlan') group = 'unknown';
   deviceGroups[device.id] = group;
  }

  // Give genuine neighbors a common branch order, even when a later filter hides their parent.
  const depth = new Map(), order = new Map();
  const visit = roots => {
   const queue = [];
   for (const id of roots) {
    if (!depth.has(id)) { depth.set(id, 0); queue.push(id); }
   }
   for (let i = 0; i < queue.length; i++) {
    const id = queue[i];
    order.set(id, order.size);
    for (const other of neighbors.get(id)) {
     if (depth.has(other)) continue;
     depth.set(other, depth.get(id) + 1);
     queue.push(other);
    }
   }
  };
  visit(devices.filter(d => ['gateway', 'coordinator'].includes(deviceKinds[d.id])).map(d => d.id));
  for (const device of devices) {
   if (deviceRoles[device.id] === 'infrastructure' && !depth.has(device.id)) visit([device.id]);
  }
  // Isolated devices have no topology depth. Keep them together after traced branches.
  for (const device of devices) {
   if (!depth.has(device.id) && neighbors.get(device.id).size) visit([device.id]);
  }
  return {gateway: gateway?.id, deviceGroups, deviceRoles, deviceKinds, neighbors, depth, order};
 }

 function withinLane(a, b) {
  return a.x < b.x + b.width + 12 && b.x < a.x + a.width + 12 &&
   a.y < b.y + b.height + 12 && b.y < a.y + a.height + 12;
 }

 function placeMembers(group, members, stage, topology, filtering) {
  const gutter = 16, gap = 16;
  const cellWidth = Math.max(...members.map(d => stage.sizes[d.id].width));
  const columns = Math.max(1, Math.floor((group.width - gutter * 2 + gap) / (cellWidth + gap)));
  const cellX = column => group.x + (group.width - columns * cellWidth - (columns - 1) * gap) / 2 + column * (cellWidth + gap);
  let y = group.contentTop, rowHeight = 0, column = 0, lastDepth;
  const slots = new Map();
  for (const device of members) {
   const depth = topology.depth.get(device.id), size = stage.sizes[device.id];
   if (rowHeight && (column === columns || lastDepth !== depth)) {
    y += rowHeight + gap;
    rowHeight = 0;
    column = 0;
   }
   slots.set(device.id, {x: cellX(column++) + (cellWidth - size.width) / 2, y, ...size});
   rowHeight = Math.max(rowHeight, size.height);
   lastDepth = depth;
  }
  let bottom = y + rowHeight;
  const pinned = new Map();
  if (!filtering) {
   for (const d of members) {
    const rect = {x: d.x, y: d.y, ...stage.sizes[d.id]};
    if (d.layout_group !== group.network || !Number.isFinite(d.x) || !Number.isFinite(d.y)) continue;
    // Positions from a wider or previously expanded layout may no longer fit.
    if (d.x < group.x + 12 || d.x + rect.width > group.x + group.width - 12 ||
        d.y < group.contentTop || d.y + rect.height > bottom + gap) continue;
    if (![...pinned.values()].some(other => withinLane(rect, other))) pinned.set(d.id, rect);
   }
  }
  const reservations = [...pinned.values()];
  for (const rect of reservations) bottom = Math.max(bottom, rect.y + rect.height);
  let overflowY = bottom + gap, overflowColumn = 0, overflowHeight = 0;
  for (const d of members) {
   let rect = pinned.get(d.id);
   if (!rect) {
    rect = slots.get(d.id);
    if (reservations.some(other => withinLane(rect, other))) {
     if (overflowColumn === columns) {
      overflowY += overflowHeight + gap;
      overflowColumn = 0;
      overflowHeight = 0;
     }
     rect = {...rect, x: cellX(overflowColumn++) + (cellWidth - rect.width) / 2, y: overflowY};
     overflowHeight = Math.max(overflowHeight, rect.height);
    }
   }
   stage.positions[d.id] = {x: rect.x, y: rect.y};
   bottom = Math.max(bottom, rect.y + rect.height);
  }
  return bottom;
 }

 function layoutGroups(devices, edges, width, expanded = new Set(), options = {}) {
  width = Math.max(270, Math.floor(width));
  const topology = options.topology || classifyTopology(options.allDevices || devices, edges);
  const {gateway, deviceGroups, deviceRoles, deviceKinds} = topology;
  const filtering = Boolean(options.filtering), mobile = width < 600;
  const stage = {width, height: 200, positions: Object.create(null), sizes: Object.create(null),
   groups: [], gateway, deviceGroups, deviceRoles, deviceKinds, columns: 1,
   cardWidth: dimensions.endpoint.width, cardHeight: dimensions.endpoint.height};
  const membersByGroup = new Map(domains.map(key => [key, []]));
  let top = 16;
  for (const d of devices) {
   stage.sizes[d.id] = dimensions[deviceRoles[d.id]];
   if (d.id === gateway) {
    const size = stage.sizes[d.id];
    stage.positions[d.id] = {x: (width - size.width) / 2, y: 16};
    top = 16 + size.height + 36;
   } else {
    membersByGroup.get(deviceGroups[d.id]).push(d);
   }
  }
  const keys = domains.filter(key => membersByGroup.get(key).length || (!filtering && key !== 'unknown'));
  const primaryKeys = keys.filter(key => key !== 'unknown');
  const firstPopulated = keys.find(key => membersByGroup.get(key).length);
  const columns = width >= 860 ? Math.max(1, primaryKeys.length) : 1;
  stage.columns = columns;
  let bottom = top, nextY = top;
  for (const key of keys) {
   const members = membersByGroup.get(key);
   const inPrimaryRow = columns > 1 && key !== 'unknown';
   const groupWidth = inPrimaryRow ? (width - 32 - (columns - 1) * 16) / columns : width - 32;
   const x = inPrimaryRow ? 16 + primaryKeys.indexOf(key) * (groupWidth + 16) : 16;
   const y = inPrimaryRow ? top : nextY;
   const defaultFold = !filtering && (mobile ? key !== firstPopulated : key === 'unknown');
   const collapsed = options.collapsed ? options.collapsed.has(key) : defaultFold;
   // Empty mobile sections are kept to a header, with setup available in Sources.
   const group = {network: key, x, y, width: groupWidth, contentTop: y + 62, height: 52,
    collapsed: collapsed || (!members.length && mobile), canCollapse: Boolean(members.length),
    count: members.length, ids: [], hidden: members.length};
   stage.groups.push(group);
   if (!group.collapsed && members.length) {
    const rank = d => topology.depth.get(d.id) ?? Infinity;
    const sorted = [...members].sort((a, b) =>
     rank(a) - rank(b) || (topology.order.get(a.id) ?? Infinity) - (topology.order.get(b.id) ?? Infinity));
    const cap = key === 'zigbee' || key === 'unknown' ? 5 : 4;
    let shown = sorted;
    if (!filtering && !expanded.has(key) && sorted.length > cap) {
     const preferred = sorted.filter(d => deviceRoles[d.id] === 'infrastructure');
     const preview = new Set([...preferred, ...sorted].slice(0,cap).map(d => d.id));
     shown = sorted.filter(d => preview.has(d.id));
    }
    group.ids = shown.map(d => d.id);
    group.hidden = members.length - shown.length;
    const end = placeMembers(group, shown, stage, topology, filtering);
    group.height = end - group.y + 16;
    if (!filtering && sorted.length > cap) {
     group.control = {x: x + 16, y: end + 12, width: groupWidth - 32, height: 36};
     group.height += 48;
    }
   } else if (!group.collapsed) {
    group.height = 118;
   }
   bottom = Math.max(bottom, y + group.height);
   nextY = bottom + 16;
  }
  stage.height = Math.max(200, bottom + 16);
  return stage;
 }

 const api = {layoutNetwork, classifyTopology, layoutGroups};
 if (typeof module !== 'undefined' && module.exports) module.exports = api;
 else root.RunvardMapLayout = api;
})(globalThis);
