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


def test_app_store_open_controls_are_native_links_to_new_tabs():
    import re
    from html.parser import HTMLParser

    index = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    assert 'onclick="appOpen(' not in index
    links = re.findall(r'<a\b[^>]*class="btn sm success"[^>]*>', index)
    app_links = [link for link in links if 'location.hostname' in link]
    assert len(app_links) == 2

    class LinkParser(HTMLParser):
        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            assert tag == "a"
            assert attrs["target"] == "_blank"
            assert {"noopener", "noreferrer"} <= set(attrs["rel"].split())
            assert attrs["href"] == "http://${location.hostname}:${a.port}"
            assert "onclick" not in attrs

    for link in app_links:
        LinkParser().feed(link)
