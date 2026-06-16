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


def _published_and_target(port_mapping):
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


def best_web_port_from_compose(content, fallback=0):
    candidates = []
    for service_name, entry in _compose_port_entries(content):
        published, target = _published_and_target(entry)
        if not published:
            continue
        if published in NON_WEB_PORTS or target in NON_WEB_PORTS:
            continue
        score = 0
        if WEB_SERVICE_RE.search(service_name or ""):
            score += 60
        if NON_WEB_SERVICE_RE.search(service_name or ""):
            score -= 80
        if target in WEB_TARGET_PORTS:
            score += 50
        if published in WEB_TARGET_PORTS:
            score += 25
        if target in (80, 443):
            score += 20
        candidates.append((score, published))
    if not candidates:
        return fallback
    candidates.sort(reverse=True)
    return candidates[0][1] if candidates[0][0] >= 0 else fallback
