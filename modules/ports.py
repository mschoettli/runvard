"""Runvard-managed port overview."""
import os
import re
import socket
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


APPS_DIR = os.getenv("RUNVARD_APPS_DIR", "/opt/runvard/data/apps")
COMPOSE_DIR = os.getenv("RUNVARD_COMPOSE_DIR", "/opt/runvard/data/compose")
IGNORED_INTERFACE_PREFIXES = (
    "lo", "docker", "br-", "veth", "virbr", "tailscale", "zt", "wg", "tun",
)


def _run_cmd(args, timeout=3):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    return proc.stdout if proc.returncode == 0 else proc.stdout + proc.stderr


def host_ips():
    ips = []
    out = _run_cmd(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=3)
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
    if isinstance(token, dict):
        host_port = token.get("published") or token.get("host_port")
        bind_ip = str(token.get("host_ip") or "")
        proto = str(token.get("protocol") or "tcp")
        if str(host_port or "").isdigit():
            port = int(host_port)
            if 1 <= port <= 65535:
                return {"bind_ip": bind_ip, "port": port, "protocol": proto.lower()}
        return None
    token = _strip_quote(token).strip().split("#", 1)[0].strip()
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


def _env_port(env):
    if isinstance(env, dict):
        value = env.get("PORT")
        return int(str(value)) if str(value or "").isdigit() else None
    if isinstance(env, list):
        for item in env:
            key, sep, value = str(item).partition("=")
            if sep and key == "PORT" and value.isdigit():
                return int(value)
    return None


def _rows_from_services(services, project_name, source, fallback_ips):
    rows = []
    service_count = len(services)
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        display_name = _service_display_name(project_name, service_name, service_count)
        for raw in service.get("ports") or []:
            item = _parse_port_token(raw)
            if not item:
                continue
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
        if service.get("network_mode") == "host":
            port = _env_port(service.get("environment") or {})
            if port:
                for ip in fallback_ips:
                    rows.append({
                        "ip": ip,
                        "port": port,
                        "protocol": "tcp",
                        "app": display_name,
                        "service": service_name,
                        "source": source,
                    })
    return rows


def _parse_compose_ports_fallback(content, project_name, source, fallback_ips):
    services = {}
    current = None
    current_list = ""
    for raw in str(content or "").splitlines():
        line = raw.rstrip()
        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$", line)
        if service_match:
            current = service_match.group(1)
            services.setdefault(current, {"ports": [], "environment": {}, "network_mode": ""})
            current_list = ""
            continue
        if not current or not line.startswith("    "):
            current_list = ""
            continue
        stripped = line.strip()
        if re.match(r"^[A-Za-z0-9_.-]+:", stripped):
            current_list = ""
        if stripped.startswith("network_mode:") and "host" in stripped:
            services[current]["network_mode"] = "host"
        elif stripped.startswith("ports:"):
            current_list = "ports"
        elif stripped.startswith("environment:"):
            current_list = "environment"
        elif current_list == "ports" and stripped.startswith("- "):
            services[current]["ports"].append(stripped[2:].strip())
        elif current_list == "environment" and stripped.startswith("- "):
            item = _strip_quote(stripped[2:].strip())
            if "=" in item:
                key, value = item.split("=", 1)
                services[current]["environment"][key.strip()] = _strip_quote(value)
        elif current_list == "environment" and ":" in stripped:
            key, value = stripped.split(":", 1)
            services[current]["environment"][key.strip()] = _strip_quote(value)
    return _rows_from_services(services, project_name, source, fallback_ips)


def parse_compose_ports(content, project_name, source, fallback_ips):
    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
            services = data.get("services") if isinstance(data, dict) else {}
            if isinstance(services, dict):
                return _rows_from_services(services, project_name, source, fallback_ips)
        except Exception:
            pass
    return _parse_compose_ports_fallback(content, project_name, source, fallback_ips)


def compose_files(apps_dir=None, compose_dir=None):
    app_root = Path(apps_dir or APPS_DIR)
    compose_root = Path(compose_dir or COMPOSE_DIR)
    files = []
    if app_root.is_dir():
        for path in sorted(app_root.glob("*/docker-compose.yml")):
            files.append((path.parent.name, "App", path))
    if compose_root.is_dir():
        for path in sorted(compose_root.glob("*")):
            if path.is_file() and path.suffix in (".yml", ".yaml"):
                files.append((path.stem, "Compose", path))
            elif path.is_dir():
                for name in ("docker-compose.yml", "compose.yml", "docker-compose.yaml", "compose.yaml"):
                    candidate = path / name
                    if candidate.is_file():
                        files.append((path.name, "Compose", candidate))
                        break
    return files


def list_ports(apps_dir=None, compose_dir=None):
    ips = host_ips()
    rows = []
    for project_name, source, path in compose_files(apps_dir, compose_dir):
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
    return {
        "ports": sorted(unique, key=lambda x: (x["ip"], x["port"], x["app"])),
        "ips": ips,
    }
