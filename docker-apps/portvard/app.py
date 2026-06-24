#!/usr/bin/env python3
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATE_FILE = DATA_DIR / "last_scan.json"
PORT = int(os.environ.get("PORT", "8766"))
SCAN_CIDRS = os.environ.get("SCAN_CIDRS", "auto")
PORT_RANGE = os.environ.get("PORT_RANGE", "common")
NAME_SOURCES = [x.strip().lower() for x in os.environ.get("NAME_SOURCES", "dns,mdns,netbios").split(",") if x.strip()]
WORKERS = int(os.environ.get("SCAN_WORKERS", "512"))
CONNECT_TIMEOUT = float(os.environ.get("CONNECT_TIMEOUT", "0.25"))
QUICK_CONNECT_TIMEOUT = float(os.environ.get("QUICK_CONNECT_TIMEOUT", "0.08"))
QUICK_MAX_HOSTS = int(os.environ.get("QUICK_MAX_HOSTS", "1024"))
COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 139, 143, 443, 445, 548, 587, 631, 993,
    995, 1883, 2049, 3000, 3306, 3389, 5000, 5432, 5900, 6379, 8000, 8080,
    8123, 8443, 8765, 8766, 9000, 9090, 9100, 9443, 32400,
]
DISCOVERY_PORTS = [22, 53, 80, 139, 443, 445, 3389, 8080, 8766]
IGNORED_AUTO_INTERFACE_PREFIXES = (
    "lo", "docker", "br-", "veth", "virbr", "tailscale", "zt", "wg", "tun",
)


state_lock = threading.Lock()
scan_cancel = threading.Event()
scan_thread = None
state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "message": "Bereit",
    "error": "",
    "started_at": "",
    "finished_at": "",
    "scan_mode": "",
    "port_count": 0,
    "result": None,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_last_scan():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None


def save_last_scan(result):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(result, indent=2, sort_keys=True))


def run_cmd(args, timeout=3):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    if proc.returncode != 0:
        return proc.stdout + proc.stderr
    return proc.stdout


def detect_cidrs():
    if SCAN_CIDRS.strip().lower() != "auto":
        return [x.strip() for x in SCAN_CIDRS.split(",") if x.strip()]

    cidrs = []
    out = run_cmd(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=3)
    for line in out.splitlines():
        if_match = re.match(r"\d+:\s+([^:\s]+)", line)
        ifname = (if_match.group(1).split("@", 1)[0] if if_match else "").lower()
        if ifname.startswith(IGNORED_AUTO_INTERFACE_PREFIXES):
            continue
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if not match:
            continue
        try:
            network = ipaddress.ip_interface(match.group(1)).network
        except ValueError:
            continue
        if network.is_private and str(network) not in cidrs:
            cidrs.append(str(network))

    if cidrs:
        return cidrs

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        network = ipaddress.ip_network(f"{ip}/24", strict=False)
        if network.is_private:
            cidrs.append(str(network))
    except Exception:
        pass
    return cidrs


def parse_ports(spec):
    value = str(spec or "").strip().lower()
    if value in ("", "common", "quick", "default"):
        return sorted(set(COMMON_PORTS))
    if value in ("all", "full", "1-65535"):
        return list(range(1, 65536))
    ports = set()
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            left, right = item.split("-", 1)
            start, end = int(left), int(right)
            ports.update(range(max(1, start), min(65535, end) + 1))
        else:
            port = int(item)
            if 1 <= port <= 65535:
                ports.add(port)
    return sorted(ports)


def iter_hosts(cidrs, max_hosts=None):
    seen = set()
    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if not isinstance(net, ipaddress.IPv4Network) or not net.is_private:
            continue
        for ip in net.hosts():
            value = str(ip)
            if value not in seen:
                seen.add(value)
                yield value
                if max_hosts and len(seen) >= max_hosts:
                    return


def ping_host(ip):
    proc = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def check_port(ip, port, timeout=CONNECT_TIMEOUT):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover_host(ip):
    if scan_cancel.is_set():
        return None
    if ping_host(ip):
        return ip
    for port in DISCOVERY_PORTS:
        if scan_cancel.is_set():
            return None
        if check_port(ip, port, QUICK_CONNECT_TIMEOUT):
            return ip
    return None


def discover_arp_hosts(cidrs):
    found = {}
    for cidr in cidrs:
        if scan_cancel.is_set():
            break
        out = run_cmd(
            ["arp-scan", "--retry=1", "--timeout=500", "--plain", cidr],
            timeout=8,
        )
        for line in out.splitlines():
            match = re.match(
                r"^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})\b",
                line,
            )
            if match:
                found[match.group(1)] = match.group(2).lower()
    return found


def resolve_dns(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def resolve_mdns(ip):
    out = run_cmd(["avahi-resolve-address", ip], timeout=2).strip()
    if not out:
        return ""
    parts = out.split()
    return parts[-1].rstrip(".") if len(parts) >= 2 else ""


def resolve_netbios(ip):
    out = run_cmd(["nmblookup", "-A", ip], timeout=3)
    for line in out.splitlines():
        if "<00>" in line and "GROUP" not in line:
            name = line.split("<00>", 1)[0].strip()
            if name:
                return name
    return ""


def resolve_name(ip):
    resolvers = {
        "dns": resolve_dns,
        "mdns": resolve_mdns,
        "netbios": resolve_netbios,
    }
    for source in NAME_SOURCES:
        resolver = resolvers.get(source)
        if not resolver:
            continue
        name = resolver(ip)
        if name:
            return name, source
    return "", ""


def read_mac(ip):
    try:
        arp = Path("/proc/net/arp").read_text()
    except Exception:
        return ""
    for line in arp.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == ip and parts[3] != "00:00:00:00:00:00":
            return parts[3]
    return ""


def scan_host(ip, ports, known_alive=False, timeout=CONNECT_TIMEOUT, known_mac=""):
    if scan_cancel.is_set():
        return None
    alive = known_alive or ping_host(ip)
    open_ports = []
    for port in ports:
        if scan_cancel.is_set():
            return None
        if check_port(ip, port, timeout):
            open_ports.append(port)
    if not alive and not open_ports:
        return None
    name, source = resolve_name(ip)
    return {
        "ip": ip,
        "name": name,
        "name_source": source,
        "mac": known_mac or read_mac(ip),
        "scan_time": now_iso(),
        "open_ports": open_ports,
        "closed_or_filtered": max(0, len(ports) - len(open_ports)),
    }


def scan_worker(port_spec=None, scan_mode="quick"):
    started = now_iso()
    try:
        cidrs = detect_cidrs()
        effective_spec = port_spec or PORT_RANGE
        ports = parse_ports(effective_spec)
        is_quick = scan_mode == "quick"
        hosts = list(iter_hosts(cidrs, QUICK_MAX_HOSTS if is_quick else None))
        with state_lock:
            state.update({
                "running": True,
                "progress": 0,
                "total": len(hosts),
                "message": "Vollscan laeuft" if scan_mode == "full" else "Geraete werden gesucht",
                "error": "",
                "started_at": started,
                "finished_at": "",
                "scan_mode": scan_mode,
                "port_count": len(ports),
            })
        devices = []
        scan_hosts = hosts
        discovery_macs = {}
        if is_quick:
            active_hosts = set()
            discovery_macs = discover_arp_hosts(cidrs)
            active_hosts.update(discovery_macs)
            with state_lock:
                state["message"] = f"{len(active_hosts)} Geraete per ARP gefunden"
            with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
                futures = {pool.submit(discover_host, ip): ip for ip in hosts}
                for idx, future in enumerate(as_completed(futures), start=1):
                    if scan_cancel.is_set():
                        break
                    found = future.result()
                    if found:
                        active_hosts.add(found)
                    with state_lock:
                        state["progress"] = idx
                        state["message"] = f"{idx}/{len(hosts)} Hosts gesucht, {len(active_hosts)} aktiv"
            scan_hosts = sorted(active_hosts, key=ipaddress.ip_address)
            with state_lock:
                state["progress"] = 0
                state["total"] = len(scan_hosts)
                state["message"] = f"{len(scan_hosts)} aktive Geraete, Ports werden geprueft"
        with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
            futures = {
                pool.submit(
                    scan_host,
                    ip,
                    ports,
                    is_quick,
                    QUICK_CONNECT_TIMEOUT if is_quick else CONNECT_TIMEOUT,
                    discovery_macs.get(ip, ""),
                ): ip for ip in scan_hosts
            }
            for idx, future in enumerate(as_completed(futures), start=1):
                if scan_cancel.is_set():
                    break
                item = future.result()
                if item:
                    devices.append(item)
                with state_lock:
                    state["progress"] = idx
                    state["message"] = f"{idx}/{len(scan_hosts)} aktive Hosts, {len(ports)} Ports je Host"
        result = {
            "started_at": started,
            "finished_at": now_iso(),
            "cancelled": scan_cancel.is_set(),
            "cidrs": cidrs,
            "port_range": effective_spec,
            "port_count": len(ports),
            "scan_mode": scan_mode,
            "hosts_considered": len(hosts),
            "hosts_scanned": len(scan_hosts),
            "devices": sorted(devices, key=lambda x: ipaddress.ip_address(x["ip"])),
        }
        save_last_scan(result)
        with state_lock:
            state.update({
                "running": False,
                "finished_at": result["finished_at"],
                "message": "Scan abgebrochen" if result["cancelled"] else "Scan fertig",
                "result": result,
            })
    except Exception as exc:
        with state_lock:
            state.update({"running": False, "error": str(exc), "message": "Fehler"})


HTML = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ports</title>
  <style>
    :root{color-scheme:dark;--bg:#101417;--panel:#171d21;--line:#2b353b;--text:#eef3f5;--muted:#91a0a8;--accent:#4fb3ff;--ok:#5ee28b;--bad:#ff7979}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 24px;border-bottom:1px solid var(--line);background:#11181c}
    h1{font-size:22px;margin:0}
    main{padding:22px 24px;max-width:1280px;margin:0 auto}
    button{border:1px solid var(--line);background:#1d262b;color:var(--text);border-radius:7px;padding:9px 13px;cursor:pointer}
    button.primary{background:var(--accent);border-color:var(--accent);color:#061017;font-weight:700}
    button:disabled{opacity:.55;cursor:not-allowed}
    .bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    .status{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px;margin:18px 0}
    .metric{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}
    .metric b{display:block;font-size:18px}.metric span{color:var(--muted);font-size:12px}
    progress{width:100%;height:12px;accent-color:var(--accent)}
    table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
    th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
    th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;background:#141a1e}
    tr:last-child td{border-bottom:0}
    .muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}
    .ports{display:flex;gap:5px;flex-wrap:wrap}
    .chip{border:1px solid var(--line);border-radius:999px;padding:2px 7px;background:#10171b}
    @media(max-width:760px){header{align-items:flex-start;flex-direction:column}.status{grid-template-columns:1fr 1fr}table{font-size:13px}}
  </style>
</head>
<body>
  <header>
    <h1>Ports</h1>
    <div class="bar">
      <button class="primary" id="start">Schnellscan</button>
      <button id="full">Vollscan</button>
      <button id="cancel">Abbrechen</button>
    </div>
  </header>
  <main>
    <progress id="progress" value="0" max="1"></progress>
    <div class="status">
      <div class="metric"><b id="devices">0</b><span>Geraete</span></div>
      <div class="metric"><b id="checked">0</b><span>Gepruefte Hosts</span></div>
      <div class="metric"><b id="range">-</b><span>Ports je Host</span></div>
      <div class="metric"><b id="message">Bereit</b><span>Status</span></div>
    </div>
    <p id="error" class="bad"></p>
    <table>
      <thead><tr><th>IP</th><th>Name</th><th>MAC</th><th>Offene Ports</th><th>Geschlossen/gefiltert</th><th>Scanzeit</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    async function json(url, options){const r=await fetch(url, options||{}); return r.json();}
    function render(data){
      const result=data.result||{};
      const devices=result.devices||[];
      document.getElementById('start').disabled=!!data.running;
      document.getElementById('full').disabled=!!data.running;
      document.getElementById('cancel').disabled=!data.running;
      document.getElementById('devices').textContent=devices.length;
      document.getElementById('checked').textContent=(data.progress||0)+'/'+(data.total||0);
      document.getElementById('range').textContent=(data.port_count||result.port_count||'-')+'';
      document.getElementById('message').textContent=data.message||'Bereit';
      document.getElementById('error').textContent=data.error||'';
      const p=document.getElementById('progress');
      p.max=Math.max(1,data.total||1); p.value=data.progress||0;
      document.getElementById('rows').innerHTML=devices.map(d=>`
        <tr>
          <td>${d.ip}</td>
          <td>${d.name||'<span class="muted">-</span>'} <span class="muted">${d.name_source||''}</span></td>
          <td>${d.mac||'<span class="muted">-</span>'}</td>
          <td><div class="ports">${(d.open_ports||[]).map(p=>`<span class="chip ok">${p}</span>`).join('')||'<span class="muted">keine</span>'}</div></td>
          <td>${d.closed_or_filtered||0}</td>
          <td>${d.scan_time||''}</td>
        </tr>`).join('');
    }
    async function refresh(){render(await json('/api/status'));}
    document.getElementById('start').onclick=async()=>{await json('/api/scan/start?mode=quick',{method:'POST'}); refresh();};
    document.getElementById('full').onclick=async()=>{await json('/api/scan/start?mode=full',{method:'POST'}); refresh();};
    document.getElementById('cancel').onclick=async()=>{await json('/api/scan/cancel',{method:'POST'}); refresh();};
    refresh(); setInterval(refresh, 1500);
  </script>
</body>
</html>
"""


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
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            with state_lock:
                payload = dict(state)
                payload["result"] = state["result"] or load_last_scan()
            self._send(200, json.dumps(payload))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        global scan_thread
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/scan/start":
            with state_lock:
                if state["running"]:
                    self._send(409, json.dumps({"error": "scan already running"}))
                    return
                query = parse_qs(parsed.query)
                mode = (query.get("mode") or ["quick"])[0]
                if mode == "full":
                    port_spec = "full"
                    scan_mode = "full"
                else:
                    port_spec = PORT_RANGE
                    scan_mode = "quick"
                scan_cancel.clear()
                state.update({"running": True, "progress": 0, "total": 0, "message": "Scan startet", "error": "", "scan_mode": scan_mode})
                scan_thread = threading.Thread(target=scan_worker, args=(port_spec, scan_mode), daemon=True)
                scan_thread.start()
            self._send(202, json.dumps({"ok": True}))
            return
        if path == "/api/scan/cancel":
            scan_cancel.set()
            self._send(202, json.dumps({"ok": True}))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):
        return


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state["result"] = load_last_scan()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Portvard listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
