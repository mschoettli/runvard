// Minimal DOM adapter for exercising editor events without a browser.
const fs=require('fs');
const cp=require('child_process');
const vm=require('vm');
const source=fs.readFileSync('static/index.html','utf8');
global.COMPOSE_EDITOR_I18N=JSON.parse(source.split('const COMPOSE_EDITOR_I18N=')[1].split(';\nfunction currentLanguage')[0]);
const decode=s=>s.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&');
const elements=new Map();
class Element{
  constructor(id){this.id=id;this.value='';this.hidden=false;this.checked=false;this.dataset={};this.classList={add(){},toggle(){}};this.scrollTop=0;this.scrollLeft=0;this.listeners={};this.validity='';this.html='';}
  set innerHTML(html){
    for(const [id,el] of elements)if(this.html.includes('id="'+id+'"'))elements.delete(id);
    this.html=html;
    for(const match of html.matchAll(/<([a-z][a-z0-9]*)[^>]*\bid="([^"]+)"[^>]*>/g)){
      const el=new Element(match[2]),tag=match[0];
      el.type=(tag.match(/\btype="([^"]+)"/)||[])[1]||'';
      el.dataset.composeField=(tag.match(/data-compose-field="([^"]+)"/)||[])[1];
      el.dataset.composeAction=(tag.match(/data-compose-action="([^"]+)"/)||[])[1];
      el.dataset.composeMode=(tag.match(/data-compose-mode="([^"]+)"/)||[])[1];
      if(match[1]==='input')el.value=decode((tag.match(/\bvalue="([^"]*)"/)||[])[1]||'');
      if(match[1]==='select')el.value=decode((html.slice(match.index+tag.length).split('</select>')[0].match(/<option value="([^"]*)" selected/)||[])[1]||'');
      el.checked=/\schecked(?:\s|>)/.test(tag);
      if(match[1]==='textarea')el.value=decode(html.slice(match.index+tag.length).split('</textarea>')[0]);
      elements.set(el.id,el);
    }
  }
  get innerHTML(){return this.html;}
  querySelectorAll(selector){
    if(selector==='[data-compose-field]')return [...elements.values()].filter(el=>el.dataset.composeField!==undefined&&this.html.includes('id="'+el.id+'"'));
    if(selector==='[data-compose-action]')return [...elements.values()].filter(el=>el.dataset.composeAction!==undefined&&this.html.includes('id="'+el.id+'"'));
    return [];
  }
  setAttribute(){}
  addEventListener(name,handler){this.listeners[name]=handler;}
  setCustomValidity(value){this.validity=value;}
  reportValidity(){return !this.validity;}
}
['form-head','form-body','card'].forEach(id=>elements.set(id,new Element(id)));
global.window=global;
global.document={
  querySelector(selector){return selector==='#form-overlay .form-card'?elements.get('card'):null;},
  querySelectorAll(selector){return selector==='[data-compose-mode]'?[...elements.values()].filter(el=>el.dataset.composeMode):[];},
};
global.$=selector=>elements.get(selector.replace(/^#/,''));
global.esc=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
global.currentLanguage=()=>process.env.TEST_LOCALE||'de';
global.uiText=text=>text;
global.tr=key=>key;
global.openFormOverlay=()=>{};
global.closeForm=()=>{};
global.confirm=()=>true;
global.loadDashboard=()=>{};
global.currentTile=null;
global.requireOk=result=>{if(!result.ok)throw new Error('save failed');};
global.toasts=[];
global.toast=(...args)=>toasts.push(args);
function convert(data){
  return JSON.parse(cp.execFileSync('.venv/bin/python',['-c',
    'import json,sys; from modules.docker_mgr import convert_compose; print(json.dumps(convert_compose(**json.load(sys.stdin))))'],
    {input:JSON.stringify(data),encoding:'utf8',stdio:['pipe','pipe','pipe']}));
}
const base='services:\n  web:\n    image: nginx\n    environment:\n      TEXT: "false"\n    healthcheck:\n      disable: false\n      retries: 3\n  db:\n    image: postgres\nvolumes:\n  data: {}\n';
const override='services:\n  web:\n    ports: '+(process.env.TEST_COMPOSE_TAG?'!override ':'')+'["9090:80"]\n';
global.api=async()=>({sources:[{path:'/srv/compose.yaml',content:base,...convert({content:base})},{path:'/srv/override.yaml',content:override,...convert({content:override})}],env_enabled:true,env_content:'A=example\n'});
global.saved=null;
global.post=async(url,data)=>{
  if(url.endsWith('/convert'))return convert(data);
  saved=data;return {ok:true};
};
require('../static/compose-editor.js');
module.exports={elements,convert};
