function modernDockerToolIcon(kind){
  const paths={
    compose:'<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="8.5" y="13" width="7" height="7" rx="2"/>',
    image:'<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9"/>',
    volume:'<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
  };
  return `<span class="modern-docker-tool-mark" aria-hidden="true"><svg viewBox="0 0 24 24">${paths[kind]||paths.compose}</svg></span>`;
}
function modernComposeProjectRows(projects){
  const running=(projects||[]).filter(project=>project.running).length;
  return `<div class="nx-summary-grid modern-docker-tool-summary">${summaryCard('Compose projects',(projects||[]).length,'Total')}${summaryCard('Running',running,'Active now')}</div><div class="modern-docker-tool-list">${(projects||[]).map(project=>{
    const pending=composePendingActions.get(project.name),nameArg=esc(JSON.stringify(project.name)),port=Number(project.port||0);
    const primary=project.running&&port?`<button class="btn sm" onclick="composeOpen(${nameArg})">${esc(uiText('Open'))}</button>`:`<button class="btn sm" onclick="composeAct(${nameArg},'${project.running?'restart':'up'}')" ${pending?'disabled':''}>${esc(uiText(project.running?'Restart':'Start'))}</button>`;
    return `<article class="modern-docker-tool-row">${modernDockerToolIcon('compose')}<div class="modern-docker-tool-copy"><strong>${esc(project.name)}</strong><small class="mono">docker-compose.yml${port?' · :'+port:''}</small></div>${pending?statusBadge(appPendingLabel(pending),'warn'):statusBadge(project.running?'Running':'Stopped',project.running?'good':'neutral')}<div class="modern-docker-tool-actions">${primary}<details class="docker-card-menu"><summary title="${esc(uiText('Actions'))}" aria-label="${esc(uiText('Actions'))}"><span class="docker-card-menu-dots" aria-hidden="true"><i></i><i></i><i></i></span></summary><div class="docker-card-menu-list">${project.running?`<button onclick="composeAct(${nameArg},'stop')">${esc(uiText('Stop'))}</button>`:''}<button onclick="composeEdit(${nameArg})">${esc(uiText('Edit'))}</button><button onclick="composeAddToDashboard(${nameArg},${port})">Dashboard</button><button class="danger" onclick="composeDelete(${nameArg})">${esc(uiText('Delete'))}</button></div></details></div></article>`;
  }).join('')||emptyState('No projects')}</div>`;
}
function modernDockerImageRows(images){
  return `<div class="modern-docker-tool-list">${(images||[]).map(image=>{
    const label=(image.tags||[]).join(', ')||String(image.id||'').slice(0,12);
    return `<article class="modern-docker-tool-row">${modernDockerToolIcon('image')}<div class="modern-docker-tool-copy"><strong class="mono" title="${esc(label)}">${esc(label)}</strong><small>${fmtBytes(image.size)}</small></div><div class="modern-docker-tool-actions"><details class="docker-card-menu"><summary title="${esc(uiText('Actions'))}" aria-label="${esc(uiText('Actions'))}"><span class="docker-card-menu-dots" aria-hidden="true"><i></i><i></i><i></i></span></summary><div class="docker-card-menu-list"><button class="danger" onclick="imageDel(${esc(JSON.stringify(image.id))},${esc(JSON.stringify(label))})">${esc(uiText('Delete'))}</button></div></details></div></article>`;
  }).join('')||emptyState('No images')}</div>`;
}
function modernDockerVolumeRows(volumes){
  return `<div class="modern-docker-tool-list">${(volumes||[]).map(volume=>`<article class="modern-docker-tool-row">${modernDockerToolIcon('volume')}<div class="modern-docker-tool-copy"><strong title="${esc(volume.name)}">${esc(volume.name)}</strong><small><span>${esc(volume.driver||'local')}</span><span class="mono" title="${esc(volume.mountpoint)}">${esc(volume.mountpoint)}</span></small></div><div class="modern-docker-tool-actions"><details class="docker-card-menu"><summary title="${esc(uiText('Actions'))}" aria-label="${esc(uiText('Actions'))}"><span class="docker-card-menu-dots" aria-hidden="true"><i></i><i></i><i></i></span></summary><div class="docker-card-menu-list"><button class="danger" onclick="volumeDel(${esc(JSON.stringify(volume.name))})">${esc(uiText('Delete'))}</button></div></details></div></article>`).join('')||emptyState('No volumes')}</div>`;
}
async function renderDockerModern(body,tab){
  const available=await api('/docker/available');if(!available.available){body.innerHTML=viewIntro('Containers','Docker is not available on this server.','')+emptyState('Docker unavailable');return;}
  if(tab==='Compose'){
    const projects=await api('/docker/compose');body.innerHTML=viewIntro('Compose projects','Advanced multi-container projects. Editing Compose files can break services.',`<button class="btn" onclick="composeForm()">${esc(uiText('Create project'))}</button>`)+modernComposeProjectRows(projects);translateRenderedText(body);return;
  }
  if(tab==='Images'){
    const images=await api('/docker/images');body.innerHTML=viewIntro('Images','Images are templates used to create containers.',`<button class="btn" onclick="pullForm()">${esc(uiText('Pull image'))}</button>`)+modernDockerImageRows(images);translateRenderedText(body);return;
  }
  if(tab==='Volumes'){
    const volumes=await api('/docker/volumes');body.innerHTML=viewIntro('Volumes','Volumes hold persistent container data. Delete only when you know the data is no longer needed.','')+modernDockerVolumeRows(volumes);translateRenderedText(body);return;
  }
  const [containers,catalogData]=await Promise.all([api('/docker/containers'),api('/apps/catalog').catch(()=>({apps:[]}))]);
  const groups=RunvardDockerModern.groupContainers(containers);
  _modernDockerGroups=new Map(groups.map(group=>[group.key,group]));
  const counts=groups.reduce((result,group)=>{result[RunvardDockerModern.groupState(group.containers)]++;return result;},{running:0,partial:0,error:0,stopped:0});
  body.innerHTML=viewIntro('Container services','Start, stop and inspect services running in Docker. Advanced image and volume tools are in the other tabs.',`<button class="btn" onclick="dockerCreateForm()">${esc(uiText('Create container'))}</button>`)
    +`<div class="nx-summary-grid modern-docker-summary">${summaryCard('Apps',groups.length,'Total')}${summaryCard('Running',counts.running,'Active now')}${summaryCard('Attention',counts.partial+counts.error,counts.partial+counts.error?appUi('partlyRunning'):'OK')}</div><div class="modern-docker-app-grid">${groups.map((group,index)=>dockerModernAppCard(group,catalogData.apps||[],index)).join('')||emptyState('No containers')}</div>`;
  translateRenderedText(body);startDockerCardStats(body);
}

async function renderDocker(body,tab){
  stopDockerCardStats();
  if(isModernUi())return renderDockerModern(body,tab);
  const av=await api('/docker/available');if(!av.available){body.innerHTML=viewIntro('Containers','Docker is not available on this server.','')+emptyState('Docker unavailable');return;}
  if(tab==='Container'||!tab){
    const c=await api('/docker/containers');
    body.innerHTML=viewIntro('Container services','Start, stop and inspect services running in Docker. Advanced image and volume tools are in the other tabs.',`<button class="btn" onclick="dockerCreateForm()">Create container</button>`)
      +`<div class="nx-summary-grid">${summaryCard('Apps',dockerAppGroupCount(c),'Total')}${summaryCard('Running',dockerAppGroupCount(c,true),'Active now')}</div>
      <div class="docker-service-grid">${c.map(dockerServiceCard).join('')||emptyState('No containers')}</div>`;
    translateRenderedText(body);
    startDockerCardStats(body);
    return;
  }
  return renderDockerAdvanced(body,tab);
}
async function renderDockerAdvanced(body,tab){
  body.innerHTML=viewIntro(tab,'Advanced Docker tools for users who know images, volumes and Compose files.','')+'<div class="loading">Loading…</div>';
  const oldTab=tab;
  if(oldTab==='Compose'){
    const p=await api('/docker/compose');
    body.innerHTML=viewIntro('Compose projects','Advanced multi-container projects. Editing Compose files can break services.',`<button class="btn" onclick="composeForm()">Create project</button>`)
      +composeProjectCards(p);
  } else if(oldTab==='Images'){
    const im=await api('/docker/images');
    body.innerHTML=viewIntro('Images','Images are templates used to create containers.',`<button class="btn" onclick="pullForm()">Pull image</button>`)
      +simpleTable(['Tags','Size',''],im.map(x=>`<tr><td class="mono">${esc(x.tags.join(', ')||x.id.slice(0,12))}</td><td>${fmtBytes(x.size)}</td><td><button class="btn danger sm" onclick="imageDel('${x.id}','${esc(x.tags[0]||x.id)}')">Delete</button></td></tr>`).join(''));
  } else {
    const v=await api('/docker/volumes');
    body.innerHTML=viewIntro('Volumes','Volumes hold persistent container data. Delete only when you know the data is no longer needed.','')
      +simpleTable(['Name','Driver','Mount point',''],v.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.driver)}</td><td class="mono">${esc(x.mountpoint)}</td><td><button class="btn danger sm" onclick="volumeDel('${esc(x.name)}')">Delete</button></td></tr>`).join(''));
  }
}
