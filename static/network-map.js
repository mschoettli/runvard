'use strict';
(async()=>{
 const $=id=>document.getElementById(id);
 const catalogs=await (await fetch('/static/network-map-i18n.json?v=4')).json();
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
  refresh:['M20 7a8 8 0 1 0 1 7M20 3v5h-5']
 };
 function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');for(const d of iconPaths[name]||iconPaths.device){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}return svg;}
 function deviceIcon(d){return d.network==='zigbee'?(d.kind==='Coordinator'||d.kind==='Router'?'zigbee':'sensor'):d.kind==='router'?'wifi':d.kind==='bridge'?'zigbee':d.ports?.includes(22)?'server':'device';}
 let state={devices:[],edges:[],settings:{networks:[]},can_edit:false},selected='',stage={width:300,height:200,positions:{},groups:[],cardWidth:210,cardHeight:96},positions=new Map(),scale=1,busy=false,dragging=false,formDirty=false,editOpen=false,signature='',lastDetailId='';
 const expandedGroups=new Set(),seenDevices=new Set();
 const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
 reducedMotion.addEventListener('change',()=>{if(reducedMotion.matches)document.querySelectorAll('.node').forEach(n=>n.getAnimations().forEach(a=>a.cancel()));});
 const originLabel=e=>t(e.origin==='manual'?'manual':e.origin==='fritz'?'fritzOrigin':'zigbee');
 const label=d=>d.label||d.name||d.ip||d.id;
 const date=value=>value?new Date(value*1000).toLocaleString(lang):t('never');
 const status=d=>['online','offline'].includes(d.status)?d.status:'unknown';
 function translate(){document.documentElement.lang=lang;document.title=t('title')+' · runvard';$('language').value=lang;
  document.querySelectorAll('[data-t]').forEach(e=>e.textContent=t(e.dataset.t));document.querySelectorAll('[data-placeholder]').forEach(e=>{e.placeholder=t(e.dataset.placeholder);e.setAttribute('aria-label',t(e.dataset.placeholder));});document.querySelectorAll('[data-aria]').forEach(e=>e.setAttribute('aria-label',t(e.dataset.aria)));
  $('language').setAttribute('aria-label',t('language'));$('filter').setAttribute('aria-label',t('all'));$('viewport').setAttribute('aria-label',t('title'));$('viewport').title=t('mapHelp');}
 function message(key){$('message').textContent=key?t(key):'';}
 async function request(action,data={}){const r=await fetch('/api/network-map/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(r.status===401){location.href='/login';throw Error('error');}if(!r.ok)throw Error(r.status===400?'invalid':'error');return r.json();}
 function allGroups(){return RunvardMapLayout.layoutGroups(state.devices,state.edges,$('viewport').clientWidth,expandedGroups);}
 function visible(){const query=$('search').value.toLocaleLowerCase(lang),filter=$('filter').value,groups=allGroups().deviceGroups;return state.devices.filter(d=>{
  const group=groups[d.id];const match=filter==='all'||filter==='unknownConnection'&&group==='unknown'||['lan','wlan','zigbee'].includes(filter)&&group===filter||['online','offline','unknown'].includes(filter)&&status(d)===filter;
  return match&&[label(d),d.name,d.ip,d.mac,d.room,d.manufacturer,d.model].filter(Boolean).join(' ').toLocaleLowerCase(lang).includes(query);
 });}
 function layout(){stage=RunvardMapLayout.layoutGroups(visible(),state.edges,$('viewport').clientWidth,expandedGroups);positions=new Map(Object.entries(stage.positions));}
 function transform(){$('world').style.width=stage.width+'px';$('world').style.height=stage.height+'px';$('world').style.transform=`scale(${scale})`;$('canvas').style.width=Math.ceil(stage.width*scale)+'px';$('canvas').style.height=Math.ceil(stage.height*scale)+'px';$('zoom').textContent=Math.round(scale*100)+'%';}
 function resetView(){scale=1;layout();renderMap();$('viewport').scrollTo({left:0,top:0});}
 function drawEdges(){const svg=$('edges');svg.replaceChildren();const groups=new Map(stage.groups.map(g=>[g.network,g]));const rendered=new Set();
  for(const [index,e] of state.edges.entries()){
   const a=positions.get(e.source),b=positions.get(e.target);if(!a||!b)continue;
   const key=[e.origin,...[e.source,e.target].sort()].join(':');if(rendered.has(key))continue;rendered.add(key);
   const sourceGroup=stage.deviceGroups[e.source],targetGroup=stage.deviceGroups[e.target],ga=groups.get(sourceGroup),gb=groups.get(targetGroup);
   const ax=a.x+105,ay=a.y+48,bx=b.x+105,by=b.y+48;let route;
   if(ga&&gb&&sourceGroup===targetGroup){const lane=ga.x+12+(index%3)*3;route=`M ${a.x} ${ay} H ${lane} V ${by} H ${b.x}`;}
   else{const lane=Math.min(ga?.y??168,gb?.y??168)-14-(index%3)*7;
    const fromX=ga?ga.x+ga.width+(stage.width<860?8:-10):ax,toX=gb?gb.x+(stage.width<860?-8:12):bx;
    route=ga?`M ${a.x+210} ${ay} H ${fromX} V ${lane}`:`M ${ax} ${a.y+96} V ${lane}`;
    route+=` H ${toX}`+(gb?` V ${by} H ${b.x}`:` V ${b.y}`);
   }
   const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',route);path.setAttribute('fill','none');path.classList.add(e.origin==='manual'?'manual':e.origin==='fritz'?'observed':'zigbee');
   if(selected)path.classList.add(e.source===selected||e.target===selected?'selected':'dim');
   const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=originLabel(e)+(e.lqi!=null?' · LQI '+e.lqi:'');path.append(title);svg.append(path);
   if(e.source===selected||e.target===selected){const text=document.createElementNS(svg.namespaceURI,'text');text.setAttribute('x',ga&&gb&&ga===gb?bx:(ax+bx)/2);text.setAttribute('y',ga&&gb&&ga===gb?b.y-8:Math.min(ga?.y??168,gb?.y??168)-22-(index%3)*7);text.textContent=e.lqi!=null?'LQI '+e.lqi:e.type==='wlan'?t('groupWlan'):e.type==='lan'?'LAN':originLabel(e);text.setAttribute('text-anchor','middle');text.classList.add('edge-label');svg.append(text);}
  }
 }
 function renderMap(animateLayout=false){
  const nodes=$('nodes'),oldPositions=new Map([...nodes.querySelectorAll('.node')].map(n=>[n.dataset.id,{x:parseFloat(n.style.left),y:parseFloat(n.style.top)}])),focusId=document.activeElement?.dataset?.id;
  nodes.replaceChildren();$('groups').replaceChildren();$('group-jumps').replaceChildren();$('group-jumps').setAttribute('aria-label',t('title'));const devices=visible();$('count').textContent=devices.length+' / '+state.devices.length;$('empty').textContent=state.devices.length?(devices.length?'':t('noResults')):t('empty');
  for(const group of stage.groups){
   const box=el('div','network-group '+group.network+(stage.deviceGroups[selected]===group.network?' active':''));Object.assign(box.style,{left:group.x+'px',top:group.y+'px',width:group.width+'px',height:group.height+'px'});$('groups').append(box);
   const heading=el(group.control?'button':'div','group-label'+(group.control?' group-heading':''));heading.dataset.groupHeading=group.network;heading.tabIndex=group.control?0:-1;if(group.control){heading.type='button';heading.setAttribute('aria-expanded',String(expandedGroups.has(group.network)));heading.classList.toggle('expanded',expandedGroups.has(group.network));}heading.style.left=(group.x+20)+'px';heading.style.top=(group.y+22)+'px';heading.append(icon(group.network==='zigbee'?'zigbee':group.network==='wlan'?'wifi':group.network==='unknown'?'link':'network'),el('span','',group.network==='zigbee'?'Zigbee':t(group.network==='lan'?'groupLan':group.network==='wlan'?'groupWlan':'groupUnknown')),el('span','group-count',group.count+' '+t(group.count===1?'device':'devices')));if(group.control){const arrow=icon('chevron');arrow.classList.add('group-chevron');heading.append(arrow);heading.onclick=()=>{expandedGroups.has(group.network)?expandedGroups.delete(group.network):expandedGroups.add(group.network);layout();renderMap(true);document.querySelector(`[data-group-heading="${group.network}"]`)?.focus({preventScroll:true});};}nodes.append(heading);
   const jump=el('button','group-jump'+(stage.deviceGroups[selected]===group.network?' active':''),(group.network==='zigbee'?'Zigbee':t(group.network==='lan'?'groupLan':group.network==='wlan'?'groupWlan':'groupUnknown'))+' · '+group.count);jump.type='button';jump.setAttribute('aria-controls','viewport');jump.onclick=()=>{$('viewport').scrollTo({left:Math.max(0,group.x*scale-12),top:Math.max(0,group.y*scale-12),behavior:reducedMotion.matches?'auto':'smooth'});document.querySelector(`[data-group-heading="${group.network}"]`)?.focus({preventScroll:true});};$('group-jumps').append(jump);
   if(group.network==='unknown'){const note=el('p','group-note',t('unknownHint'));Object.assign(note.style,{left:(group.x+20)+'px',top:(group.y+48)+'px',width:(group.width-40)+'px'});nodes.append(note);}
   if(!group.count){const empty=el('div','group-empty');empty.style.left=(group.x+24)+'px';empty.style.top=(group.y+76)+'px';empty.style.width=(group.width-48)+'px';empty.append(el('p','hint',t('emptyGroup')));if(state.can_edit){const setup=el('button','btn ghost',t(group.network==='zigbee'?'setupZigbee':'setupFritz'));setup.onclick=()=>openSettings(group.network==='zigbee'?'zigbee':'fritz');empty.append(setup);}nodes.append(empty);}
   if(group.control){const control=el('button','group-toggle');control.dataset.group=group.network;const r=group.control;Object.assign(control.style,{left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px'});control.textContent=group.hidden?'+ '+group.hidden+' '+t(group.hidden===1?'moreDevice':'moreDevices'):t('collapseGroup');control.setAttribute('aria-expanded',expandedGroups.has(group.network)?'true':'false');control.onclick=()=>{expandedGroups.has(group.network)?expandedGroups.delete(group.network):expandedGroups.add(group.network);layout();renderMap(true);document.querySelector(`[data-group="${group.network}"]`)?.focus({preventScroll:true});};nodes.append(control);}
  }
  for(const d of devices){const p=positions.get(d.id);if(!p)continue;const b=el('button','node '+status(d)+(selected===d.id?' selected':'')+(stage.deviceGroups[d.id]==='gateway'?' gateway-node':''));b.type='button';b.dataset.id=d.id;b.style.left=p.x+'px';b.style.top=p.y+'px';
   const symbol=el('span','node-icon');symbol.append(icon(deviceIcon(d)));const copy=el('span','node-copy');copy.append(el('strong','',label(d)));const secondary=label(d)===(d.ip||d.ieee)?(d.manufacturer||t('unnamed')):(d.ip||d.ieee||d.model||'');copy.append(el('small','',secondary));b.append(symbol,copy,el('i','status-dot '+status(d)));
   if(state.can_edit&&stage.deviceGroups[d.id]!=='gateway'){const grip=el('span','node-grip','⠿');grip.setAttribute('aria-hidden','true');b.append(grip);}
   b.title=label(d)+' · '+(d.ip||d.ieee||'')+' · '+t(status(d));b.setAttribute('aria-label',label(d)+' · '+t(status(d)));b.setAttribute('aria-pressed',selected===d.id?'true':'false');
   b.onclick=()=>{if(b.dataset.moved==='1'){b.dataset.moved='';return;}select(d.id);};b.addEventListener('pointerdown',e=>nodeDrag(e,b,d));nodes.append(b);
   if(!seenDevices.has(d.id)){if(!reducedMotion.matches&&!document.hidden){b.classList.add('node-enter');b.style.setProperty('--enter-delay',Math.min(180,seenDevices.size*20)+'ms');b.addEventListener('animationend',()=>b.classList.remove('node-enter'),{once:true});}seenDevices.add(d.id);}
   const old=oldPositions.get(d.id);if(animateLayout&&old&&!reducedMotion.matches&&!document.hidden){b.animate([{transform:`translate(${old.x-p.x}px,${old.y-p.y}px)`},{transform:'translate(0,0)'}],{duration:200,easing:'cubic-bezier(.2,.7,.2,1)'});}
   if(focusId===d.id)b.focus({preventScroll:true});
  }
  drawEdges();transform();
 }
 function select(id){selected=id;formDirty=false;editOpen=false;renderMap();renderDetails();}
 function field(form,key,value,tag='input'){const l=el('label'),input=el(tag);input.name=key;input.value=value||'';input.maxLength=key==='notes'?1000:160;l.append(el('span','',t(key)),input);form.append(l);return input;}
 function summary(root){const title=el('div','overview-title');title.append(icon('network'),el('h2','',t('overview')));root.append(title,el('div','overview-count',state.devices.length),el('div','overview-label',t(state.devices.length===1?'device':'devices')));const stats=el('div','summary-stats');
  for(const key of ['online','offline']){const stat=el('div','summary-stat');stat.append(el('i','status-dot '+key),el('strong','',state.devices.filter(d=>status(d)===key).length),el('span','',t(key)));stats.append(stat);}root.append(stats);const graphic=el('div','selection-visual');graphic.append(icon('select'));root.append(graphic,el('p','hint',t('selectionHint')));
  const row=el('div','kv');row.append(el('span','',t('connections')),el('strong','',state.edges.length));root.append(row);}
 function renderDetails(){const root=$('details');root.replaceChildren();const d=state.devices.find(x=>x.id===selected);if(!d){summary(root);return;}
  const heading=el('div','detail-heading'),symbol=el('span','node-icon');symbol.append(icon(deviceIcon(d)));heading.append(symbol,el('h2','',label(d)));if(lastDetailId!==d.id){heading.classList.add('detail-enter');lastDetailId=d.id;}root.append(heading);const badge=el('div','detail-status');badge.append(el('i','status-dot '+status(d)),el('span','',t(status(d))));root.append(badge);
  const kind=d.kind==='Coordinator'?'coordinator':d.kind==='Router'||d.kind==='router'?'router':'endDevice';root.append(el('div','device-kind',t(kind)+(d.room?' · '+d.room:'')));
  for(const [key,value] of [['IP',d.ip],['MAC',d.mac],['IEEE',d.ieee],['manufacturer',d.manufacturer],['model',d.model],['battery',d.battery!=null?d.battery+' %':null],['lqi',d.linkquality],['lastSeen',d.last_seen?date(d.last_seen):null]]){if(value==null||value==='')continue;const row=el('div','kv'+(['IP','MAC','IEEE'].includes(key)?' mono':''));row.append(el('span','',['IP','MAC','IEEE'].includes(key)?key:t(key)),el('strong','',String(value)));root.append(row);}
  root.append(el('h3','',t('connections')));const neighbors=el('div','neighbors');const links=state.edges.filter(e=>e.source===d.id||e.target===d.id);
  for(const e of links){const other=state.devices.find(x=>x.id===(e.source===d.id?e.target:e.source));if(!other)continue;const button=el('button','neighbor'),copy=el('span');copy.append(el('strong','',label(other)),el('small','',originLabel(e)+(e.lqi!=null?' · LQI '+e.lqi:'')));button.append(icon(deviceIcon(other)),copy);button.onclick=()=>{select(other.id);focusNode(other.id);};neighbors.append(button);}
  if(!links.length){const empty=el('div','connection-empty');empty.append(icon('link'),el('p','',t('noConnections')));neighbors.append(empty);}root.append(neighbors);
  if(!state.can_edit){root.append(el('p','hint',t('readOnly')));return;}
  const editor=el('details','editor');editor.open=editOpen;const disclosure=el('summary');disclosure.append(icon('edit'),el('span','',t('edit')));editor.append(disclosure);editor.addEventListener('toggle',()=>{editOpen=editor.open;});
  const form=el('form');form.oninput=()=>{formDirty=true;};field(form,'name',d.label).name='label';field(form,'room',d.room);field(form,'notes',d.notes,'textarea');const parent=field(form,'parent','','select');parent.append(new Option(t('none'),''));for(const other of state.devices.filter(x=>x.id!==d.id))parent.append(new Option(label(other),other.id));parent.value=d.parent||'';
  const connection=field(form,'connectionType','','select');connection.name='connection_type';for(const key of ['unknown','lan','wlan'])connection.append(new Option(t(key==='unknown'?'groupUnknown':key==='lan'?'groupLan':'groupWlan'),key));connection.value=d.connection_type||'unknown';
  const save=el('button','btn',t('save'));form.append(save);form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await request('device',{id:d.id,...Object.fromEntries(new FormData(form))});formDirty=false;editOpen=false;await refresh(true);message('saved');}catch(err){message(err.message);}finally{save.disabled=false;}};editor.append(form);root.append(editor);}
 function focusNode(id){
  if(!positions.has(id)){const key=allGroups().deviceGroups[id];if(key)expandedGroups.add(key);$('filter').value='all';$('search').value='';layout();renderMap(true);}
  const p=positions.get(id);if(p)$('viewport').scrollTo({left:Math.max(0,(p.x+105)*scale-$('viewport').clientWidth/2),top:Math.max(0,(p.y+48)*scale-$('viewport').clientHeight/2)});
 }
 function nodeDrag(e,node,d){if(e.button!==0||!state.can_edit||stage.deviceGroups[d.id]==='gateway'||(e.pointerType==='touch'&&!e.target.closest('.node-grip')))return;e.stopPropagation();node.setPointerCapture(e.pointerId);const start={x:e.clientX,y:e.clientY,p:{...positions.get(d.id)}};let moved=false;
  node.onpointermove=event=>{const dx=(event.clientX-start.x)/scale,dy=(event.clientY-start.y)/scale;if(Math.abs(dx)+Math.abs(dy)<5&&!moved)return;moved=true;dragging=true;const g=stage.groups.find(g=>g.network===stage.deviceGroups[d.id]);const p={x:Math.max(g.x+12,Math.min(g.x+g.width-222,start.p.x+dx)),y:Math.max(g.y+(g.network==='unknown'?(stage.width<600?148:100):72),Math.min(g.y+g.height-120,start.p.y+dy))};positions.set(d.id,p);node.style.left=p.x+'px';node.style.top=p.y+'px';stage.width=Math.max(stage.width,p.x+240);stage.height=Math.max(stage.height,p.y+120);drawEdges();transform();};
  const finish=async()=>{node.onpointermove=null;node.onpointerup=null;node.onpointercancel=null;dragging=false;if(!moved)return;node.dataset.moved='1';const p=positions.get(d.id);try{await request('device',{id:d.id,...p,layout_group:stage.deviceGroups[d.id]});d.x=p.x;d.y=p.y;d.layout_group=stage.deviceGroups[d.id];}catch(err){positions.set(d.id,start.p);layout();renderMap();message(err.message);}};node.onpointerup=finish;node.onpointercancel=finish;}
 $('viewport').addEventListener('pointerdown',e=>{if(e.button!==0||e.pointerType!=='mouse'||e.target.closest('button'))return;const v=$('viewport'),start={x:e.clientX,y:e.clientY,left:v.scrollLeft,top:v.scrollTop};v.setPointerCapture(e.pointerId);v.onpointermove=ev=>{dragging=true;v.scrollLeft=start.left+start.x-ev.clientX;v.scrollTop=start.top+start.y-ev.clientY;};v.onpointerup=v.onpointercancel=()=>{dragging=false;v.onpointermove=null;v.onpointerup=null;v.onpointercancel=null;};});
 function zoom(factor){const v=$('viewport'),x=(v.scrollLeft+v.clientWidth/2)/scale,y=(v.scrollTop+v.clientHeight/2)/scale;scale=Math.max(.6,Math.min(1.8,scale*factor));transform();v.scrollLeft=x*scale-v.clientWidth/2;v.scrollTop=y*scale-v.clientHeight/2;}
 $('viewport').addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();zoom(e.deltaY<0?1.1:1/1.1);}},{passive:false});
 $('viewport').addEventListener('keydown',e=>{if(e.target!==$('viewport'))return;if(e.key==='+'||e.key==='='){e.preventDefault();zoom(1.15);}else if(e.key==='-'){e.preventDefault();zoom(1/1.15);}else if(e.key==='0')resetView();});
 $('plus').onclick=()=>zoom(1.15);$('minus').onclick=()=>zoom(1/1.15);$('fit').onclick=resetView;
 $('search').oninput=$('filter').onchange=()=>{layout();renderMap();$('viewport').scrollTo({top:0,left:0});};
 $('language').onchange=()=>{lang=$('language').value;try{localStorage.setItem('runvard_language',lang);}catch(_){}translate();layout();renderMap();renderDetails();renderStatus();};
 function openSettings(section=''){
  $('networks').value=state.settings.networks.join('\n');const found=state.devices.filter(d=>d.zigbee_url);$('zigbee-url').value=state.settings.zigbee_url||(found.length===1?found[0].zigbee_url:'');$('token').value='';$('clear-token').checked=false;
  const gateway=state.devices.find(d=>d.gateway)||state.devices.find(d=>/FRITZ!Box/i.test(d.model||d.name||''));$('fritz-host').value=state.settings.fritz_host||gateway?.ip||'';$('fritz-user').value=state.settings.fritz_user||'';$('fritz-password').value='';$('clear-fritz').checked=false;
  $('settings-message').textContent='';$('settings-dialog').showModal();if(section==='zigbee')$('zigbee-url').focus();if(section==='fritz')$('fritz-host').focus();
 }
 function renderStatus(){
  $('scan').replaceChildren(icon('refresh'),el('span','',t(state.scanning?'scanning':'scan')));$('scan').classList.toggle('scanning',state.scanning);$('scan').setAttribute('aria-busy',String(state.scanning));$('scan').disabled=!state.can_edit||state.scanning;$('settings').disabled=!state.can_edit;$('mesh').hidden=!state.connected;$('mesh').disabled=!state.can_edit||!state.connected;
  $('connection').textContent=t(state.connected?'connected':state.settings.zigbee_url?'disconnected':'zigbeeMissing');$('integration-dot').className='status-dot '+(state.connected?'online':'unknown');
  $('integration-copy').textContent=state.connected?t('meshTime')+': '+date(state.mesh_at):t(state.devices.filter(d=>d.zigbee_url).length===1?'setupFound':'setupCopy');$('setup-zigbee').hidden=state.connected;$('setup-zigbee').disabled=!state.can_edit;
  $('fritz-status').textContent=t(state.fritz_error?'fritzError':state.fritz_at?'fritzReady':'fritzNotSet');$('fritz-dot').className='status-dot '+(state.fritz_error?'offline':state.fritz_at?'online':'unknown');$('setup-fritz').disabled=!state.can_edit;$('setup-fritz').textContent=t(state.settings.fritz_host?'settings':'setupFritz');document.querySelector('.fritz-panel .hint').hidden=!!state.settings.fritz_host;$('fritz-time').textContent=t('fritzTime')+': '+date(state.fritz_at);
  $('scan-time').textContent=t('scanTime')+': '+date(state.scan_at);$('mesh-time').textContent=t('meshTime')+': '+date(state.mesh_at);
 }
 async function refresh(force=false){if(busy||dragging)return;busy=true;try{const r=await fetch('/api/network-map',{cache:'no-store'});if(r.status===401){location.href='/login';return;}if(!r.ok)throw Error('error');const next=await r.json(),nextSignature=JSON.stringify([next.devices,next.edges,next.can_edit]);state=next;
   if(force||signature!==nextSignature){signature=nextSignature;layout();renderMap();if(!formDirty&&!editOpen)renderDetails();}renderStatus();if(state.error||state.zigbee_error)message('error');
  }catch(_){message('error');}finally{busy=false;}}
 $('scan').onclick=async()=>{try{await request('scan');state.scanning=true;renderStatus();setTimeout(()=>refresh(),500);}catch(err){message(err.message);}};
 $('mesh').onclick=async()=>{try{await request('mesh');message('meshPending');}catch(err){message(err.message);}};
 $('settings').onclick=()=>openSettings();$('setup-zigbee').onclick=()=>openSettings('zigbee');$('setup-fritz').onclick=()=>openSettings('fritz');$('close-settings').onclick=()=>$('settings-dialog').close();
 $('settings-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;if(button)button.disabled=true;try{await request('settings',{networks:$('networks').value.split('\n').map(x=>x.trim()).filter(Boolean),zigbee_url:$('zigbee-url').value.trim(),token:$('token').value,clear_token:$('clear-token').checked,fritz_host:$('fritz-host').value.trim(),fritz_user:$('fritz-user').value.trim(),fritz_password:$('fritz-password').value,clear_fritz_password:$('clear-fritz').checked});$('settings-dialog').close();if($('fritz-host').value.trim())await request('scan');await refresh(true);message('saved');}catch(err){$('settings-message').textContent=t(err.message);}finally{if(button)button.disabled=false;}};
 let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(!dragging){layout();renderMap();}},120);});
 $('fritz-icon').append(icon('network'));$('search-icon').append(icon('search'));$('integration-icon').append(icon('zigbee'));
 translate();await refresh(true);setInterval(()=>{if(!document.hidden)refresh();},5000);
})();
