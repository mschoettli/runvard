'use strict';
let locale='de',scenario='system',section='overview',updated=false,deleted=false,formBaseline='',chosenFolder='/mnt/nas',confirmKind='delete',toastTimer;
const $=s=>document.querySelector(s),t=k=>previewText(locale,k),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths={server:'M4 3h16v7H4z M4 14h16v7H4z M7 6h.01 M7 17h.01 M16 6h2 M16 17h2',close:'m6 6 12 12 M18 6 6 18',back:'m14 5-7 7 7 7',chevron:'m9 5 7 7-7 7',check:'m5 12 4 4 10-10',folder:'M3 7V4h6l3 3h9v13H3z',network:'M12 3v6 M4 15v5h5v-5H4z M15 15v5h5v-5h-5z M9 9h6v5H9z M6 15v-3h3 M18 15v-3h-3',users:'M16 21v-3c0-3-8-3-8 0v3 M9 7a3 3 0 1 0 6 0 3 3 0 1 0-6 0 M4 18v-2c0-2 1-3 3-3 M17 13c2 0 3 1 3 3v2',services:'m12 3 9 5-9 5-9-5 9-5 M3 12l9 5 9-5 M3 16l9 5 9-5',download:'M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5',clock:'M12 8v5l3 2 M21 12a9 9 0 1 0-18 0 9 9 0 1 0 18 0',box:'m12 3 9 5v9l-9 5-9-5V8l9-5 M3 8l9 5 9-5 M12 13v9',alert:'m12 3 10 18H2L12 3 M12 9v5 M12 17h.01',search:'M21 21l-6-6 M17 10a7 7 0 1 0-14 0 7 7 0 1 0 14 0'};
const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]||paths.server}"/></svg>`;
function labels(root=document){root.querySelectorAll('[data-text]').forEach(e=>e.textContent=t(e.dataset.text));root.querySelectorAll('[data-label]').forEach(e=>e.setAttribute('aria-label',t(e.dataset.label)));}
function toast(key){const e=$('#toast');clearTimeout(toastTimer);(document.querySelector('dialog[open]:last-of-type')||$('#workspace[open]')||document.body).append(e);e.textContent=t(key);e.hidden=false;toastTimer=setTimeout(()=>e.hidden=true,3500);}
function closeAll(){document.querySelectorAll('dialog[open]').forEach(d=>d.close());$('#toast').hidden=true;}
function home(){ $('#home').innerHTML=`<header class="home-top"><strong>RUNVARD</strong><small>homeserver</small></header><div class="home-content"><h2 class="section-heading">${t('home')}</h2><div class="home-grid"><button class="home-tile" data-action="system">${icon('server')}<span><strong>${t('system')}</strong><small>${t('healthy')}</small></span></button><button class="home-tile" data-action="form">${icon('folder')}<span><strong>${t('smb')}</strong><small>${t('shares')}</small></span></button><button class="home-tile" data-action="docker">${icon('box')}<span><strong>Docker</strong><small>${t('containers')}</small></span></button></div><p class="home-note">${t('offline')}</p></div>`; }
const sections=['overview','updates','users','network','services','shares'];
function workspace(mode='system'){
 if($('#workspace').open)$('#workspace').close();
 $('#workspace-title').textContent=mode==='system'?t('system'):'Docker';$('#workspace-icon').innerHTML=icon(mode==='system'?'server':'box');
 $('#workspace').dataset.mode=mode;
 $('#workspace-nav').innerHTML=mode==='system'?`<div class="section-select-wrap"><label class="sr" for="section">${t('scenarios')}</label><select class="section-select" id="section">${sections.map(k=>`<option value="${k}" ${section===k?'selected':''}>${t(k)}</option>`).join('')}</select></div>`:'';
 labels();renderSection();$('#workspace').showModal();
}
function row(key,ico,extra=''){return `<button class="row" data-section="${key}">${icon(ico)}<span>${t(key)}</span>${extra}<span class="chevron">${icon('chevron')}</span></button>`;}
function renderSection(){
 const body=$('#workspace-body');body.scrollTop=0;
 if($('#workspace').dataset.mode==='docker')return renderContainers();
 if(section==='overview'){
 body.innerHTML=`<div class="health"><span class="check">${icon('check')}</span><div><h2>${t('healthy')}</h2><p>${t('healthyBody')}</p></div></div><div class="metrics"><div class="metric"><span>CPU</span><strong>12 %</strong></div><div class="metric"><span>RAM</span><strong>${new Intl.NumberFormat(locale).format(4.2)} GB</strong></div><div class="metric"><span>${t('storage')}</span><strong>62 %</strong></div><div class="metric"><span>${t('uptime')}</span><strong>${t('days')}</strong></div></div><h2 class="section-heading">${t('attention')}</h2><div class="list"><button class="row" data-section="updates">${icon('download')}<span>${t(updated?'updated':'updateCount')}</span><span class="chevron">${icon('chevron')}</span></button><div class="row">${icon('clock')}<span>${t('backup')}</span><span class="end">${t('today')}</span></div></div><h2 class="section-heading">${t('manage')}</h2><div class="list">${row('users','users')+row('network','network')+row('services','services')+row('shares','folder')}</div>`;
 }else if(section==='updates'){
 body.innerHTML=`<h2 class="section-heading" style="margin-top:0">${t(updated?'updated':'updateCount')}</h2>${updated?`<div class="health"><span class="check">${icon('check')}</span><p>${t('updated')}</p></div>`:`<div class="list">${[['openssl','3.0.15'],['curl','8.10.1'],['linux-image','6.8.0']].map(([name,version])=>`<div class="row">${icon('download')}<strong>${name}</strong><span class="end">${version}</span></div>`).join('')}</div><button class="button primary full" data-action="install">${t('install')}</button>`}`;
 }else if(section==='users'){
 body.innerHTML=`<h2 class="section-heading" style="margin-top:0">${t('users')}</h2><div class="list"><div class="row">${icon('users')}<div><strong>admin</strong><small>${t('admin')}</small></div></div><div class="row">${icon('users')}<div><strong>monitor</strong><small>${t('readonly')}</small></div></div></div>`;
 }else if(section==='network'){
 body.innerHTML=`<h2 class="section-heading" style="margin-top:0">${t('network')}</h2><div class="list"><div class="row">${icon('network')}<div><strong>eth0</strong><small>192.168.1.10</small></div><span class="end pill">${t('connected')}</span></div><div class="row"><strong>DNS</strong><span class="end">192.168.1.1</span></div><div class="row"><strong>IPv6</strong><span class="end">fe80::1</span></div></div>`;
 }else if(section==='services'){
 body.innerHTML=`<label class="sr" for="service-search">${t('search')}</label><input class="search" id="service-search" placeholder="${t('search')}" type="search"><div class="list service-row" id="service-list">${['nginx','docker','ssh'].map(name=>`<div class="row" data-service="${name}"><div><strong>${name}</strong><small>${t('running')}</small></div><button class="button secondary" data-action="restart">${t('restart')}</button></div>`).join('')}</div><p id="no-results" class="empty" hidden>${t('noResults')}</p>`;
 }else{
 body.innerHTML=`<h2 class="section-heading" style="margin-top:0">${t('shares')}</h2><div class="list"><div class="row">${icon('folder')}<div><strong>media</strong><small>/mnt/media</small></div><span class="end pill">${t('connected')}</span></div></div><button class="button primary full" data-action="form">${t('smb')}</button>`;
 }
}
function renderContainers(){ $('#workspace-body').innerHTML=`<p class="muted" style="margin-bottom:16px">${t('containers')}</p>${['nextcloud',...(!deleted?['nextcloud-test']:[]),'portainer'].map(name=>`<article class="container-card"><div class="container-top"><span class="status-dot"></span><strong>${name}</strong></div><div class="container-meta">${t('running')}</div>${name==='nextcloud-test'?`<p>${t('containerHint')}</p><button class="button danger-quiet" data-action="delete">${t('delete')}</button>`:''}</article>`).join('')}${deleted?`<button class="button secondary full" data-action="restore">${t('restore')}</button>`:''}`;}
function formState(){return JSON.stringify([...new FormData($('#smb-form')).entries()]);}
function openForm(){
 $('#form-title').textContent=t('smb');
 $('#fields').innerHTML=`<p class="muted">${t('smbBody')}</p>${[['server','server','192.168.1.10'],['share','shareName','media'],['mount','mount','/mnt/nas'],['username','username','guest'],['password','password','']].map(([name,key,value])=>`<div class="field"><label for="field-${name}">${t(key)}</label>${name==='mount'?'<div class="input-row">':''}<input id="field-${name}" name="${name}" type="${name==='password'?'password':'text'}" value="${value}" ${['server','share','mount'].includes(name)?'required':''} ${name==='mount'?'readonly':''} ${name==='password'?'autocomplete="off"':''} spellcheck="false">${name==='mount'?`<button class="icon-button" type="button" data-action="folder" aria-label="${t('chooseFolder')}">${icon('folder')}</button></div><p class="hint">${t('mountHint')}</p>`:''}</div>`).join('')}<label class="password-toggle"><input type="checkbox" id="show-password">${t('showPassword')}</label>`;
 labels();formBaseline=formState();$('#form-dialog').showModal();
 // Focus the heading instead of opening the mobile keyboard on entry.
 $('#form-title').tabIndex=-1;$('#form-title').focus();
}
function requestCloseForm(){if(formState()!==formBaseline)openConfirm('discard');else $('#form-dialog').close();}
function openFolder(){
 chosenFolder=$('#field-mount').value;$('#folder-title').textContent=t('chooseFolder');
 $('#folder-content').innerHTML=`<h2 class="section-heading" style="margin-top:0">${t('locations')}</h2><div class="list">${['/mnt/nas','/mnt/media','/srv/shares','/srv/backups'].map(path=>`<label class="folder-option">${icon('folder')}<span>${path}</span><input type="radio" name="folder" value="${path}" ${chosenFolder===path?'checked':''}></label>`).join('')}</div>`;
 labels();$('#folder-dialog').showModal();
}
function openConfirm(kind){
 confirmKind=kind;$('#confirm-title').textContent=t(kind==='delete'?'deleteTitle':'discardTitle');$('#confirm-copy').textContent=t(kind==='delete'?'deleteBody':'discardBody');$('#warning-icon').innerHTML=icon('alert');$('#delete-check-row').hidden=kind!=='delete';$('#delete-check').checked=false;$('#confirm-go').textContent=t(kind==='delete'?'delete':'discard');$('#confirm-cancel').textContent=t(kind==='delete'?'cancel':'keep');$('#confirm-go').disabled=kind==='delete';labels();$('#confirm-dialog').showModal();$('#confirm-cancel').focus();
}
function runScenario(value,reset=false){closeAll();scenario=value;if(reset){updated=false;deleted=false;section='overview';}home();if(value==='form')openForm();else if(value==='confirmation'){workspace('docker');if(!deleted)openConfirm('delete');}else{section='overview';workspace();}}
document.addEventListener('click',event=>{
 const b=event.target.closest('[data-action],[data-section]');if(!b)return;
 if(b.dataset.section){section=b.dataset.section;$('#section').value=section;renderSection();return;}
 switch(b.dataset.action){
 case 'system':section='overview';workspace();break;
 case 'form':openForm();break;
 case 'docker':workspace('docker');break;
 case 'close-workspace':$('#workspace').close();break;
 case 'close-form':requestCloseForm();break;
 case 'folder':openFolder();break;
 case 'close-folder':$('#folder-dialog').close();break;
 case 'select-folder':$('#field-mount').value=chosenFolder;$('#folder-dialog').close();break;
 case 'delete':openConfirm('delete');break;
 case 'restore':deleted=false;renderContainers();break;
 case 'install':updated=true;renderSection();toast('updateDone');break;
 case 'restart':toast('serviceDone');break;
 }
});
document.addEventListener('change',e=>{if(e.target.id==='section'){section=e.target.value;renderSection();}if(e.target.name==='folder')chosenFolder=e.target.value;if(e.target.id==='delete-check')$('#confirm-go').disabled=!e.target.checked;if(e.target.id==='show-password')$('#field-password').type=e.target.checked?'text':'password';});
document.addEventListener('input',e=>{if(e.target.id==='service-search'){let n=0;document.querySelectorAll('[data-service]').forEach(row=>{row.hidden=!row.dataset.service.includes(e.target.value.toLowerCase().trim());if(!row.hidden)n++;});$('#no-results').hidden=n>0;}});
$('#smb-form').onsubmit=e=>{e.preventDefault();$('#form-dialog').close();toast('connectDone');};
$('#confirm-cancel').onclick=()=>$('#confirm-dialog').close();
$('#confirm-go').onclick=()=>{if(confirmKind==='delete'&&!$('#delete-check').checked)return;$('#confirm-dialog').close();if(confirmKind==='discard')$('#form-dialog').close();else{deleted=true;renderContainers();toast('deleteDone');}};
$('#form-dialog').addEventListener('cancel',e=>{e.preventDefault();requestCloseForm();});
// Native dialogs isolate focus, Escape and the backdrop to the topmost dialog.
window.addEventListener('message',e=>{if(e.source!==parent||e.data?.type!=='runvard-preview')return;const {action,value}=e.data;if(action==='locale'&&PREVIEW_LOCALES.includes(value)){locale=value;document.documentElement.lang=locale;document.title=t('title');runScenario(scenario);}else if(action==='scenario'&&['system','form','confirmation'].includes(value))runScenario(value);else if(action==='reset')runScenario(scenario,true);});
document.querySelectorAll('[data-action="close-workspace"],[data-label="close"]').forEach(b=>b.innerHTML=icon('close'));document.querySelector('[data-label="back"]').innerHTML=icon('back');
home();labels();if(parent!==window)parent.postMessage({type:'runvard-preview-ready'},'*');else runScenario('system');
