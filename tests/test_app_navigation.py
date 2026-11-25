import re
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("blocked", [True, False])
def test_app_open_never_replaces_current_tab(blocked):
    index = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    open_app = index.split("window.appOpen=", 1)[1].split("\n};", 1)[0]
    script = f"""
const assert = require('node:assert/strict');
const window = globalThis;
const opened = [], fallback = [], errors = [];
const newTab = {{opener: window}};
const location = {{hostname: 'server.test', assign: url => fallback.push(url)}};
window.location = location;
window.open = (url, target) => {{
  assert.equal(target, '_blank');
  opened.push(url);
  return {str(blocked).lower()} ? null : newTab;
}};
function toast(message, level) {{errors.push([message, level]);}}
window.appOpen = {open_app}\n}};
appOpen('jellyfin', 8096);
appOpen('compose:example', 8088);
appOpen('no-port', 0);
assert.deepEqual(opened, ['http://server.test:8096', 'http://server.test:8088']);
assert.deepEqual(fallback, []);
assert.deepEqual(errors, [['No port defined', 'error']]);
if (!{str(blocked).lower()}) assert.equal(newTab.opener, null);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_detail_actions_open_only_running_web_apps_in_a_new_tab():
    html = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    helpers = [
        re.search(rf"function {name}\([^)]*\)\{{.*?^\}}", html, re.M | re.S).group()
        for name in ("appWebUrl", "appDetailActions", "appIsCompose")
    ]
    script = "\n".join(helpers) + r"""
const assert = require('node:assert/strict');
const location = {hostname:'server.test'};
const currentAuthStatus = {role:'admin'};
const esc = String, uiText = String, appUi = String;
function actions(app, pending) {
  return appDetailActions(app, pending, appWebUrl(app));
}
const web = {id:'portainer', installed:true, running:true, port:10443, web_scheme:'https'};
const link = actions(web).match(/<a\b[^>]*>/)[0];
assert.match(link, /href="https:\/\/server\.test:10443"/);
assert.match(link, /target="_blank"/);
assert.match(link, /rel="noopener noreferrer"/);
assert.doesNotMatch(link, /onclick/);
assert.equal(appWebUrl({...web, web_scheme:'http', port:8096}), 'http://server.test:8096');
assert.doesNotMatch(actions({...web, running:false}), /<a\b/);
assert.doesNotMatch(actions(web,'restart'), /<a\b/);
for (const [web_scheme, port] of [['',5432], ['http',0], ['https',65536], ['file',80]]) {
  assert.doesNotMatch(actions({...web, web_scheme, port}), /<a\b/);
}
currentAuthStatus.role = 'readonly';
assert.match(actions(web), /<a\b/);
assert.doesNotMatch(actions(web), /<button\b/);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
