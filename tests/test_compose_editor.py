import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def use_current_python_interpreter(monkeypatch):
    # Node subprocesses must use the same environment as pytest, including CI.
    monkeypatch.setenv('RUNVARD_TEST_PYTHON', sys.executable)


def run_js(script):
    module = ROOT / 'static' / 'compose-editor.js'
    assert module.exists(), 'The complete Compose form editor must exist'
    source = f"global.window=global; require({json.dumps(str(module))});\n" + script
    result = subprocess.run(['node', '-e', source], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_form_fields_include_every_nested_service_setting():
    result = run_js('''
const document={services:{web:{image:'nginx',environment:{FLAG:'false'},healthcheck:{test:['CMD','true'],disable:false}},db:{image:'postgres',ports:[{target:5432,published:'5432',host_ip:'127.0.0.1'}]}},volumes:{data:null}};
process.stdout.write(JSON.stringify(RunvardComposeEditor.fields(document)));
''')
    paths = [field['path'] for field in result]
    assert ['services', 'web', 'environment', 'FLAG'] in paths
    assert ['services', 'db', 'ports', 0, 'host_ip'] in paths
    assert ['services', 'web', 'healthcheck', 'disable'] in paths
    assert ['volumes', 'data'] in paths


def test_form_edits_preserve_value_types_and_unrelated_services():
    result = run_js('''
const document={services:{web:{environment:{FLAG:'false',N:'01'},healthcheck:{disable:false,retries:3}},db:{image:'postgres'}}};
RunvardComposeEditor.setValue(document,['services','web','environment','FLAG'],'true');
RunvardComposeEditor.setValue(document,['services','web','healthcheck','disable'],true);
RunvardComposeEditor.setValue(document,['services','web','healthcheck','retries'],'5');
process.stdout.write(JSON.stringify(document));
''')
    web = result['services']['web']
    assert web['environment'] == {'FLAG': 'true', 'N': '01'}
    assert web['healthcheck'] == {'disable': True, 'retries': 5}
    assert result['services']['db'] == {'image': 'postgres'}


def test_every_container_card_has_an_editor_entry():
    source = (ROOT / 'static' / 'index.html').read_text()
    assert 'RunvardComposeEditor.open' in source
    assert "composeEdit('container:'" in source


def test_form_yaml_and_override_switches_keep_all_values():
    script = r'''
const assert=require('assert');
const {elements,convert}=require('./tests/compose_editor_harness.cjs');
(async()=>{
  await RunvardComposeEditor.open('demo');
  const fields=[...elements.values()].filter(el=>el.dataset.composeField!==undefined);
  const text=fields.find(el=>el.value==='false'&&el.type!=='checkbox');
  text.value='first\nsecond';text.listeners.input();
  const boolean=fields.find(el=>el.type==='checkbox');boolean.checked=true;boolean.listeners.input();
  const number=fields.find(el=>el.value==='3');number.value='5';number.listeners.input();
  await RunvardComposeEditor.flush();
  let parsed=convert({content:elements.get('compose-complete-yaml').value}).document;
  assert.strictEqual(parsed.services.web.environment.TEXT,'first\nsecond');
  assert.strictEqual(parsed.services.web.healthcheck.disable,true);
  assert.strictEqual(parsed.services.web.healthcheck.retries,5);
  assert.strictEqual(parsed.services.db.image,'postgres');
  const edited=elements.get('compose-complete-yaml').value.replace('postgres','postgres:17');
  elements.get('compose-complete-yaml').value=edited;
  await elements.get('compose-complete-yaml').listeners.input();
  await RunvardComposeEditor.flush().catch(()=>{});
  assert(elements.get('compose-complete-form').innerHTML.includes('postgres:17'));
  const selector=elements.get('compose-complete-source');
  selector.value='1';await selector.onchange({target:selector});
  await new Promise(resolve=>setImmediate(resolve));
  assert(elements.get('compose-complete-form').innerHTML.includes('value="9090"'));
  await elements.get('compose-complete-save').onclick();
  await elements.get('compose-complete-save').onclick();
  const sources=JSON.parse(saved.source_contents);
  assert.strictEqual(convert({content:sources[0]}).document.services.db.image,'postgres:17');
  assert.strictEqual(convert({content:sources[1]}).document.services.web.ports[0],'9090:80');
  assert.strictEqual(saved.env_content,'A=example\n');
  assert.strictEqual(toasts.filter(t=>t[1]==='error').length,0);
  process.stdout.write(JSON.stringify({ok:true}));
})().catch(e=>{console.error(e);process.exit(1);});
'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['ok']


def test_invalid_yaml_keeps_editor_and_does_not_save():
    script = r'''
const assert=require('assert');
const {elements}=require('./tests/compose_editor_harness.cjs');
(async()=>{
  await RunvardComposeEditor.open('demo');
  await RunvardComposeEditor.flush();
  elements.get('compose-complete-yaml').value='services: [';
  await elements.get('compose-complete-yaml').listeners.input();
  await RunvardComposeEditor.flush().catch(()=>{});
  assert.strictEqual(elements.get('compose-complete-yaml-panel').hidden,false);
  assert.strictEqual(elements.get('compose-complete-yaml').value,'services: [');
  await elements.get('compose-complete-save').onclick();
  assert.strictEqual(saved,null);
  assert(elements.get('compose-complete-error').textContent.length>0);
  process.stdout.write(JSON.stringify({ok:true}));
})().catch(e=>{console.error(e);process.exit(1);});
'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_editor_renders_translated_guidance_in_every_locale():
    import os
    for locale in ('de', 'en', 'fr', 'it', 'es', 'pt'):
        script = r'''
const assert=require('assert');
const {elements}=require('./tests/compose_editor_harness.cjs');
(async()=>{
  await RunvardComposeEditor.open('demo');
  const dictionary=COMPOSE_EDITOR_I18N[currentLanguage()];
  assert(elements.get('form-body').innerHTML.includes(esc(dictionary.settings)));
  assert(elements.get('form-body').innerHTML.includes(esc(dictionary.sources)));
  assert(elements.get('compose-complete-form').innerHTML.includes(esc(dictionary.container)));
  assert(elements.get('compose-complete-form').innerHTML.includes('postgres'));
})().catch(e=>{console.error(e);process.exit(1);});
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                                text=True, env={**os.environ, 'TEST_LOCALE': locale})
        assert result.returncode == 0, f'{locale}: {result.stdout}{result.stderr}'


def test_form_edits_preserve_compose_override_tags():
    import os
    script = r'''
const assert=require('assert');
const {elements,convert}=require('./tests/compose_editor_harness.cjs');
(async()=>{
  await RunvardComposeEditor.open('demo');
  const selector=elements.get('compose-complete-source');selector.value='1';
  await selector.onchange({target:selector});await new Promise(resolve=>setImmediate(resolve));
  const field=[...elements.values()].find(el=>el.dataset.composeField!==undefined&&el.value==='9090');
  field.value='9091';field.listeners.input();
  await RunvardComposeEditor.flush();
  const content=elements.get('compose-complete-yaml').value;
  assert(content.includes('!override'));
  assert.strictEqual(convert({content}).document.services.web.ports[0],'9091:80');
  assert.strictEqual(toasts.filter(t=>t[1]==='error').length,0);
})().catch(e=>{console.error(e);process.exit(1);});
'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                            text=True, env={**os.environ, 'TEST_COMPOSE_TAG': '1'})
    assert result.returncode == 0, result.stdout + result.stderr


def test_split_editor_is_permanent_and_yaml_updates_form_automatically():
    result = run_editor_js(r'''
await RunvardComposeEditor.open('demo');
assert(!elements.get('form-body').innerHTML.includes('data-compose-mode'));
assert.strictEqual(elements.get('compose-complete-yaml-panel').hidden,false);
const yaml=elements.get('compose-complete-yaml');
yaml.value=yaml.value.replace('postgres','postgres:18');
yaml.listeners.input();
await RunvardComposeEditor.flush();
assert(elements.get('compose-complete-form').innerHTML.includes('postgres:18'));
assert.strictEqual(yaml.value.includes('postgres:18'),true);
''')
    assert result['ok']


def run_editor_js(body):
    script = "const assert=require('assert');const {elements,convert}=require('./tests/compose_editor_harness.cjs');\n(async()=>{\n" + body + "\nprocess.stdout.write(JSON.stringify({ok:true}));\n})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_invalid_yaml_can_be_corrected_without_losing_last_valid_form():
    run_editor_js(r'''
await RunvardComposeEditor.open('demo');
const yaml=elements.get('compose-complete-yaml'), original=yaml.value;
yaml.value='services: [';yaml.listeners.input();
await RunvardComposeEditor.flush().catch(()=>{});
assert.strictEqual(yaml.value,'services: [');
assert(elements.get('compose-complete-form').innerHTML.includes('postgres'));
assert.strictEqual(elements.get('compose-complete-form').inert,true);
yaml.value=original.replace('postgres','postgres:18');yaml.listeners.input();
await RunvardComposeEditor.flush();
assert.strictEqual(elements.get('compose-complete-form').inert,false);
assert(elements.get('compose-complete-form').innerHTML.includes('postgres:18'));
''')


def test_stale_conversion_cannot_overwrite_new_yaml_input():
    run_editor_js(r'''
await RunvardComposeEditor.open('demo');
const realPost=post;let resolve;
global.post=async(url,data)=>url.endsWith('/convert')?new Promise(r=>{resolve=()=>r(convert(data));}):realPost(url,data);
const image=[...elements.values()].find(el=>el.dataset.composeField!==undefined&&el.value==='nginx');
image.value='nginx:stable';image.listeners.input();
const flushing=RunvardComposeEditor.flush();
await new Promise(r=>setImmediate(r));
const yaml=elements.get('compose-complete-yaml');
yaml.value=yaml.value.replace('postgres','postgres:18');yaml.listeners.input();
global.post=realPost;resolve();await flushing;
assert(yaml.value.includes('postgres:18'));
assert(elements.get('compose-complete-form').innerHTML.includes('postgres:18'));
''')


def test_yaml_can_recover_from_invalid_numeric_form_field():
    run_editor_js(r'''
await RunvardComposeEditor.open('demo');
const number=[...elements.values()].find(el=>el.dataset.composeField!==undefined&&el.value==='3');
number.value='';number.listeners.input();
await RunvardComposeEditor.flush().catch(()=>{});
const yaml=elements.get('compose-complete-yaml');
yaml.value=yaml.value.replace('retries: 3','retries: 5');yaml.listeners.input();
await RunvardComposeEditor.flush();
assert(elements.get('compose-complete-form').innerHTML.includes('value="5"'));
''')


def test_repeated_form_edits_after_sync_update_same_document():
    run_editor_js(r'''
await RunvardComposeEditor.open('demo');
const image=[...elements.values()].find(el=>el.dataset.composeField!==undefined&&el.value==='nginx');
image.value='nginx:stable';image.listeners.input();await RunvardComposeEditor.flush();
image.value='nginx:alpine';image.listeners.input();await RunvardComposeEditor.flush();
assert.strictEqual(convert({content:elements.get('compose-complete-yaml').value}).document.services.web.image,'nginx:alpine');
''')


def test_node_harness_uses_pytest_interpreter_without_local_virtualenv(tmp_path):
    import os
    import shutil
    import sys

    (tmp_path / 'static').mkdir()
    shutil.copyfile(ROOT / 'static' / 'index.html', tmp_path / 'static' / 'index.html')
    script = f"const {{convert}}=require({json.dumps(str(ROOT / 'tests' / 'compose_editor_harness.cjs'))});process.stdout.write(JSON.stringify(convert({{content:'services: {{web: {{image: nginx}}}}'}}).document));"
    result = subprocess.run(['node', '-e', script], cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, 'RUNVARD_TEST_PYTHON': sys.executable,
                                 'PYTHONPATH': str(ROOT)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'services': {'web': {'image': 'nginx'}}}
