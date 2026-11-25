from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and "src" in attrs:
            self.paths.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.paths.append(attrs["href"])


def test_server_console_and_file_preview_load_libraries_locally():
    parser = Assets()
    parser.feed((ROOT / "static/index.html").read_text())
    # Fonts and optional service icons may also have remote sources.
    libraries = [p for p in parser.paths if any(name in p.lower() for name in ("xterm", "marked", "sortable"))]
    assert len(libraries) >= 8
    server = (ROOT / "server.py").read_text()
    libraries.extend(re.findall(r'''(?:src=|href=|import\()["']([^"']+(?:xterm[^"']*|rfb\.js))["']''', server))
    assert any("rfb.js" in p for p in libraries)
    for asset in libraries:
        assert asset.startswith("/static/vendor/")
        assert (ROOT / asset.lstrip("/")).is_file()
