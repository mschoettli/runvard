import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("blocked", [True, False])
def test_app_open_falls_back_when_browser_refuses_new_tab(blocked):
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
assert.deepEqual(fallback, {str(blocked).lower()} ? opened : []);
assert.deepEqual(errors, [['No port defined', 'error']]);
if (!{str(blocked).lower()}) assert.equal(newTab.opener, null);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
