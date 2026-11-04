'use strict';
(async()=>{
 const $=id=>document.getElementById(id);
 const catalogs=await (await fetch('/static/network-map-i18n.json?v=3')).json();
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
  sensor:['M8 3h8v18H8zM11 7h2M11 17h2']
 };
 function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');for(const d of iconPaths[name]||iconPaths.device){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}return svg;}
 function deviceIcon(d){return d.network==='zigbee'?(d.kind==='Coordinator'||d.kind==='Router'?'zigbee':'sensor'):d.kind==='router'?'wifi':d.kind==='bridge'?'zigbee':d.ports?.includes(22)?'server':'device';}
 let state={devices:[],edges:[],settings:{networks:[]},can_edit:false},selected='',stage={width:300,height:200,positions:{},groups:[],cardWidth:210,cardHeight:96},positions=new Map(),scale=1,busy=false,dragging=false,formDirty=false,editOpen=false,signature='';
 const label=d=>d.label||d.name||d.ip||d.id;
 const date=value=>value?new Date(value*1000).toLocaleString(lang):t('never');
 const status=d=>['online','offline'].includes(d.status)?d.status:'unknown';
 function translate(){document.documentElement.lang=lang;document.title=t('title')+' · runvard';$('language').value=lang;
  document.querySelectorAll('[data-t]').forEach(e=>e.textContent=t(e.dataset.t));document.querySelectorAll('[data-placeholder]').forEach(e=>{e.placeholder=t(e.dataset.placeholder);e.setAttribute('aria-label',t(e.dataset.placeholder));});document.querySelectorAll('[data-aria]').forEach(e=>e.setAttribute('aria-label',t(e.dataset.aria)));
  $('language').setAttribute('aria-label',t('language'));$('filter').setAttribute('aria-label',t('all'));$('viewport').setAttribute('aria-label',t('title'));$('viewport').title=t('mapHelp');}
 function message(key){$('message').textContent=key?t(key):'';}
 async function request(action,data={}){const r=await fetch('/api/network-map/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(r.status===401){location.href='/login';throw Error('error');}if(!r.ok)throw Error(r.status===400?'invalid':'error');return r.json();}
 function visible(){const query=$('search').value.toLocaleLowerCase(lang),filter=$('filter').value;return state.devices.filter(d=>(filter==='all'||filter===d.network||filter===status(d))&&[label(d),d.name,d.ip,d.mac,d.room,d.manufacturer,d.model].filter(Boolean).join(' ').toLocaleLowerCase(lang).includes(query));}
 function layout(){stage=RunvardMapLayout.layoutNetwork(visible(),state.edges,$('viewport').clientWidth);positions=new Map(Object.entries(stage.positions));}
 function transform(){$('world').style.width=stage.width+'px';$('world').style.height=stage.height+'px';$('world').style.transform=`scale(${scale})`;$('canvas').style.width=Math.ceil(stage.width*scale)+'px';$('canvas').style.height=Math.ceil(stage.height*scale)+'px';$('zoom').textContent=Math.round(scale*100)+'%';}
 function resetView(){scale=1;layout();renderMap();$('viewport').scrollTo({left:0,top:0});}
 function drawEdges(){const svg=$('edges');svg.replaceChildren();const ids=new Set(visible().map(d=>d.id));
  for(const e of state.edges){if(!ids.has(e.source)||!ids.has(e.target))continue;const a=positions.get(e.source),b=positions.get(e.target);if(!a||!b)continue;
   const path=document.createElementNS(svg.namespaceURI,'path'),ax=a.x+105,ay=a.y+48,bx=b.x+105,by=b.y+48,dx=bx-ax,dy=by-ay;if(Math.abs(dx)+Math.abs(dy)<1)continue;
   if(Math.abs(dy)<100){const lane=Math.min(a.y,b.y)-12;path.setAttribute('d',`M ${ax} ${a.y} V ${lane} H ${bx} V ${b.y}`);}else{const f=Math.min(105/Math.max(Math.abs(dx),.001),48/Math.max(Math.abs(dy),.001));path.setAttribute('d',`M ${ax+dx*f} ${ay+dy*f} L ${bx-dx*f} ${by-dy*f}`);}
   path.setAttribute('fill','none');path.classList.add(e.origin==='manual'?'manual':'zigbee');if(selected)path.classList.add(e.source===selected||e.target===selected?'selected':'dim');
   const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=t(e.origin==='manual'?'manual':'zigbee')+(e.lqi!=null?' · LQI '+e.lqi:'');path.append(title);svg.append(path);}
 }
 function renderMap(){const nodes=$('nodes');nodes.replaceChildren();const devices=visible();$('count').textContent=devices.length+' / '+state.devices.length;$('empty').textContent=state.devices.length?(devices.length?'':t('noResults')):t('empty');
  for(const group of stage.groups){const heading=el('div','group-label');heading.style.top=group.y+'px';heading.append(icon(group.network==='zigbee'?'zigbee':'network'),el('span','',group.network==='zigbee'?'Zigbee':t('lan')),el('span','group-count',group.count+' '+t(group.count===1?'device':'devices')));nodes.append(heading);}
  for(const d of devices){const p=positions.get(d.id);if(!p)continue;const b=el('button','node '+status(d)+(selected===d.id?' selected':''));b.type='button';b.dataset.id=d.id;b.style.left=p.x+'px';b.style.top=p.y+'px';
   const symbol=el('span','node-icon');symbol.append(icon(deviceIcon(d)));const copy=el('span','node-copy');copy.append(el('strong','',label(d)));const secondary=label(d)===(d.ip||d.ieee)?(d.manufacturer||t('unnamed')):(d.ip||d.ieee||d.model||'');copy.append(el('small','',secondary));b.append(symbol,copy,el('i','status-dot '+status(d)));
   if(state.can_edit){const grip=el('span','node-grip','⠿');grip.setAttribute('aria-hidden','true');b.append(grip);}
   b.title=label(d)+' · '+(d.ip||d.ieee||'')+' · '+t(status(d));b.setAttribute('aria-label',label(d)+' · '+t(status(d)));b.setAttribute('aria-pressed',selected===d.id?'true':'false');
   b.onclick=()=>{if(b.dataset.moved==='1'){b.dataset.moved='';return;}select(d.id);};b.addEventListener('pointerdown',e=>nodeDrag(e,b,d));nodes.append(b);}
  drawEdges();transform();}
 function select(id){selected=id;formDirty=false;editOpen=false;renderMap();renderDetails();}
 function field(form,key,value,tag='input'){const l=el('label'),input=el(tag);input.name=key;input.value=value||'';input.maxLength=key==='notes'?1000:160;l.append(el('span','',t(key)),input);form.append(l);return input;}
 function summary(root){const title=el('div','overview-title');title.append(icon('network'),el('h2','',t('overview')));root.append(title,el('div','overview-count',state.devices.length),el('div','overview-label',t(state.devices.length===1?'device':'devices')));const stats=el('div','summary-stats');
  for(const key of ['online','offline']){const stat=el('div','summary-stat');stat.append(el('i','status-dot '+key),el('strong','',state.devices.filter(d=>status(d)===key).length),el('span','',t(key)));stats.append(stat);}root.append(stats);const graphic=el('div','selection-visual');graphic.append(icon('select'));root.append(graphic,el('p','hint',t('selectionHint')));
  const row=el('div','kv');row.append(el('span','',t('connections')),el('strong','',state.edges.length));root.append(row);}
 function renderDetails(){const root=$('details');root.replaceChildren();const d=state.devices.find(x=>x.id===selected);if(!d){summary(root);return;}
  const heading=el('div','detail-heading'),symbol=el('span','node-icon');symbol.append(icon(deviceIcon(d)));heading.append(symbol,el('h2','',label(d)));root.append(heading);const badge=el('div','detail-status');badge.append(el('i','status-dot '+status(d)),el('span','',t(status(d))));root.append(badge);
  const kind=d.kind==='Coordinator'?'coordinator':d.kind==='Router'||d.kind==='router'?'router':'endDevice';root.append(el('div','device-kind',t(kind)+(d.room?' · '+d.room:'')));
  for(const [key,value] of [['IP',d.ip],['MAC',d.mac],['IEEE',d.ieee],['manufacturer',d.manufacturer],['model',d.model],['battery',d.battery!=null?d.battery+' %':null],['lqi',d.linkquality],['lastSeen',d.last_seen?date(d.last_seen):null]]){if(value==null||value==='')continue;const row=el('div','kv'+(['IP','MAC','IEEE'].includes(key)?' mono':''));row.append(el('span','',['IP','MAC','IEEE'].includes(key)?key:t(key)),el('strong','',String(value)));root.append(row);}
  root.append(el('h3','',t('connections')));const neighbors=el('div','neighbors');const links=state.edges.filter(e=>e.source===d.id||e.target===d.id);
  for(const e of links){const other=state.devices.find(x=>x.id===(e.source===d.id?e.target:e.source));if(!other)continue;const button=el('button','neighbor'),copy=el('span');copy.append(el('strong','',label(other)),el('small','',t(e.origin==='manual'?'manual':'zigbee')+(e.lqi!=null?' · LQI '+e.lqi:'')));button.append(icon(deviceIcon(other)),copy);button.onclick=()=>{select(other.id);focusNode(other.id);};neighbors.append(button);}
  if(!links.length){const empty=el('div','connection-empty');empty.append(icon('link'),el('p','',t('noConnections')));neighbors.append(empty);}root.append(neighbors);
  if(!state.can_edit){root.append(el('p','hint',t('readOnly')));return;}
  const editor=el('details','editor');editor.open=editOpen;const disclosure=el('summary');disclosure.append(icon('edit'),el('span','',t('edit')));editor.append(disclosure);editor.addEventListener('toggle',()=>{editOpen=editor.open;});
  const form=el('form');form.oninput=()=>{formDirty=true;};field(form,'name',d.label).name='label';field(form,'room',d.room);field(form,'notes',d.notes,'textarea');const parent=field(form,'parent','','select');parent.append(new Option(t('none'),''));for(const other of state.devices.filter(x=>x.id!==d.id))parent.append(new Option(label(other),other.id));parent.value=d.parent||'';
  const save=el('button','btn',t('save'));form.append(save);form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await request('device',{id:d.id,...Object.fromEntries(new FormData(form))});formDirty=false;editOpen=false;await refresh(true);message('saved');}catch(err){message(err.message);}finally{save.disabled=false;}};editor.append(form);root.append(editor);}
 function focusNode(id){const p=positions.get(id);if(p)$('viewport').scrollTo({left:Math.max(0,(p.x+105)*scale-$('viewport').clientWidth/2),top:Math.max(0,(p.y+48)*scale-$('viewport').clientHeight/2)});}
 function nodeDrag(e,node,d){if(e.button!==0||!state.can_edit||(e.pointerType==='touch'&&!e.target.closest('.node-grip')))return;e.stopPropagation();node.setPointerCapture(e.pointerId);const start={x:e.clientX,y:e.clientY,p:{...positions.get(d.id)}};let moved=false;
  node.onpointermove=event=>{const dx=(event.clientX-start.x)/scale,dy=(event.clientY-start.y)/scale;if(Math.abs(dx)+Math.abs(dy)<5&&!moved)return;moved=true;dragging=true;const p={x:Math.max(0,Math.min(9600,start.p.x+dx)),y:Math.max(0,Math.min(9600,start.p.y+dy))};positions.set(d.id,p);node.style.left=p.x+'px';node.style.top=p.y+'px';stage.width=Math.max(stage.width,p.x+240);stage.height=Math.max(stage.height,p.y+120);drawEdges();transform();};
  const finish=async()=>{node.onpointermove=null;node.onpointerup=null;node.onpointercancel=null;dragging=false;if(!moved)return;node.dataset.moved='1';const p=positions.get(d.id);try{await request('device',{id:d.id,...p});d.x=p.x;d.y=p.y;}catch(err){positions.set(d.id,start.p);layout();renderMap();message(err.message);}};node.onpointerup=finish;node.onpointercancel=finish;}
 $('viewport').addEventListener('pointerdown',e=>{if(e.button!==0||e.pointerType!=='mouse'||e.target.closest('button'))return;const v=$('viewport'),start={x:e.clientX,y:e.clientY,left:v.scrollLeft,top:v.scrollTop};v.setPointerCapture(e.pointerId);v.onpointermove=ev=>{dragging=true;v.scrollLeft=start.left+start.x-ev.clientX;v.scrollTop=start.top+start.y-ev.clientY;};v.onpointerup=v.onpointercancel=()=>{dragging=false;v.onpointermove=null;v.onpointerup=null;v.onpointercancel=null;};});
 function zoom(factor){const v=$('viewport'),x=(v.scrollLeft+v.clientWidth/2)/scale,y=(v.scrollTop+v.clientHeight/2)/scale;scale=Math.max(.6,Math.min(1.8,scale*factor));transform();v.scrollLeft=x*scale-v.clientWidth/2;v.scrollTop=y*scale-v.clientHeight/2;}
 $('viewport').addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();zoom(e.deltaY<0?1.1:1/1.1);}},{passive:false});
 $('viewport').addEventListener('keydown',e=>{if(e.target!==$('viewport'))return;if(e.key==='+'||e.key==='='){e.preventDefault();zoom(1.15);}else if(e.key==='-'){e.preventDefault();zoom(1/1.15);}else if(e.key==='0')resetView();});
 $('plus').onclick=()=>zoom(1.15);$('minus').onclick=()=>zoom(1/1.15);$('fit').onclick=resetView;
 $('search').oninput=$('filter').onchange=()=>{layout();renderMap();$('viewport').scrollTo({top:0,left:0});};
 $('language').onchange=()=>{lang=$('language').value;try{localStorage.setItem('runvard_language',lang);}catch(_){}translate();layout();renderMap();renderDetails();renderStatus();};
 function openSettings(zigbee=false){$('networks').value=state.settings.networks.join('\n');const found=state.devices.filter(d=>d.zigbee_url);$('zigbee-url').value=state.settings.zigbee_url||(found.length===1?found[0].zigbee_url:'');$('token').value='';$('clear-token').checked=false;$('settings-message').textContent='';$('settings-dialog').showModal();if(zigbee)$('zigbee-url').focus();}
 function renderStatus(){
  $('scan').textContent=t(state.scanning?'scanning':'scan');$('scan').disabled=!state.can_edit||state.scanning;$('settings').disabled=!state.can_edit;$('mesh').hidden=!state.connected;$('mesh').disabled=!state.can_edit||!state.connected;
  $('connection').textContent=t(state.connected?'connected':state.settings.zigbee_url?'disconnected':'zigbeeMissing');$('integration-dot').className='status-dot '+(state.connected?'online':'unknown');
  $('integration-copy').textContent=state.connected?t('meshHint'):t(state.devices.filter(d=>d.zigbee_url).length===1?'setupFound':'setupCopy');$('setup-zigbee').hidden=state.connected;$('setup-zigbee').disabled=!state.can_edit;
  $('scan-time').textContent=t('scanTime')+': '+date(state.scan_at);$('mesh-time').textContent=t('meshTime')+': '+date(state.mesh_at);
 }
 async function refresh(force=false){if(busy||dragging)return;busy=true;try{const r=await fetch('/api/network-map',{cache:'no-store'});if(r.status===401){location.href='/login';return;}if(!r.ok)throw Error('error');const next=await r.json(),nextSignature=JSON.stringify([next.devices,next.edges,next.can_edit]);state=next;
   if(force||signature!==nextSignature){signature=nextSignature;layout();renderMap();if(!formDirty&&!editOpen)renderDetails();}renderStatus();if(state.error||state.zigbee_error)message('error');
  }catch(_){message('error');}finally{busy=false;}}
 $('scan').onclick=async()=>{try{await request('scan');state.scanning=true;renderStatus();setTimeout(()=>refresh(),500);}catch(err){message(err.message);}};
 $('mesh').onclick=async()=>{try{await request('mesh');message('meshPending');}catch(err){message(err.message);}};
 $('settings').onclick=()=>openSettings();$('setup-zigbee').onclick=()=>openSettings(true);$('close-settings').onclick=()=>$('settings-dialog').close();
 $('settings-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;if(button)button.disabled=true;try{await request('settings',{networks:$('networks').value.split('\n').map(x=>x.trim()).filter(Boolean),zigbee_url:$('zigbee-url').value.trim(),token:$('token').value,clear_token:$('clear-token').checked});$('settings-dialog').close();await refresh(true);message('saved');}catch(err){$('settings-message').textContent=t(err.message);}finally{if(button)button.disabled=false;}};
 let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(!dragging){layout();renderMap();}},120);});
 $('search-icon').append(icon('search'));$('integration-icon').append(icon('zigbee'));
 translate();await refresh(true);setInterval(()=>{if(!document.hidden)refresh();},5000);
})();
