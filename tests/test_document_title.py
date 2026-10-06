import subprocess
from pathlib import Path


INDEX_HTML = Path("static/index.html")


def test_document_title_includes_computer_name_and_update_status():
    html = INDEX_HTML.read_text(encoding="utf-8")

    start = "const RUNVARD_DOCUMENT_TITLE='runvard';"
    end = "function applySystemUpdateBadge(){"
    assert start in html
    assert end in html
    title_source = start + html.split(start, 1)[1].split(end, 1)[0]
    assert "setRunvardComputerName(i.hostname)" in html

    script = f"""
global.document = {{ title: '' }};
global.uiText = value => value;
let _systemUpdateBadgeHasUpdates = false;
{title_source}
setRunvardComputerName('office-server');
if (document.title !== 'office-server · runvard') {{
  throw new Error(`unexpected normal title: ${{document.title}}`);
}}
_systemUpdateBadgeHasUpdates = true;
updateDocumentTitle();
if (document.title !== 'Update available · office-server · runvard') {{
  throw new Error(`unexpected update title: ${{document.title}}`);
}}
"""
    subprocess.run(["node", "-e", script], check=True)
