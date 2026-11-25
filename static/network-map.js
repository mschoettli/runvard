'use strict';
(async()=>{
 const $=id=>document.getElementById(id);
 const catalogs=await (await fetch('/static/network-map-i18n.json?v=5')).json();
 let lang;try{lang=localStorage.getItem('runvard_language');}catch(_){}
 lang=lang||document.body.dataset.defaultLang||'en';if(!catalogs[lang])lang='en';
 const t=key=>catalogs[lang][key]||catalogs[lang].error;
 const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
 const iconPaths={
  network:['M12 4v5M5 15v5M19 15v5M5 15h14M12 9v6','M8 2h8v5H8zM2 19h6v3H2zM16 19h6v3h-6z'],
  wifi:['M3 9a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0M9 15a5 5 0 0 1 6 0M12 19h.01'],
  device:['M4 4h16v12H4zM8 21h8M12 16v5'],
  server:['M5 3h14v7H5zM5 14h14v7H5zM8 6.5h.01M8 17.5h.01M12 6.5h4M12 17.5h4'],
  zigbee:['M12 2l8.7 5v10L12 22l-8.7-5V7zM12 7v10M7.7 9.5l8.6 5M16.3 9.5l-8.6 5'],
  search:['M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15M16 16l5 5'],
  edit:['M14 5l5 5M4 16l12-12a2.1 2.1 0 0 1 3 3L7 19l-4 1z'],
  link:['M9 15l6-6M8 17l-1 1a4.2 4.2 0 0 1-6-6l5-5a4.2 4.2 0 0 1 6 0M16 7l1-1a4.2 4.2 0 0 1 6 6l-5 5a4.2 4.2 0 0 1-6 0'],
  select:['M5 3l13 8-6 2-3 6zM14 15l4 5'],
  sensor:['M8 3h8v18H8zM11 7h2M11 17h2'],
  chevron:['M7 10l5 5 5-5'],
  back:['M19 12H5m6-6-6 6 6 6'],
  switch:['M2 6h20v12H2zM5 10h2m2 0h2m2 0h2m2 0h2'],
  gateway:['M3 13h18v7H3zM6 13V5m12 8V5M7 17h1m3 0h1'],
  grip:['M9 5h.01M15 5h.01M9 12h.01M15 12h.01M9 19h.01M15 19h.01'],
  unavailable:['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18','M8 12h8'],
  refresh:['M20 7a8 8 0 1 0 1 7M20 3v5h-5']
 };
 function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');for(const d of iconPaths[name]||iconPaths.device){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}return svg;}
 const kindKeys = {gateway:'gatewayRole', coordinator:'coordinator', router:'routerRole', switch:'switchRole', repeater:'repeaterRole', bridge:'bridgeRole', endpoint:'endDevice'};
 const groupKeys = {lan:'groupLan', wlan:'groupWlan', unknown:'groupUnknown'};
 const expandedGroups = new Set();
 // Keep folded groups independently for the phone accordion and the desktop map.
 const folds = new Map();
 const viewport = $('viewport');
 let state = {devices:[], edges:[], settings:{networks:[]}, can_edit:false};
 let topology = RunvardMapLayout.classifyTopology([], []);
 let stage, matches = [], filtering = false, contextId = '', selected = '', scale = 1;
 let signature = null, pendingRefresh = null, dragging = false, messageKey = '';
 const label = d => d.label || d.name || d.ip || d.ieee || d.id;
 const date = value => value ? new Date(value * 1000).toLocaleString(lang) : t('never');
 const groupLabel = key => key === 'zigbee' ? 'Zigbee' : t(groupKeys[key]);
 const kindLabel = d => t(kindKeys[topology.deviceKinds[d.id]] || 'endDevice');
 const statusCode = d => ['online', 'offline', 'out_of_scope'].includes(d.status) ? d.status : 'unknown';
 const status = d => statusCode(d) === 'out_of_scope' ? 'outOfScope' : statusCode(d);
 const originLabel = e => t(e.origin === 'manual' ? 'manual' : e.origin === 'fritz' ? 'fritzOrigin' : 'zigbee');
 const foldMode = () => (viewport.clientWidth < 600 ? 'mobile' : 'desktop') + (filtering ? '-filtered' : '');
 function deviceIcon(d) {
  const kind = topology.deviceKinds[d.id];
  if (kind === 'gateway' || kind === 'switch') return kind;
  if (kind === 'coordinator' || d.network === 'zigbee' && kind === 'router') return 'zigbee';
  if (kind === 'router' || kind === 'repeater') return 'wifi';
  if (kind === 'bridge') return 'network';
  return d.network === 'zigbee' ? 'sensor' : d.ports?.includes(22) ? 'server' : 'device';
 }
 function translate() {
  document.documentElement.lang = lang;
  document.title = t('title') + ' · Runvard';
  $('language').value = lang;
  document.querySelectorAll('[data-t]').forEach(e => e.textContent = t(e.dataset.t));
  document.querySelectorAll('[data-aria]').forEach(e => e.setAttribute('aria-label', t(e.dataset.aria)));
  const search = $('search');
  search.placeholder = t('search');
  search.setAttribute('aria-label', t('search'));
  $('language').setAttribute('aria-label', t('language'));
  viewport.setAttribute('aria-label', t('title'));
  viewport.title = t('mapHelp');
  message(messageKey);
 }
 function message(key) {
  messageKey = key;
  $('message').textContent = key ? t(key) : '';
 }
 async function request(action, data = {}) {
  const r = await fetch('/api/network-map/' + action, {
   method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
  });
  if (r.status === 401) location.href = '/login';
  if (!r.ok) throw Error(r.status === 400 ? 'invalid' : 'error');
  return r.json();
 }
 function layout() {
  const query = $('search').value.trim().toLocaleLowerCase(lang);
  const network = $('network-filter').value, reachability = $('status-filter').value;
  filtering = Boolean(query || network !== 'all' || reachability !== 'all');
  matches = state.devices.filter(d => {
   if (network !== 'all' && network !== topology.deviceGroups[d.id]) return false;
   if (reachability !== 'all' && reachability !== statusCode(d)) return false;
   return !query || [label(d), d.name, d.ip, d.mac, d.ieee, d.room, d.manufacturer, d.model]
    .filter(Boolean).some(value => String(value).toLocaleLowerCase(lang).includes(query));
  });
  contextId = '';
  let shown = matches;
  if (!query && matches.length && ['lan','wlan'].includes(network) && topology.gateway) {
   contextId = topology.gateway;
   shown = [state.devices.find(d => d.id === contextId), ...matches];
  }
  stage = RunvardMapLayout.layoutGroups(shown, state.edges, viewport.clientWidth, expandedGroups, {
   topology, filtering:filtering || !state.devices.length, collapsed:folds.get(foldMode())
  });
 }
 function transform() {
  $('world').style.width = stage.width + 'px';
  $('world').style.height = stage.height + 'px';
  $('world').style.transform = 'scale(' + scale + ')';
  $('canvas').style.width = Math.ceil(stage.width * scale) + 'px';
  $('canvas').style.height = Math.ceil(stage.height * scale) + 'px';
  $('zoom').textContent = Math.round(scale * 100) + '%';
 }
 function drawEdges() {
  const svg = $('edges');
  const paths = document.createDocumentFragment();
  for (const {edge, path} of RunvardMapEdges.routes(stage, state.edges)) {
   const line = document.createElementNS(svg.namespaceURI, 'path');
   line.setAttribute('d', path);
   line.dataset.source = edge.source;
   line.dataset.target = edge.target;
   line.classList.add(edge.origin === 'manual' ? 'manual' : edge.origin === 'fritz' ? 'observed' : 'zigbee');
   const title = document.createElementNS(svg.namespaceURI, 'title');
   title.textContent = originLabel(edge) + (edge.lqi == null ? '' : ' · LQI ' + edge.lqi);
   line.append(title);
   paths.append(line);
  }
  svg.replaceChildren(paths);
 }
 function highlight() {
  const neighbors = topology.neighbors.get(selected);
  for (const node of $('nodes').querySelectorAll('.node')) {
   const id = node.dataset.id, active = id === selected, nearby = Boolean(neighbors?.has(id));
   node.classList.toggle('selected', active);
   node.classList.toggle('neighbor-node', nearby);
   node.classList.toggle('subdued', Boolean(selected) && !nearby && !active || id === contextId && !active && !nearby);
   node.setAttribute('aria-pressed', String(active));
  }
  for (const line of $('edges').children) {
   const active = selected && [line.dataset.source, line.dataset.target].includes(selected);
   line.classList.toggle('selected', Boolean(active));
   line.classList.toggle('dim', Boolean(selected && !active));
  }
  // Selected wires remain visible where several routes share a corridor.
  for (const line of $('edges').querySelectorAll('.selected')) $('edges').append(line);
 }
 const details = RunvardMapDetails.create({
  getState:() => state, t, el, icon, deviceIcon, kindLabel, label, status, date, originLabel, request, refresh,
  onSelection:id => { selected = id; highlight(); }, revealNode:focusNode
 });
 function rect(element, {x,y,width,height}) {
  Object.assign(element.style, {left:x+'px', top:y+'px', width:width+'px', height:height+'px'});
 }
 function currentFolds() {
  return new Set(stage.groups.filter(g => g.collapsed && g.count).map(g => g.network));
 }
 function renderGroup(group, boxes, controls) {
  const box = el('div', 'network-group ' + group.network);
  rect(box, group);
  boxes.append(box);
  const head = el(group.canCollapse ? 'button' : 'div', 'group-label');
  head.dataset.group = group.network;
  head.style.left = group.x + 8 + 'px';
  head.style.top = group.y + 4 + 'px';
  head.append(icon(({lan:'network',wlan:'wifi',zigbee:'zigbee',unknown:'link'})[group.network]),
   el('span','',groupLabel(group.network)), el('span','group-count',group.count));
  if (group.canCollapse) {
   head.setAttribute('aria-expanded', String(!group.collapsed));
   head.append(icon('chevron'));
   head.lastChild.classList.add('group-chevron');
   head.onclick = () => {
    const collapsed = currentFolds();
    if (group.collapsed) collapsed.delete(group.network);
    else collapsed.add(group.network);
    folds.set(foldMode(), collapsed);
    renderMap();
   };
  }
  controls.append(head);
  if (!group.count && !group.collapsed) {
   const empty = el('p','group-empty',t('emptyGroup'));
   empty.style.left = group.x + 16 + 'px';
   empty.style.top = group.contentTop + 'px';
   controls.append(empty);
  }
  if (group.control) {
   const button = el('button','group-toggle',group.hidden
    ? '+ ' + group.hidden + ' ' + t(group.hidden === 1 ? 'moreDevice' : 'moreDevices') : t('collapseGroup'));
   button.dataset.more = group.network;
   button.setAttribute('aria-expanded', String(!group.hidden));
   rect(button, group.control);
   button.onclick = () => {
    if (group.hidden) expandedGroups.add(group.network);
    else expandedGroups.delete(group.network);
    renderMap();
   };
   controls.append(button);
  }
 }
 function buildNode(d) {
  const role = stage.deviceRoles[d.id], code = statusCode(d);
  const node = el('button', 'node ' + role + ' ' + code);
  node.type = 'button';
  node.dataset.id = d.id;
  node.dataset.group = stage.deviceGroups[d.id];
  node.setAttribute('aria-haspopup','dialog');
  node.setAttribute('aria-controls','details');
  rect(node, {...stage.positions[d.id], ...stage.sizes[d.id]});
  const symbol = el('span','node-icon');
  symbol.append(icon(deviceIcon(d)));
  const copy = el('span','node-copy');
  if (role !== 'endpoint') copy.append(el('span','node-role',kindLabel(d)));
  copy.append(el('strong','',label(d)));
  const address = [d.ip, d.room, d.ieee, d.model, d.manufacturer].find(value => value && value !== label(d));
  const secondary = el('small','node-meta');
  if (code === 'offline' || code === 'out_of_scope') {
   secondary.append(icon('unavailable'), el('span','',t(code === 'offline' ? 'offline' : 'outOfScopeShort')));
  } else {
   if (code === 'online') secondary.append(el('i','status-dot online'));
   secondary.append(el('span','',address || t(status(d))));
  }
  copy.append(secondary);
  node.append(symbol,copy);
  if (state.can_edit && role !== 'gateway' && !filtering) {
   const grip = el('span','node-grip');
   grip.append(icon('grip'));
   node.append(grip);
   node.addEventListener('pointerdown', e => nodeDrag(e, node, d));
  }
  const description = [label(d),kindLabel(d),address,t(status(d))].filter(Boolean);
  if (d.id === contextId) {
   node.classList.add('context-node');
   description.push(t('filterContext'));
  }
  node.title = description.join(' · ');
  node.setAttribute('aria-label',node.title);
  node.onclick = () => { if (!node.suppressClick) details.select(d.id); };
  return node;
 }
 function renderMap() {
  const active = document.activeElement;
  const focusKey = $('nodes').contains(active) ? ['id','more','group'].find(key => active.dataset[key]) : null;
  const focusValue = focusKey && active.dataset[focusKey];
  layout();
  const boxes = document.createDocumentFragment(), nodes = document.createDocumentFragment();
  for (const group of stage.groups) renderGroup(group, boxes, nodes);
  for (const device of state.devices) {
   if (stage.positions[device.id]) nodes.append(buildNode(device));
  }
  $('groups').replaceChildren(boxes);
  $('nodes').replaceChildren(nodes);
  drawEdges();
  highlight();
  transform();
  const empty = $('empty');
  empty.hidden = Boolean(matches.length);
  empty.textContent = matches.length ? '' : t(state.devices.length ? 'noResults' : 'empty');
  $('clear-filters').hidden = !filtering;
  $('count').textContent = filtering ? t('resultCount').replace('{shown}',matches.length).replace('{total}',state.devices.length) : '';
  if (focusKey) {
   const element = [...$('nodes').querySelectorAll('button')].find(b => b.dataset[focusKey] === focusValue);
   (element || viewport).focus({preventScroll:true});
  }
 }
 function focusNode(id) {
  if (!stage.positions[id]) {
   $('search').value = '';
   $('network-filter').value = $('status-filter').value = 'all';
   layout();
   const group = topology.deviceGroups[id], collapsed = currentFolds();
   collapsed.delete(group);
   expandedGroups.add(group);
   folds.set(foldMode(),collapsed);
   renderMap();
  }
  const p = stage.positions[id], size = stage.sizes[id];
  if (p) viewport.scrollTo({left:(p.x+size.width/2)*scale-viewport.clientWidth/2, top:(p.y+size.height/2)*scale-viewport.clientHeight/2});
 }
 function nodeDrag(event, node, device) {
  if (event.button !== 0 || event.pointerType === 'touch' && !event.target.closest('.node-grip')) return;
  event.stopPropagation();
  const group = stage.groups.find(g => g.network === stage.deviceGroups[device.id]);
  const size = stage.sizes[device.id], start = {...stage.positions[device.id]};
  let moved = false, frame;
  const redraw = () => { drawEdges(); highlight(); frame = null; };
  node.setPointerCapture(event.pointerId);
  node.onpointermove = e => {
   const dx = (e.clientX-event.clientX)/scale, dy = (e.clientY-event.clientY)/scale;
   if (!moved && Math.abs(dx)+Math.abs(dy) < 5) return;
   moved = dragging = true;
   const p = {
    x:Math.max(group.x+12, Math.min(group.x+group.width-size.width-12,start.x+dx)),
    y:Math.max(group.contentTop, Math.min((group.control?.y ?? group.y+group.height)-size.height-8,start.y+dy))
   };
   stage.positions[device.id] = p;
   rect(node,{...p,...size});
   if (!frame) frame = requestAnimationFrame(redraw);
  };
  node.onpointerup = node.onpointercancel = async e => {
   node.onpointermove = node.onpointerup = node.onpointercancel = null;
   if (!moved) return;
   cancelAnimationFrame(frame);
   node.suppressClick = true;
   setTimeout(() => {node.suppressClick = false;}, 0);
   const p = stage.positions[device.id];
   try {
    if (e.type !== 'pointercancel') {
     const patch = {...p,layout_group:group.network};
     await request('device',{id:device.id,...patch});
     Object.assign(device,patch);
    }
   } catch(err) { message(err.message); }
   finally { dragging = false; renderMap(); }
  };
 }
 viewport.addEventListener('pointerdown',e => {
  if (e.button !== 0 || e.pointerType !== 'mouse' || e.target.closest('button')) return;
  const {scrollLeft,scrollTop} = viewport;
  viewport.setPointerCapture(e.pointerId);
  viewport.onpointermove = ev => {
   dragging = true;
   viewport.scrollLeft = scrollLeft + e.clientX - ev.clientX;
   viewport.scrollTop = scrollTop + e.clientY - ev.clientY;
  };
  viewport.onpointerup = viewport.onpointercancel = () => {
   dragging = false; viewport.onpointermove = viewport.onpointerup = viewport.onpointercancel = null;
  };
 });
 function zoom(factor) {
  if (!stage) return;
  const x = (viewport.scrollLeft+viewport.clientWidth/2)/scale, y = (viewport.scrollTop+viewport.clientHeight/2)/scale;
  scale = Math.max(.6,Math.min(1.8,scale*factor));
  transform();
  viewport.scrollTo({left:x*scale-viewport.clientWidth/2,top:y*scale-viewport.clientHeight/2});
 }
 function resetView() { scale = 1; renderMap(); viewport.scrollTo({left:0,top:0}); }
 viewport.addEventListener('wheel',e => {
  if (e.ctrlKey || e.metaKey) {e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 1/1.1);}
 },{passive:false});
 viewport.addEventListener('keydown',e => {
  if (e.target !== viewport) return;
  if (['+','=','-','0'].includes(e.key)) {
   e.preventDefault();
   if (e.key === '0') resetView(); else zoom(e.key === '-' ? 1/1.15 : 1.15);
  }
 });
 $('plus').onclick = () => zoom(1.15);
 $('minus').onclick = () => zoom(1/1.15);
 $('fit').onclick = resetView;
 function filtersChanged() {
  folds.delete('mobile-filtered'); folds.delete('desktop-filtered');
  renderMap();
  if (selected && !stage.positions[selected]) {
   const focused = document.activeElement;
   details.close();
   focused?.focus({preventScroll:true});
  }
  viewport.scrollTo({top:0,left:0});
 }
 $('search').oninput = $('network-filter').onchange = $('status-filter').onchange = filtersChanged;
 $('clear-filters').onclick = () => {
  $('network-filter').value = $('status-filter').value = 'all';
  $('search').value = '';
  filtersChanged();
  $('search').focus();
 };
 $('language').onchange = () => {
  lang = $('language').value;
  try {localStorage.setItem('runvard_language',lang);} catch(_) {}
  translate(); renderMap(); details.render(true); renderStatus();
 };
 function openSettings(section='') {
  $('networks').value = state.settings.networks.join('\n');
  const found = state.devices.filter(d => d.zigbee_url);
  $('zigbee-url').value = state.settings.zigbee_url || (found.length === 1 ? found[0].zigbee_url : '');
  $('token').value = $('fritz-password').value = '';
  $('clear-token').checked = $('clear-fritz').checked = false;
  $('fritz-host').value = state.settings.fritz_host || state.devices.find(d => d.id === topology.gateway)?.ip || '';
  $('fritz-user').value = state.settings.fritz_user || '';
  $('settings-message').textContent = '';
  $('settings-dialog').showModal();
  if (section) $(section === 'zigbee' ? 'zigbee-url' : 'fritz-host').focus();
 }
 function renderStatus() {
  const n = state.devices.length, links = state.edges.length;
  const metrics = [n+' '+t(n === 1 ? 'device' : 'devices'), links+' '+t(links === 1 ? 'connectionSingular' : 'connections')];
  for (const key of ['online','offline']) {
   const count = state.devices.filter(d => statusCode(d) === key).length;
   if (count) metrics.push(count+' '+t(key));
  }
  $('overview').replaceChildren(...metrics.map(text => el('span','',text)));
  $('scan').replaceChildren(icon('refresh'),el('span','',t(state.scanning ? 'scanning' : 'scan')));
  $('scan').title = t(state.scanning ? 'scanning' : 'scan');
  $('scan').setAttribute('aria-busy',String(Boolean(state.scanning)));
  $('scan').disabled = !state.can_edit || state.scanning;
  $('settings').disabled = $('setup-zigbee').disabled = $('setup-fritz').disabled = !state.can_edit;
  $('mesh').hidden = !state.connected;
  $('mesh').disabled = !state.can_edit || !state.connected;
  $('connection').textContent = t(state.connected ? 'connected' : state.settings.zigbee_url ? 'disconnected' : 'zigbeeMissing');
  $('integration-copy').textContent = state.connected ? '' : t(state.devices.filter(d => d.zigbee_url).length === 1 ? 'setupFound' : 'setupCopy');
  $('setup-zigbee').hidden = state.connected;
  $('fritz-status').textContent = t(!state.settings.fritz_host ? 'fritzNotSet' : state.fritz_error ? 'fritzError' : state.fritz_at ? 'fritzReady' : 'fritzPending');
  $('setup-fritz').textContent = t(state.settings.fritz_host ? 'settings' : 'setupFritz');
  for (const [id,key,at] of [['scan-time','scanTime',state.scan_at],['mesh-time','meshTime',state.mesh_at],['fritz-time','fritzTime',state.fritz_at]]) {
   $(id).textContent = t(key) + ' · ' + date(at);
  }
 }
 async function refresh(force=false) {
  if (pendingRefresh) {
   await pendingRefresh;
   if (!force) return;
  }
  if (dragging && !force) return;
  const read = async () => {
   try {
    const response = await fetch('/api/network-map',{cache:'no-store'});
    if (response.status === 401) {location.href = '/login'; return;}
    if (!response.ok) throw Error('error');
    const next = await response.json();
    const nextSignature = JSON.stringify([next.devices,next.edges,next.can_edit]);
    state = next;
    if (force || signature !== nextSignature) {
     signature = nextSignature;
     topology = RunvardMapLayout.classifyTopology(state.devices,state.edges);
     renderMap();
     details.render();
    }
    renderStatus();
    if (state.error || state.zigbee_error) message('error');
    else if (messageKey === 'error') message('');
   } catch(_) { message('error'); }
  };
  const current = read();
  pendingRefresh = current;
  try { await current; } finally { if (pendingRefresh === current) pendingRefresh = null; }
 }
 $('scan').onclick = async () => {
  $('scan').disabled = true;
  try {
   await request('scan'); state.scanning = true; renderStatus();
   setTimeout(refresh,500);
  } catch(err) { message(err.message); renderStatus(); }
 };
 $('mesh').onclick = async () => {
  try {await request('mesh'); $('sources-dialog').close(); message('meshPending');} catch(err) { $('sources-dialog').close(); message(err.message); }
 };
 $('integrations').onclick = () => $('sources-dialog').showModal();
 $('close-sources').onclick = () => $('sources-dialog').close();
 $('settings').onclick = () => openSettings();
 $('setup-zigbee').onclick = () => openSettings('zigbee');
 $('setup-fritz').onclick = () => openSettings('fritz');
 $('close-settings').onclick = () => $('settings-dialog').close();
 $('settings-form').onsubmit = async e => {
  e.preventDefault();
  const submit = e.submitter;
  if (submit) submit.disabled = true;
  try {
   await request('settings',{networks:$('networks').value.split('\n').map(x=>x.trim()).filter(Boolean),
    zigbee_url:$('zigbee-url').value.trim(), token:$('token').value, clear_token:$('clear-token').checked,
    fritz_host:$('fritz-host').value.trim(), fritz_user:$('fritz-user').value.trim(), fritz_password:$('fritz-password').value, clear_fritz_password:$('clear-fritz').checked});
   if ($('fritz-host').value.trim()) await request('scan');
   $('settings-dialog').close();
   $('sources-dialog').close();
   await refresh(true); message('saved');
  } catch(err) { $('settings-message').textContent = t(err.message); }
  finally { if (submit) submit.disabled = false; }
 };
 // Opening the desktop details pane also changes the width available to the graph.
 const observer = new ResizeObserver(() => {
  if (!dragging && stage && stage.width !== Math.max(270,viewport.clientWidth)) renderMap();
 });
 observer.observe(viewport);
 document.querySelectorAll('[data-icon]').forEach(e => e.append(icon(e.dataset.icon)));
 translate();
 await refresh(true);
 setInterval(() => {if (!document.hidden) refresh();},5000);
})();
