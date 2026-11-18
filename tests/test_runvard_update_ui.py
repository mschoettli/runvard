import subprocess
from pathlib import Path


INDEX_HTML = Path("static/index.html")


def test_previous_update_log_cannot_finish_a_newly_started_update():
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = "function runvardUpdateHasFinished("
    end = "function pollRunvardUpdateLog("

    assert start in html
    assert end in html
    helper_source = start + html.split(start, 1)[1].split(end, 1)[0]

    script = f"""
{helper_source}
const previousLog = 'runvard update finished: yesterday';
if (runvardUpdateHasFinished({{status:'running'}}, previousLog, previousLog)) {{
  throw new Error('stale completion was accepted');
}}
if (!runvardUpdateHasFinished({{status:'running'}}, 'runvard update finished: now', previousLog)) {{
  throw new Error('fresh completion was ignored');
}}
if (!runvardUpdateHasFinished({{status:'succeeded'}}, previousLog, previousLog)) {{
  throw new Error('durable success was ignored');
}}
"""
    subprocess.run(["node", "-e", script], check=True)
