"""Helpers for reading Docker Compose files."""
import re

try:
    import yaml
except ImportError:
    yaml = None


WEB_TARGET_PORTS = {
    80, 443, 3000, 3001, 4200, 5000, 5173, 5601, 8000, 8008,
    8080, 8081, 8082, 8096, 8123, 8443, 8834, 9000, 9001, 9090,
    9443, 10000,
}

NON_WEB_PORTS = {
    21, 22, 25, 53, 110, 139, 143, 389, 445, 587, 636, 993, 995,
    1883, 2049, 2181, 3306, 33060, 4222, 5432, 5671, 5672, 6379,
    8883, 9092, 9300, 11211, 27017, 27018,
}

WEB_SERVICE_RE = re.compile(
    r"(web|www|ui|frontend|front|app|server|api|http|nginx|apache|caddy|"
    r"proxy|dashboard|homepage|admin)",
    re.I,
)
NON_WEB_SERVICE_RE = re.compile(
    r"(db|database|postgres|postgresql|mysql|mariadb|redis|mongo|mongodb|"
    r"rabbit|broker|kafka|zookeeper|memcached|mqtt)",
    re.I,
)


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def management_target_port(service_name, service):
    """Return an explicit management target, independently of host mappings."""
    explicit = _as_int(service.get("x-runvard-web-port"))
    if explicit and 0 < explicit <= 65535:
        return explicit
    image = str(service.get("image") or "").split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]
    name = image.lower() or str(service_name).lower()
    if name == "nginx-proxy-manager":
        return 81
    if name != "zoraxy":
        return None
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(str(item).split("=", 1) for item in env if "=" in str(item))
    port = _as_int(env.get("PORT")) if isinstance(env, dict) else None
    return port if port and 0 < port <= 65535 else 8000


def _compose_services(content):
    if yaml is not None:
        try:
            data = yaml.safe_load(content)
            services = data.get("services", {}) if isinstance(data, dict) else {}
            return services if isinstance(services, dict) else {}
        except Exception:
            pass
    return {}


def published_and_target(port_mapping):
    if isinstance(port_mapping, dict):
        return _as_int(port_mapping.get("published")), _as_int(port_mapping.get("target"))

    spec = str(port_mapping).strip().strip("\"'")
    base = spec.split("/", 1)[0]
    parts = base.rsplit(":", 2)
    if len(parts) == 1:
        return None, _as_int(parts[0])
    if len(parts) == 2:
        return _as_int(parts[0]), _as_int(parts[1])
    return _as_int(parts[-2]), _as_int(parts[-1])


def _compose_port_entries(content):
    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
        except Exception:
            data = {}
        services = data.get("services") if isinstance(data, dict) else {}
        if isinstance(services, dict):
            entries = []
            for service_name, service in services.items():
                if isinstance(service, dict) and isinstance(service.get("ports"), list):
                    entries.extend((str(service_name), port) for port in service["ports"])
            return entries

    entries = []
    current_service = ""
    in_ports = False
    service_indent = None
    ports_indent = None
    for raw in str(content or "").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        service_match = re.match(r"^([A-Za-z0-9_.-]+):\s*$", stripped)
        if service_match and indent >= 2 and (service_indent is None or indent <= service_indent):
            current_service = service_match.group(1)
            service_indent = indent
            in_ports = False
            continue
        if stripped == "ports:":
            in_ports = True
            ports_indent = indent
            continue
        if in_ports and indent <= (ports_indent or 0):
            in_ports = False
        if in_ports and stripped.startswith("- "):
            entries.append((current_service, stripped[2:].strip()))
    return entries


def _binding_candidate(name, service, entry, preferred_target):
    if isinstance(entry, dict):
        protocol = entry.get("protocol", "tcp")
        app_protocol = entry.get("app_protocol")
        address = str(entry.get("host_ip") or "")
    else:
        mapping, _, protocol = str(entry).partition("/")
        app_protocol = None
        address = mapping.rsplit(":", 2)[0] if mapping.count(":") >= 2 else ""
    published, target = published_and_target(entry)
    if str(protocol or "tcp").lower() != "tcp" or not published or not target:
        return None
    if not (0 < published <= 65535 and 0 < target <= 65535):
        return None

    management = management_target_port(name, service)
    if target == management:
        score = 1000
    elif target == preferred_target:
        score = 900
    elif app_protocol in ("http", "https"):
        score = 800
    elif target in NON_WEB_PORTS or published in NON_WEB_PORTS:
        return None
    else:
        score = 0
        if WEB_SERVICE_RE.search(name):
            score += 60
        if NON_WEB_SERVICE_RE.search(name):
            score -= 80
        if target in WEB_TARGET_PORTS:
            score += 50
        if published in WEB_TARGET_PORTS:
            score += 25
        if target in (80, 443):
            score += 20

    # Protocol follows the container port even when the host port is remapped.
    scheme = "https" if target in (443, 8443, 9443) else "http"
    image = str(service.get("image") or "").split("@")[0].rsplit("/", 1)[-1].split(":")[0]
    if image == "code-server" and target == 8443:
        scheme = "http"
    if image == "kasm" and target == 6901:
        scheme = "https"
    if app_protocol:
        scheme = app_protocol if app_protocol in ("http", "https") else ""
    if score <= 0 or (target in NON_WEB_PORTS and target != management and not app_protocol):
        scheme = ""
    if address.startswith("127.") or address.strip("[]") == "::1":
        scheme = ""
    return score, published, scheme


def best_web_endpoint_from_services(services, preferred_target=None, *, prefer_browser=True):
    """Return (published port, browser scheme) for resolved Compose services."""
    candidates = []
    for name, service in services.items():
        if not isinstance(service, dict) or not isinstance(service.get("ports"), list):
            continue
        for entry in service["ports"]:
            candidate = _binding_candidate(str(name), service, entry, preferred_target)
            if candidate and candidate[0] >= 0:
                candidates.append(candidate)
    if candidates:
        _, port, scheme = max(candidates, key=lambda item: (prefer_browser and bool(item[2]), item[0], item[1]))
        return port, scheme
    for name, service in services.items():
        if isinstance(service, dict) and service.get("network_mode") == "host":
            management = management_target_port(name, service)
            if management:
                return management, "http"
            if preferred_target:
                return preferred_target, "http"
    return 0, ""


def best_web_endpoint_from_compose(content, preferred_target=None, *, prefer_browser=True):
    services = _compose_services(content)
    if not services and yaml is None:
        for name, entry in _compose_port_entries(content):
            services.setdefault(name, {"ports": []})["ports"].append(entry)
    return best_web_endpoint_from_services(services, preferred_target, prefer_browser=prefer_browser)


def best_web_port_from_compose(content, fallback=0, preferred_target=None):
    port, _ = best_web_endpoint_from_compose(content, preferred_target, prefer_browser=False)
    return port or fallback


def published_ports_from_compose(content):
    """Return published host ports from a Docker Compose file."""
    ports = []
    for service_name, entry in _compose_port_entries(content):
        published, target = published_and_target(entry)
        if isinstance(entry, dict):
            host_ip = str(entry.get("host_ip") or "0.0.0.0")
            protocol = str(entry.get("protocol") or "tcp").lower()
        else:
            spec, _, protocol = str(entry).strip().strip("\"'").partition("/")
            parts = spec.rsplit(":", 2)
            host_ip = parts[0].strip("[]") if len(parts) == 3 else "0.0.0.0"
            protocol = (protocol or "tcp").lower()
        if published:
            ports.append({
                "service": service_name or "",
                "port": published,
                "target": target,
                "host_ip": host_ip or "0.0.0.0",
                "protocol": protocol,
            })
    return ports
