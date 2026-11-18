/* One Compose document, edited through the form and YAML at the same time. */
(function(global){
  'use strict';
  let state=null;
  const text=key=>COMPOSE_EDITOR_I18N[currentLanguage()][key];
  const clone=value=>JSON.parse(JSON.stringify(value));
  const get=(document,path)=>path.reduce((value,key)=>value?.[key],document);
  function fields(value,path=[],result=[]){
    if(value!==null&&typeof value==='object'&&Object.keys(value).length){
      Object.entries(value).forEach(([key,item])=>fields(item,[...path,Array.isArray(value)?Number(key):key],result));
    }else result.push({path,value});
    return result;
  }
  function setValue(document,path,value){
    let target=document;
    path.slice(0,-1).forEach(key=>{target=target[key];});
    const key=path[path.length-1],previous=target[key];
    if(typeof previous==='number'){
      if(String(value).trim()===''||!Number.isFinite(Number(value)))throw new Error(text('formError'));
      value=Number(value);
    }else if(typeof previous==='boolean')value=!!value;
    else if(previous===null||typeof previous==='object')value=JSON.parse(value);
    target[key]=value;
  }
  function active(){return state.sources[state.index];}
  function control(source,path,label,options={}){
    const value=options.value!==undefined?options.value:get(source.document,path);
    const index=source.fields.push({path,value,apply:options.apply})-1;
    const id='compose-complete-field-'+index;
    const complex=value===null||typeof value==='object';
    const display=complex?JSON.stringify(value):String(value??'');
    const attr=`id="${id}" data-compose-field="${index}"`;
    let input;
    if(options.choices){
      const choices=[...new Set([display,...options.choices])];
      input=`<select ${attr}>${choices.map(choice=>`<option value="${esc(choice)}"${choice===display?' selected':''}>${esc(choice)}</option>`).join('')}</select>`;
    }else if(typeof value==='boolean')input=`<input ${attr} type="checkbox" ${value?'checked':''}>`;
    else if(complex||display.includes('\n'))input=`<textarea ${attr} rows="${Math.min(8,Math.max(2,display.split('\n').length))}" spellcheck="false">${esc(display)}</textarea>`;
    else input=`<input ${attr} type="text" value="${esc(display)}"${options.numeric?' inputmode="numeric"':''} autocomplete="off" spellcheck="false">`;
    return `<div class="compose-field${typeof value==='boolean'?' compose-linked-checkbox':''}"><label for="${id}">${esc(label)}</label>${input}</div>`;
  }
  function block(label,key,body){
    return `<section class="compose-block"><div class="compose-key"><h4>${esc(label)}</h4><code>${esc(key)}</code></div><div class="compose-block-body">${body}</div></section>`;
  }
  function action(source,label,callback,remove=false){
    const index=source.actions.push(callback)-1;
    return `<button id="compose-complete-action-${index}" type="button" class="btn ghost compose-linked-${remove?'remove':'add'}" data-compose-action="${index}" aria-label="${esc(label)}">${remove?'×':'+ '+esc(label)}</button>`;
  }
  function portParts(value){
    if(typeof value!=='string'&&typeof value!=='number')return null;
    const match=String(value).match(/^(?:(.*):)?([^:]+):([^:/]+)(?:\/(tcp|udp|sctp))?$/);
    return match?{prefix:match[1]?match[1]+':':'',host:match[2],target:match[3],protocol:match[4]||'tcp'}:null;
  }
  function mountParts(value){
    if(typeof value!=='string')return null;
    const parts=value.split(':');
    return parts.length>=2&&parts.length<=3?{source:parts[0],target:parts[1],mode:parts[2]||''}:null;
  }
  function formHtml(source){
    source.fields=[];source.actions=[];
    const document=source.document;
    const advanced=[];
    let html='';
    for(const [service,config] of Object.entries(document.services||{})){
      if(!config||typeof config!=='object'||Array.isArray(config)){
        advanced.push(...fields(config,['services',service]));continue;
      }
      const path=['services',service];
      let actualService=service;
      const renameService=value=>{
        if(!/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(value)||value!==actualService&&Object.hasOwn(source.document.services,value))throw new Error(text('serviceExists'));
        const old=actualService;
        if(value!==old){source.document.services[value]=source.document.services[old];delete source.document.services[old];actualService=value;path[1]=value;}
        for(const field of source.fields)if(field.path[0]==='services'&&field.path[1]===old)field.path[1]=value;
      };
      const assign=(key,value)=>{config[key]=value;};
      html+=`<div class="compose-linked-service"><h3>${esc(service)}</h3>`;
      html+=block(text('container'),'services',`<div class="compose-linked-fields">${control(source,[...path,'image'],text('image'),{value:config.image??'',apply:value=>assign('image',value)})}${control(source,[...path],text('serviceName'),{value:service,apply:renameService})}${control(source,[...path,'container_name'],text('containerName'),{value:config.container_name??'',apply:value=>assign('container_name',value)})}${control(source,[...path,'restart'],text('restart'),{value:config.restart??'',choices:['unless-stopped','always','on-failure','no'],apply:value=>assign('restart',value)})}</div>`);
      const ports=Array.isArray(config.ports)?config.ports:[];
      let portRows=ports.map((port,i)=>{
        const p=[...path,'ports',i],parts=portParts(port);
        const remove=action(source,text('remove'),()=>ports.splice(i,1),true);
        if(parts){
          const update=(key,value)=>{
            const next={...portParts(ports[i]),[key]:value};
            ports[i]=next.prefix+next.host+':'+next.target+(next.protocol==='tcp'?'':'/'+next.protocol);
          };
          return `<div class="compose-linked-row compose-linked-port">${control(source,p,text('hostPort'),{value:parts.host,numeric:true,apply:value=>update('host',value)})}<span class="compose-linked-arrow" aria-hidden="true">→</span>${control(source,p,text('containerPort'),{value:parts.target,numeric:true,apply:value=>update('target',value)})}${control(source,p,text('protocol'),{value:parts.protocol,choices:['tcp','udp','sctp'],apply:value=>update('protocol',value)})}${remove}</div>`;
        }
        return `<div class="compose-linked-row">${fields(port,p).map(field=>control(source,field.path,field.path.slice(path.length+2).join(' › ')||text('containerPort'))).join('')}${remove}</div>`;
      }).join('');
      portRows+=action(source,text('addPort'),()=>{if(!Array.isArray(config.ports))config.ports=[];config.ports.push('8080:80');});
      html+=block(text('network'),'ports',portRows);
      const volumes=Array.isArray(config.volumes)?config.volumes:[];
      let volumeRows=volumes.map((volume,i)=>{
        const p=[...path,'volumes',i],parts=mountParts(volume);
        const remove=action(source,text('remove'),()=>volumes.splice(i,1),true);
        if(parts){
          const update=(key,value)=>{
            const next={...mountParts(volumes[i]),[key]:value};
            volumes[i]=next.source+':'+next.target+(next.mode?':'+next.mode:'');
          };
          return `<div class="compose-linked-row compose-linked-mount">${control(source,p,text('hostPath'),{value:parts.source,apply:value=>update('source',value)})}${control(source,p,text('containerPath'),{value:parts.target,apply:value=>update('target',value)})}${control(source,p,text('readOnly'),{value:parts.mode.split(',').includes('ro'),apply:value=>{const modes=mountParts(volumes[i]).mode.split(',').filter(mode=>mode&&mode!=='ro'&&mode!=='rw');if(value)modes.unshift('ro');update('mode',modes.join(','));}})}${remove}</div>`;
        }
        return `<div class="compose-linked-row">${fields(volume,p).map(field=>control(source,field.path,field.path.slice(path.length+2).join(' › ')||text('storage'))).join('')}${remove}</div>`;
      }).join('');
      volumeRows+=action(source,text('addMount'),()=>{if(!Array.isArray(config.volumes))config.volumes=[];config.volumes.push('/mnt/media:/media:ro');});
      html+=block(text('storage'),'volumes',volumeRows);
      const env=config.environment;
      let envRows='';
      if(env&&typeof env==='object'){
        envRows=Object.entries(env).map(([key,value])=>{
          const p=[...path,'environment',Array.isArray(env)?Number(key):key];
          // List-form entries remain strings, including entries without '='.
          if(Array.isArray(env))return `<div class="compose-linked-row compose-linked-env">${control(source,p,text('variable'))}${action(source,text('remove'),()=>env.splice(Number(key),1),true)}</div>`;
          let actualKey=key;
          const rename=value=>{
            if(!value||value!==actualKey&&Object.hasOwn(env,value))throw new Error(text('formError'));
            const old=actualKey;
            if(value!==old){env[value]=env[old];delete env[old];actualKey=value;}
            for(const field of source.fields)if(field.path.length>=4&&field.path[0]==='services'&&field.path[1]===actualService&&field.path[2]==='environment'&&field.path[3]===old)field.path[3]=value;
          };
          return `<div class="compose-linked-row compose-linked-env">${control(source,[...p],text('variable'),{value:key,apply:rename})}${control(source,p,text('value'))}${action(source,text('remove'),()=>delete env[actualKey],true)}</div>`;
        }).join('');
      }
      envRows+=action(source,text('addVariable'),()=>{
        if(!config.environment||typeof config.environment!=='object')config.environment={};
        if(Array.isArray(config.environment)){config.environment.push('KEY=');return;}
        let key='KEY',i=1;while(Object.hasOwn(config.environment,key))key='KEY_'+i++;
        config.environment[key]='';
      });
      html+=`<details class="compose-linked-details"><summary>${esc(text('environment'))}</summary>${block(text('environment'),'environment',envRows)}</details>`;
      for(const [key,value] of Object.entries(config)){
        if(!['image','container_name','restart','ports','volumes','environment'].includes(key))advanced.push(...fields(value,[...path,key]));
        else if(['ports','volumes'].includes(key)&&value!==undefined&&!Array.isArray(value))advanced.push(...fields(value,[...path,key]));
        else if(key==='environment'&&(value===null||typeof value!=='object'))advanced.push(...fields(value,[...path,key]));
      }
      html+='</div>';
    }
    for(const [key,value] of Object.entries(document))if(key!=='services')advanced.push(...fields(value,[key]));
    if(advanced.length)html+=`<details class="compose-linked-details"><summary>${esc(text('advanced'))}</summary>${block(text('advanced'),'',advanced.map(field=>control(source,field.path,field.path.map(String).join(' › '))).join(''))}</details>`;
    return html;
  }
  function renderForm(){
    const source=active(),form=$('#compose-complete-form');
    form.innerHTML=formHtml(source);
    form.querySelectorAll('[data-compose-field]').forEach(input=>{
      input.addEventListener('input',()=>{
        if(state.busy||source!==active()||source.side==='raw')return;
        const field=source.fields[Number(input.dataset.composeField)];
        try{
          const value=input.type==='checkbox'?input.checked:input.value;
          if(field.apply)field.apply(value);else setValue(source.document,field.path,value);
          input.setCustomValidity('');source.invalidInputs.delete(input);
        }catch(error){input.setCustomValidity(text('formError'));source.invalidInputs.add(input);}
        changed(source,'form');
      });
    });
    form.querySelectorAll('[data-compose-action]').forEach(button=>button.onclick=()=>{
      if(state.busy||source.side==='raw'||source.invalidInputs.size)return;
      source.actions[Number(button.dataset.composeAction)]();renderForm();changed(source,'form');
    });
  }
  function highlighted(line){
    // Escape every token before adding markup, including comments and quoted strings.
    return line.split(/(#[^\n]*|"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[\w.-]+(?=:\s|:$))/g).map(token=>{
      const kind=token.startsWith('#')?'comment':/^["']/.test(token)?'string':/^[\w.-]+$/.test(token)?'key':'';
      return kind?`<span class="compose-code-${kind}">${esc(token)}</span>`:esc(token);
    }).join('');
  }
  function paintYaml(){
    const yaml=$('#compose-complete-yaml'),lines=yaml.value.split('\n');
    $('#compose-complete-highlight').innerHTML=lines.map(highlighted).join('\n')+'\n';
    $('#compose-complete-lines').textContent=lines.map((_,i)=>i+1).join('\n');
    $('#compose-complete-highlight').scrollTop=yaml.scrollTop;
    $('#compose-complete-highlight').scrollLeft=yaml.scrollLeft;
    $('#compose-complete-lines').scrollTop=yaml.scrollTop;
  }
  function status(message,error=''){
    $('#compose-complete-status').textContent=error?'':message;
    if(error==='One or more Compose ports are already in use.')error=text('portConflict');
    const el=$('#compose-complete-error');el.textContent=error?(message===error?message:message+'\n'+error):'';el.hidden=!error;
    $('#compose-complete-dirty').hidden=!!error;
    $('#compose-complete-yaml').setAttribute('aria-invalid',String(!!error&&active().side==='raw'));
    $('#compose-complete-form').inert=active().side==='raw';
    $('#compose-complete-dirty').textContent=text(state.dirty?'unsaved':'unchanged');
  }
  function changed(source,side){
    clearTimeout(state.timer);
    source.revision++;source.side=side;source.error=null;
    if(side==='raw')source.draft=$('#compose-complete-yaml').value;
    state.dirty=true;state.review=false;
    $('#compose-complete-review').hidden=true;$('#compose-complete-back').hidden=true;
    $('#compose-complete-save').textContent=text('review');
    status(text('pending'));paintYaml();
    const current=state;
    state.timer=setTimeout(()=>{if(state===current)flush().catch(()=>{});},220);
  }
  async function flush(){
    if(!state)return;
    clearTimeout(state.timer);
    const current=state,source=active();
    if(source.inFlight)return source.inFlight;
    source.inFlight=(async()=>{
      while(state===current&&source.side){
        const revision=source.revision,side=source.side;
        try{
          if(side==='form'&&source.invalidInputs.size)throw new Error(text('formError'));
          const result=await post('/docker/compose/convert',side==='raw'
            ?{content:source.draft}
            :{content:source.content,document:JSON.stringify(source.document),yaml_tags:JSON.stringify(source.tags||[])});
          if(state!==current)return;
          if(source.revision!==revision)continue;
          source.content=side==='raw'?source.draft:result.content;
          if(side==='raw')source.document=result.document;
          source.tags=result.tags||[];
          source.side=null;source.error=null;source.invalidInputs.clear();
          if(source===active()){
            if(side==='raw')renderForm();
            else $('#compose-complete-yaml').value=source.content;
            paintYaml();status(text('synced'));
          }
        }catch(error){
          if(state!==current)return;
          if(source.revision!==revision)continue;
          source.error=error;
          if(source===active())status(text(side==='raw'?'yamlError':'formError'),side==='raw'?error.message:text('formError'));
          throw error;
        }
      }
    })();
    try{await source.inFlight;}finally{source.inFlight=null;}
  }
  function show(){
    const source=active();source.invalidInputs.clear();
    $('#compose-complete-yaml').value=source.content;
    $('#compose-complete-source').value=String(state.index);
    $('#compose-complete-filename').textContent=source.path.split(/[\\/]/).pop();
    renderForm();paintYaml();status(text('synced'));
  }
  async function transition(action){
    if(!state||state.busy)return;
    const current=state;
    current.busy=true;$('#compose-complete-save').disabled=true;$('#compose-complete-shell').inert=true;
    try{await action();}
    catch(error){if(state===current&&!active().error)status(text('invalid'),error.message);}
    finally{if(state===current){current.busy=false;$('#compose-complete-save').disabled=false;$('#compose-complete-shell').inert=false;}}
  }
  function diff(before,after){
    const left=before.split('\n'),right=after.split('\n');let start=0;
    while(start<left.length&&start<right.length&&left[start]===right[start])start++;
    let endLeft=left.length,endRight=right.length;
    while(endLeft>start&&endRight>start&&left[endLeft-1]===right[endRight-1]){endLeft--;endRight--;}
    return left.slice(start,endLeft).map(line=>'- '+line).concat(right.slice(start,endRight).map(line=>'+ '+line)).join('\n');
  }
  function review(){
    let html=`<h3>${esc(text('reviewTitle'))}</h3>`;
    for(const source of state.sources)if(source.original!==source.content)html+=`<details open><summary>${esc(source.path)}</summary><pre class="mono">${esc(diff(source.original,source.content))}</pre></details>`;
    if(state.envEnabled!==$('#compose-complete-env-enabled').checked||state.envOriginal!==$('#compose-complete-env').value)html+=`<p>${esc(text('envChanged'))}</p>`;
    $('#compose-complete-review').innerHTML=html;
    $('#compose-complete-review').hidden=false;
    $('#compose-complete-save').textContent=uiText('Save');
    state.review=true;
  }
  function envChanged(){
    state.dirty=true;state.review=false;$('#compose-complete-review').hidden=true;$('#compose-complete-back').hidden=true;$('#compose-complete-back').hidden=true;
    $('#compose-complete-save').textContent=text('review');
    $('#compose-complete-dirty').textContent=text('unsaved');
  }
  async function open(name){
    const data=await api('/docker/compose/get?name='+encodeURIComponent(name));
    if(!Array.isArray(data.sources)||!data.sources.length)throw new Error(text('loadError'));
    state={name,sources:data.sources.map(source=>({...source,document:clone(source.document),original:source.content,revision:0,side:null,invalidInputs:new Set()})),index:0,busy:false,dirty:false,review:false,envEnabled:!!data.env_enabled,envOriginal:data.env_content||''};
    const formCard=typeof resetFormCardClasses==='function'?resetFormCardClasses():document.querySelector('#form-overlay .form-card');
    if(formCard)formCard.classList.add('compose-install-card','compose-linked-card');
    $('#form-head').innerHTML=`<div class="compose-install-head"><div class="compose-install-icon"><img src="/static/runvard-icon.png" alt=""></div><div class="compose-install-title"><h3>${esc(text('editTitle')+': '+name)}</h3><p>${esc(text('sync'))}</p></div></div><button class="compose-install-close" type="button" onclick="closeForm()" aria-label="${esc(uiText('Close'))}">✕</button>`;
    $('#form-body').innerHTML=`<div class="compose-install-shell compose-linked-shell" id="compose-complete-shell">
      <div class="compose-linked-toolbar"${data.sources.length===1?' hidden':''}><label for="compose-complete-source">${esc(text('sources'))}</label><select id="compose-complete-source">${data.sources.map((source,index)=>`<option value="${index}">${esc(source.path)}</option>`).join('')}</select></div>
      ${data.reconstructed?`<p class="compose-linked-runtime" role="status">${esc(text('runtime'))}</p>`:''}
      <div class="compose-linked-columns">
        <section class="compose-linked-form-panel" aria-labelledby="compose-complete-settings"><h3 id="compose-complete-settings">${esc(text('settings'))}</h3><div class="compose-form-stack" id="compose-complete-form"></div>
          <details class="compose-linked-details compose-linked-env-file"><summary>${esc(uiText('Environment file'))}</summary><label class="compose-env-toggle"><input type="checkbox" id="compose-complete-env-enabled" ${data.env_enabled?'checked':''}>${esc(uiText('Store a .env file next to docker-compose.yml'))}</label><textarea class="compose-env-textarea" id="compose-complete-env" aria-label=".env" spellcheck="false" ${data.env_enabled?'':'hidden'}>${esc(data.env_content||'')}</textarea></details>
        </section>
        <section class="compose-linked-yaml-panel" id="compose-complete-yaml-panel" aria-labelledby="compose-complete-filename"><h3 id="compose-complete-filename">docker-compose.yml</h3><div class="compose-linked-code mono"><pre class="compose-code-lines" id="compose-complete-lines" aria-hidden="true"></pre><div class="compose-code-input"><pre class="compose-code-highlight" id="compose-complete-highlight" aria-hidden="true"></pre><textarea id="compose-complete-yaml" spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="YAML" aria-describedby="compose-complete-status compose-complete-error" wrap="off"></textarea></div></div><p id="compose-complete-status" class="compose-linked-status" role="status" aria-live="polite"></p></section>
      </div>
      <section class="compose-linked-review" id="compose-complete-review" aria-live="polite" hidden></section>
      <div class="compose-install-actions"><div class="compose-linked-feedback"><span id="compose-complete-dirty" class="compose-install-summary"></span><p id="compose-complete-error" class="compose-linked-error" role="alert" hidden></p></div><div class="compose-install-action-buttons"><button type="button" class="btn ghost" id="compose-complete-back" hidden>${esc(text('back'))}</button><button type="button" class="btn ghost" onclick="closeForm()">${esc(tr('cancel'))}</button><button type="button" class="btn" id="compose-complete-save">${esc(text('review'))}</button></div></div>
    </div>`;
    const yaml=$('#compose-complete-yaml');
    yaml.addEventListener('input',()=>changed(active(),'raw'));
    yaml.addEventListener('scroll',paintYaml);
    yaml.addEventListener('keydown',event=>{
      if(event.key!=='Tab'||event.shiftKey)return;
      event.preventDefault();const start=yaml.selectionStart,end=yaml.selectionEnd;
      yaml.setRangeText('  ',start,end,'end');changed(active(),'raw');
    });
    $('#compose-complete-source').onchange=event=>transition(async()=>{
      const index=Number(event.target.value);
      try{await flush();state.index=index;show();}
      finally{event.target.value=String(state.index);}
    });
    $('#compose-complete-env-enabled').onchange=event=>{$('#compose-complete-env').hidden=!event.target.checked;envChanged();};
    $('#compose-complete-env').addEventListener('input',envChanged);
    $('#compose-complete-back').onclick=()=>{state.review=false;$('#compose-complete-review').hidden=true;$('#compose-complete-back').hidden=true;$('#compose-complete-save').textContent=text('review');};
    $('#compose-complete-save').onclick=()=>transition(async()=>{
      await flush();
      if(!state.review){review();$('#compose-complete-back').hidden=false;return;}
      $('#compose-complete-save').textContent=text('saving');
      const current=state;
      $('#compose-complete-shell').inert=true;
      try{
        const result=await post('/docker/compose/save',{
          name:state.name,content:state.sources[0].content,source_contents:JSON.stringify(state.sources.map(source=>source.content)),
          env_enabled:$('#compose-complete-env-enabled').checked?'1':'0',env_content:$('#compose-complete-env').value,
        });
        requireOk(result,text('invalid'));
        if(state!==current)return;
        state.dirty=false;state.busy=false;closeForm();toast(uiText('Saved'));loadDashboard();
        if(currentTile?.id==='docker')renderDocker($('#modal-body'),'Compose');
      }finally{if(state===current){$('#compose-complete-shell').inert=false;$('#compose-complete-save').textContent=uiText('Save');}}
    });
    show();openFormOverlay('#compose-complete-form input');
  }
  function beforeClose(){
    if(!state)return true;
    if(state.busy)return false;
    if(state.dirty&&!global.confirm(text('discard')))return false;
    clearTimeout(state.timer);state=null;return true;
  }
  global.RunvardComposeEditor={open,fields,setValue,flush,beforeClose};
})(window);
