'use strict';
(async()=>{
 const $=id=>document.getElementById(id);
 const catalogs=await (await fetch('/static/network-map-i18n.json')).json();
 let lang=localStorage.getItem('runvard_language')||document.body.dataset.defaultLang||'en';
 if(!catalogs[lang])lang='en';
 const t=key=>catalogs[lang][key]||catalogs[lang].error;
 const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
 let state={devices:[],edges:[],settings:{},can_edit:false},selected='',positions=new Map(),scale=1,pan={x:24,y:40},fitted=false,busy=false;
 let dragging=false,first=true,formDirty=false;
 const label=d=>d.label||d.name||d.id;
 const date=value=>value?new Date(value*1000).toLocaleString(lang):t('never');
 function translate(){
  document.documentElement.lang=lang;document.title=t('title')+' · runvard';$('language').value=lang;
  document.querySelectorAll('[data-t]').forEach(e=>e.textContent=t(e.dataset.t));
  document.querySelectorAll('[data-placeholder]').forEach(e=>{e.placeholder=t(e.dataset.placeholder);e.setAttribute('aria-label',t(e.dataset.placeholder));});
  document.querySelectorAll('[data-aria]').forEach(e=>e.setAttribute('aria-label',t(e.dataset.aria)));
  $('language').setAttribute('aria-label',t('language'));$('filter').setAttribute('aria-label',t('all'));$('viewport').setAttribute('aria-label',t('title'));
 }
 function message(key){$('message').textContent=key?t(key):'';}
 async function request(action,data={}){
  const r=await fetch('/api/network-map/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.status===401){location.href='/login';throw new Error('error');}
  if(!r.ok)throw new Error(r.status===400?'invalid':'error');
  return r.json();
 }
 function visible(){const query=$('search').value.toLocaleLowerCase(lang),filter=$('filter').value;
  return state.devices.filter(d=>(filter==='all'||filter===d.network||filter===d.status)&&[label(d),d.name,d.ip,d.mac,d.room,d.manufacturer,d.model].filter(Boolean).join(' ').toLocaleLowerCase(lang).includes(query));
 }
 function layout(){
  // Deterministic columns; mesh cycles stay graph edges rather than invented parentage.
  let y=32;positions=new Map();
  const occupied=state.devices.filter(d=>d.x!=null&&d.y!=null).map(d=>({x:d.x,y:d.y}));
  for(const network of ['lan','zigbee']){
   const devices=state.devices.filter(d=>d.network===network).sort((a,b)=>label(a).localeCompare(label(b),lang));
   if(!devices.length)continue;
   const linked=new Set(state.edges.flatMap(e=>[e.source,e.target]));
   devices.sort((a,b)=>Number(b.kind==='Coordinator')-Number(a.kind==='Coordinator')||Number(linked.has(b.id))-Number(linked.has(a.id)));
   let slot=0;
   devices.forEach(d=>{
    if(d.x!=null&&d.y!=null){positions.set(d.id,{x:d.x,y:d.y});return;}
    let p;
    do{p={x:24+(slot%4)*220,y:y+30+Math.floor(slot/4)*115};slot++;}
    while(occupied.some(o=>Math.abs(o.x-p.x)<206&&Math.abs(o.y-p.y)<92));
    positions.set(d.id,p);occupied.push(p);
   });
   y+=Math.ceil(devices.length/4)*115+90;
  }
 }
 function transform(){ $('world').style.transform=`translate(${pan.x}px,${pan.y}px) scale(${scale})`;$('zoom').textContent=Math.round(scale*100)+'%'; }
 function fit(){const devices=visible();if(!devices.length)return;
  const points=devices.map(d=>positions.get(d.id));const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y));
  const maxX=Math.max(...points.map(p=>p.x))+190,maxY=Math.max(...points.map(p=>p.y))+80;
  scale=Math.max(.12,Math.min(1.3,($('viewport').clientWidth-40)/(maxX-minX),($('viewport').clientHeight-50)/(maxY-minY)));
  pan={x:20-minX*scale,y:25-minY*scale};transform();
 }
 function drawEdges(){
  const svg=$('edges');svg.replaceChildren();const ids=new Set(visible().map(d=>d.id));
  for(const e of state.edges){if(!ids.has(e.source)||!ids.has(e.target))continue;
   const a=positions.get(e.source),b=positions.get(e.target);if(!a||!b)continue;
   const line=document.createElementNS('http://www.w3.org/2000/svg','path');
   const ax=a.x+95,ay=a.y+38,bx=b.x+95,by=b.y+38,dx=bx-ax,dy=by-ay;
   if(Math.abs(dx)+Math.abs(dy)<1)continue;
   if(Math.abs(dy)<85){
    const lane=Math.min(a.y,b.y)-24;
    line.setAttribute('d',`M ${ax} ${a.y} V ${lane} H ${bx} V ${b.y}`);
   }else{
    const factor=Math.min(95/Math.max(Math.abs(dx),.001),38/Math.max(Math.abs(dy),.001));
    line.setAttribute('d',`M ${ax+dx*factor} ${ay+dy*factor} L ${bx-dx*factor} ${by-dy*factor}`);
   }
   line.setAttribute('fill','none');
   line.classList.add(e.origin==='manual'?'manual':'zigbee');
   if(selected)line.classList.add(e.source===selected||e.target===selected?'selected':'dim');
   const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=t(e.origin==='manual'?'manual':'zigbee')+(e.lqi!=null?' · LQI '+e.lqi:'');line.append(title);svg.append(line);
  }
 }
 function renderMap(){
  const nodes=$('nodes');nodes.replaceChildren();const devices=visible();$('count').textContent=devices.length+' / '+state.devices.length;
  $('empty').textContent=state.devices.length?(devices.length?'':t('noResults')):t('empty');
  for(const d of devices){const p=positions.get(d.id);if(!p)continue;
   const b=el('button','node '+(d.status||'unknown')+(selected===d.id?' selected':''));b.type='button';b.dataset.id=d.id;b.style.left=p.x+'px';b.style.top=p.y+'px';
   b.append(el('span','',d.network==='zigbee'?(d.kind==='Coordinator'?'◉':d.kind==='Router'?'◇':'○'):d.kind==='router'?'⌁':d.kind==='bridge'?'◈':'▣'));
   const text=el('span'),name=el('strong','',label(d));text.append(name,el('small','',d.ip||d.ieee||''));b.append(text);
   b.title=label(d)+' · '+(d.ip||d.ieee||'');b.setAttribute('aria-label',label(d)+' · '+t(d.status||'unknown'));b.setAttribute('aria-pressed',selected===d.id?'true':'false');
   b.onclick=()=>{if(b.dataset.moved==='1'){b.dataset.moved='';return;}select(d.id);};
   b.addEventListener('pointerdown',e=>nodeDrag(e,b,d));nodes.append(b);
  }
  drawEdges();transform();
 }
 function select(id){selected=id;formDirty=false;renderMap();renderDetails();}
 function field(form,key,value,tag='input'){const l=el('label'),s=el('span','',t(key)),input=el(tag);input.name=key;input.value=value||'';input.maxLength=key==='notes'?1000:160;l.append(s,input);form.append(l);return input;}
 function renderDetails(){
  const root=$('details');root.replaceChildren();const d=state.devices.find(x=>x.id===selected);
  if(!d){root.append(el('p','',t('select')));return;}
  root.append(el('h2','',label(d)));const status=el('p'),dot=el('i','status-dot '+d.status);status.append(dot,document.createTextNode(t(d.status||'unknown')));root.append(status);
  const kind=d.kind==='Coordinator'?'coordinator':d.kind==='Router'||d.kind==='router'?'router':'endDevice';
  root.append(el('p','hint',t(kind)+(d.room?' · '+d.room:'')));
  for(const [key,value] of [['IP',d.ip],['MAC',d.mac],['IEEE',d.ieee],['manufacturer',d.manufacturer],['model',d.model],['battery',d.battery!=null?d.battery+' %':null],['lqi',d.linkquality],['lastSeen',d.last_seen?date(d.last_seen):null]]){
   if(value==null||value==='')continue;const row=el('div','kv');row.append(el('span','',['IP','MAC','IEEE'].includes(key)?key:t(key)),el('strong','',String(value)));root.append(row);
  }
  root.append(el('h3','hint',t('neighbors')));const neighbors=el('div','neighbors');
  const links=state.edges.filter(e=>e.source===d.id||e.target===d.id);
  for(const e of links){const other=state.devices.find(x=>x.id===(e.source===d.id?e.target:e.source));if(!other)continue;
   const b=el('button','btn ghost',label(other)+' · '+t(e.origin==='manual'?'manual':'zigbee'));b.onclick=()=>select(other.id);neighbors.append(b);}
  if(!links.length)neighbors.append(el('p','hint',t('unassigned')));root.append(neighbors);
  if(!state.can_edit){root.append(el('p','hint',t('readOnly')));return;}
  const form=el('form');form.oninput=()=>{formDirty=true;};field(form,'name',d.label).name='label';field(form,'room',d.room);field(form,'notes',d.notes,'textarea');
  const parent=field(form,'parent','','select');parent.append(new Option(t('none'),''));
  for(const other of state.devices.filter(x=>x.id!==d.id))parent.append(new Option(label(other),other.id));parent.value=d.parent||'';
  const save=el('button','btn',t('save'));form.append(save);form.onsubmit=async e=>{e.preventDefault();save.disabled=true;
   try{await request('device',{id:d.id,...Object.fromEntries(new FormData(form))});formDirty=false;await refresh();message('saved');}catch(err){message(err.message);}finally{save.disabled=false;}};root.append(form);
 }
 function nodeDrag(e,node,d){
  if(e.button!==0||!state.can_edit)return;e.stopPropagation();node.setPointerCapture(e.pointerId);
  const start={x:e.clientX,y:e.clientY,p:{...positions.get(d.id)}};let moved=false;
  node.onpointermove=event=>{const dx=(event.clientX-start.x)/scale,dy=(event.clientY-start.y)/scale;if(Math.abs(dx)+Math.abs(dy)<5&&!moved)return;
   moved=true;dragging=true;const p={x:Math.max(0,Math.min(9600,start.p.x+dx)),y:Math.max(0,Math.min(9600,start.p.y+dy))};positions.set(d.id,p);node.style.left=p.x+'px';node.style.top=p.y+'px';drawEdges();};
  const finish=async()=>{node.onpointermove=null;node.onpointerup=null;node.onpointercancel=null;dragging=false;if(!moved)return;node.dataset.moved='1';const p=positions.get(d.id);
   try{await request('device',{id:d.id,...p});d.x=p.x;d.y=p.y;}catch(err){positions.set(d.id,start.p);renderMap();message(err.message);}};
  node.onpointerup=finish;node.onpointercancel=finish;
 }
 $('viewport').addEventListener('pointerdown',e=>{if(e.button!==0)return;
  const start={x:e.clientX,y:e.clientY,pan:{...pan}};const v=$('viewport');v.setPointerCapture(e.pointerId);
  v.onpointermove=ev=>{dragging=true;pan={x:start.pan.x+ev.clientX-start.x,y:start.pan.y+ev.clientY-start.y};transform();};
  v.onpointerup=v.onpointercancel=()=>{dragging=false;v.onpointermove=null;v.onpointerup=null;v.onpointercancel=null;};
 });
 function zoom(factor){const center={x:$('viewport').clientWidth/2,y:$('viewport').clientHeight/2},next=Math.max(.12,Math.min(2,scale*factor));pan={x:center.x-(center.x-pan.x)*next/scale,y:center.y-(center.y-pan.y)*next/scale};scale=next;transform();}
 $('viewport').addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?1.1:1/1.1);},{passive:false});
 $('viewport').addEventListener('keydown',e=>{if(e.target!==$('viewport'))return;if(e.key==='+'||e.key==='=')zoom(1.2);else if(e.key==='-')zoom(1/1.2);else if(e.key==='0')fit();else if(e.key.startsWith('Arrow')){e.preventDefault();pan.x+=e.key==='ArrowLeft'?30:e.key==='ArrowRight'?-30:0;pan.y+=e.key==='ArrowUp'?30:e.key==='ArrowDown'?-30:0;transform();}});
 $('plus').onclick=()=>zoom(1.2);$('minus').onclick=()=>zoom(1/1.2);$('fit').onclick=fit;
 $('search').oninput=$('filter').onchange=()=>{renderMap();fit();};
 $('language').onchange=()=>{lang=$('language').value;localStorage.setItem('runvard_language',lang);translate();renderMap();renderDetails();renderStatus();};
 function renderStatus(){
  $('scan').textContent=t(state.scanning?'scanning':'scan');$('scan').disabled=!state.can_edit||state.scanning;
  $('settings').disabled=!state.can_edit;$('mesh').disabled=!state.can_edit||!state.connected;
  $('connection').textContent=state.settings.zigbee_url?t(state.connected?'connected':'disconnected'):'';
  $('scan-time').textContent=t('scanTime')+': '+date(state.scan_at);$('mesh-time').textContent=t('meshTime')+': '+date(state.mesh_at);
 }
 async function refresh(){if(busy||dragging)return;busy=true;
  try{const r=await fetch('/api/network-map',{cache:'no-store'});if(r.status===401){location.href='/login';return;}if(!r.ok)throw new Error('error');
   const next=await r.json();state=next;layout();renderMap();renderStatus();
   if(first){renderDetails();first=false;}else if(!formDirty&&!$('details').contains(document.activeElement))renderDetails();
   if(!fitted&&state.devices.length){fit();fitted=true;}
   if(state.error||state.zigbee_error)message('error');
  }catch(err){message('error');}finally{busy=false;}
 }
 $('scan').onclick=async()=>{try{await request('scan');state.scanning=true;renderStatus();setTimeout(refresh,500);}catch(err){message(err.message);}};
 $('mesh').onclick=async()=>{try{await request('mesh');message('meshPending');}catch(err){message(err.message);}};
 $('settings').onclick=()=>{$('networks').value=state.settings.networks.join('\n');$('zigbee-url').value=state.settings.zigbee_url;$('token').value='';$('clear-token').checked=false;$('settings-message').textContent='';$('settings-dialog').showModal();};
 $('close-settings').onclick=()=>$('settings-dialog').close();
 $('settings-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;if(button)button.disabled=true;
  try{await request('settings',{networks:$('networks').value.split('\n').map(x=>x.trim()).filter(Boolean),zigbee_url:$('zigbee-url').value.trim(),token:$('token').value,clear_token:$('clear-token').checked});$('settings-dialog').close();await refresh();message('saved');}
  catch(err){$('settings-message').textContent=t(err.message);}finally{if(button)button.disabled=false;}};
 window.addEventListener('resize',()=>{if(!dragging)fit();});
 translate();await refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);
})();
