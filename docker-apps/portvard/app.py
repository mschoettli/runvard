#!/usr/bin/env python3
import html
import json
import os
import re
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PORT = int(os.environ.get("PORT", "8766"))
RUNVARD_APPS_DIR = Path(os.environ.get("RUNVARD_APPS_DIR", "/runvard/apps"))
RUNVARD_COMPOSE_DIR = Path(os.environ.get("RUNVARD_COMPOSE_DIR", "/runvard/compose"))
IGNORED_INTERFACE_PREFIXES = (
    "lo", "docker", "br-", "veth", "virbr", "tailscale", "zt", "wg", "tun",
)


def run_cmd(args, timeout=3):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    return proc.stdout if proc.returncode == 0 else proc.stdout + proc.stderr


def host_ips():
    ips = []
    out = run_cmd(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=3)
    for line in out.splitlines():
        if_match = re.match(r"\d+:\s+([^:\s]+)", line)
        ifname = (if_match.group(1).split("@", 1)[0] if if_match else "").lower()
        if ifname.startswith(IGNORED_INTERFACE_PREFIXES):
            continue
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/\d+", line)
        if match:
            ips.append(match.group(1))
    if ips:
        return sorted(dict.fromkeys(ips))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return [ip]
    except Exception:
        return ["127.0.0.1"]


def app_label(project_name):
    return project_name.replace("-", " ").replace("_", " ").strip().title() or project_name


def _strip_quote(value):
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_port_token(token):
    token = _strip_quote(token).strip()
    token = token.split("#", 1)[0].strip()
    if not token:
        return None
    proto = "tcp"
    if "/" in token:
        token, proto = token.rsplit("/", 1)
        proto = proto.strip() or "tcp"
    parts = token.split(":")
    if len(parts) == 1:
        return None
    bind_ip = ""
    host_port = parts[0]
    if len(parts) >= 3:
        bind_ip = parts[-3] if re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[-3]) else ""
        host_port = parts[-2]
    if "-" in host_port:
        host_port = host_port.split("-", 1)[0]
    if not host_port.isdigit():
        return None
    port = int(host_port)
    if not 1 <= port <= 65535:
        return None
    return {"bind_ip": bind_ip, "port": port, "protocol": proto.lower()}


def _service_display_name(project_name, service_name, service_count):
    base = app_label(project_name)
    if service_count <= 1 or service_name == project_name:
        return base
    return f"{base} / {service_name}"


def parse_compose_ports(content, project_name, source, fallback_ips):
    services = {}
    current = None
    current_list = ""
    for raw in str(content or "").splitlines():
        line = raw.rstrip()
        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$", line)
        if service_match:
            current = service_match.group(1)
            services.setdefault(current, {"ports": [], "env": {}, "host": False})
            current_list = ""
            continue
        if not current or not line.startswith("    "):
            current_list = ""
            continue
        stripped = line.strip()
        if re.match(r"^[A-Za-z0-9_.-]+:", stripped):
            current_list = ""
        if stripped.startswith("network_mode:") and "host" in stripped:
            services[current]["host"] = True
        elif stripped.startswith("ports:"):
            current_list = "ports"
        elif stripped.startswith("environment:"):
            current_list = "environment"
        elif current_list == "ports" and stripped.startswith("- "):
            parsed = _parse_port_token(stripped[2:].strip())
            if parsed:
                services[current]["ports"].append(parsed)
        elif current_list == "environment" and stripped.startswith("- "):
            item = _strip_quote(stripped[2:].strip())
            if "=" in item:
                key, value = item.split("=", 1)
                services[current]["env"][key.strip()] = _strip_quote(value)
        elif current_list == "environment" and ":" in stripped:
            key, value = stripped.split(":", 1)
            services[current]["env"][key.strip()] = _strip_quote(value)

    rows = []
    service_count = len(services)
    for service_name, info in services.items():
        display_name = _service_display_name(project_name, service_name, service_count)
        for item in info["ports"]:
            targets = [item["bind_ip"]] if item["bind_ip"] and item["bind_ip"] != "0.0.0.0" else fallback_ips
            for ip in targets:
                rows.append({
                    "ip": ip,
                    "port": item["port"],
                    "protocol": item["protocol"],
                    "app": display_name,
                    "service": service_name,
                    "source": source,
                })
        if info["host"]:
            port = str(info["env"].get("PORT", "")).strip()
            if port.isdigit():
                for ip in fallback_ips:
                    rows.append({
                        "ip": ip,
                        "port": int(port),
                        "protocol": "tcp",
                        "app": display_name,
                        "service": service_name,
                        "source": source,
                    })
    return rows


def compose_files():
    files = []
    if RUNVARD_APPS_DIR.is_dir():
        for path in sorted(RUNVARD_APPS_DIR.glob("*/docker-compose.yml")):
            files.append((path.parent.name, "App", path))
    if RUNVARD_COMPOSE_DIR.is_dir():
        for path in sorted(RUNVARD_COMPOSE_DIR.glob("*")):
            if path.is_file() and path.suffix in (".yml", ".yaml"):
                files.append((path.stem, "Compose", path))
            elif path.is_dir():
                for name in ("docker-compose.yml", "compose.yml", "docker-compose.yaml", "compose.yaml"):
                    candidate = path / name
                    if candidate.is_file():
                        files.append((path.name, "Compose", candidate))
                        break
    return files


def runvard_ports():
    ips = host_ips()
    rows = []
    for project_name, source, path in compose_files():
        try:
            content = path.read_text()
        except OSError:
            continue
        rows.extend(parse_compose_ports(content, project_name, source, ips))
    seen = set()
    unique = []
    for row in rows:
        key = (row["ip"], row["port"], row["protocol"], row["app"], row["service"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return sorted(unique, key=lambda x: (x["ip"], x["port"], x["app"]))


def page():
    return """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ports</title>
  <style>
    :root{color-scheme:dark;--bg:#101417;--panel:#171d21;--line:#2b353b;--text:#eef3f5;--muted:#91a0a8;--accent:#4fb3ff}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 24px;border-bottom:1px solid var(--line);background:#11181c}
    h1{font-size:22px;margin:0}
    main{padding:22px 24px;max-width:1120px;margin:0 auto}
    button{border:1px solid var(--line);background:#1d262b;color:var(--text);border-radius:7px;padding:9px 13px;cursor:pointer}
    button.primary{background:var(--accent);border-color:var(--accent);color:#061017;font-weight:700}
    .summary{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr));gap:10px;margin:18px 0}
    .metric{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}
    .metric b{display:block;font-size:18px}.metric span{color:var(--muted);font-size:12px}
    table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
    th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line)}
    th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;background:#141a1e}
    tr:last-child td{border-bottom:0}.muted{color:var(--muted)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
    @media(max-width:760px){header{align-items:flex-start;flex-direction:column}.summary{grid-template-columns:1fr 1fr}table{font-size:13px}}
  </style>
</head>
<body>
  <header><h1>Ports</h1><button class="primary" id="refresh">Aktualisieren</button></header>
  <main>
    <div class="summary">
      <div class="metric"><b id="count">0</b><span>Runvard-Ports</span></div>
      <div class="metric"><b id="apps">0</b><span>Apps</span></div>
      <div class="metric"><b id="hosts">0</b><span>IPs</span></div>
    </div>
    <table>
      <thead><tr><th>IP</th><th>Port</th><th>Name der App</th><th>Quelle</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    async function load(){
      const data=await fetch('/api/ports').then(r=>r.json());
      const rows=data.ports||[];
      document.getElementById('count').textContent=rows.length;
      document.getElementById('apps').textContent=new Set(rows.map(r=>r.app)).size;
      document.getElementById('hosts').textContent=new Set(rows.map(r=>r.ip)).size;
      document.getElementById('rows').innerHTML=rows.map(r=>`
        <tr>
          <td class="mono">${esc(r.ip)}</td>
          <td class="mono">${esc(r.port)}${r.protocol&&r.protocol!=='tcp'?'/'+esc(r.protocol):''}</td>
          <td>${esc(r.app)}</td>
          <td class="muted">${esc(r.source)}</td>
        </tr>`).join('')||'<tr><td colspan="4" class="muted">Keine von Runvard verwalteten Ports gefunden.</td></tr>';
    }
    document.getElementById('refresh').onclick=load;
    load();
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, page(), "text/html; charset=utf-8")
            return
        if path == "/api/ports":
            self._send(200, json.dumps({"ports": runvard_ports()}))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):
        return


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Portvard listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
