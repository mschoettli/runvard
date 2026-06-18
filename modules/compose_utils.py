"""Helpers for reading Docker Compose files."""
import re

from modules import validators

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
ALLOWED_READONLY_BINDS = {"/etc/localtime", "/etc/timezone", "/proc", "/sys"}


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


def _validate_port_mapping(port_mapping):
    published, target = _published_and_target(port_mapping)
    for port in (published, target):
        if port is not None and not (1 <= port <= 65535):
            raise ValueError("Compose port outside valid range")


def _bind_mode_is_readonly(mode):
    return str(mode or "").lower() in {"ro", "readonly", "read_only", "ro,z", "ro,Z"}


def _validate_bind_source(source, readonly=False, allow_docker_socket=False):
    source = str(source or "").strip()
    if not source:
        return
    if source.startswith(("./", "../")):
        return
    if not source.startswith("/"):
        return
    original = source.rstrip("/") or "/"
    if (
        allow_docker_socket
        and validators.real_path(original) == validators.real_path("/var/run/docker.sock")
    ):
        return
    if original in ALLOWED_READONLY_BINDS:
        if readonly:
            return
        raise PermissionError("Host path must be mounted read-only")
    if validators.is_under(original, validators.SENSITIVE_HOST_PATHS | validators.BLOCKED_PATHS):
        raise PermissionError("Sensitive host path cannot be mounted")
    resolved = validators.real_path(source)
    if resolved in ALLOWED_READONLY_BINDS and readonly:
        return
    validators.guard_host_mount(resolved)


def _validate_volume_entry(volume, allow_docker_socket=False):
    if isinstance(volume, dict):
        if str(volume.get("type", "")).lower() not in {"", "bind"}:
            return
        source = volume.get("source") or volume.get("src") or volume.get("host")
        readonly = bool(volume.get("read_only")) or _bind_mode_is_readonly(volume.get("mode"))
        _validate_bind_source(
            source,
            readonly=readonly,
            allow_docker_socket=allow_docker_socket,
        )
        return

    spec = str(volume or "").strip().strip("\"'")
    if not spec:
        return
    parts = spec.split(":")
    if not parts:
        return
    source = parts[0]
    mode = parts[2] if len(parts) >= 3 else ""
    _validate_bind_source(
        source,
        readonly=_bind_mode_is_readonly(mode),
        allow_docker_socket=allow_docker_socket,
    )


def _compose_service_entries(content):
    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
        except Exception as exc:
            raise ValueError(f"Invalid Compose YAML: {exc}") from exc
        services = data.get("services") if isinstance(data, dict) else {}
        if not isinstance(services, dict) or not services:
            raise ValueError("Compose file must define services")
        return services
    return None


def validate_compose_content(content, allow_docker_socket=False):
    """
    Validate high-risk Docker Compose fields before writing or running them.

    The validator focuses on host-impacting settings: bind mounts and published
    ports. It allows relative project volumes and named Docker volumes.
    """
    services = _compose_service_entries(content)
    if services is not None:
        for service in services.values():
            if not isinstance(service, dict):
                continue
            for volume in service.get("volumes") or []:
                _validate_volume_entry(
                    volume,
                    allow_docker_socket=allow_docker_socket,
                )
            for port in service.get("ports") or []:
                _validate_port_mapping(port)
        return True

    def clean_scalar(value):
        return str(value or "").strip().strip("\"'")

    def flush_pending():
        nonlocal pending_kind, pending_entry
        if not pending_entry:
            return
        if pending_kind == "volumes":
            _validate_volume_entry(
                pending_entry,
                allow_docker_socket=allow_docker_socket,
            )
        elif pending_kind == "ports":
            _validate_port_mapping(pending_entry)
        pending_kind = ""
        pending_entry = {}

    service_seen = False
    in_volumes = False
    in_ports = False
    section_indent = 0
    pending_kind = ""
    pending_entry = {}
    for raw in str(content or "").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped == "volumes:":
            flush_pending()
            in_volumes = True
            in_ports = False
            section_indent = indent
        elif stripped == "ports:":
            flush_pending()
            in_ports = True
            in_volumes = False
            section_indent = indent
        elif re.match(r"^[A-Za-z0-9_.-]+:\s*$", stripped) and indent == 2:
            flush_pending()
            service_seen = True
            in_volumes = False
            in_ports = False
        elif indent <= section_indent:
            flush_pending()
            in_volumes = False
            in_ports = False
        elif stripped.startswith("- "):
            flush_pending()
            item = stripped[2:].strip()
            if (in_volumes or in_ports) and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", item):
                key, value = item.split(":", 1)
                pending_kind = "volumes" if in_volumes else "ports"
                pending_entry = {key.strip(): clean_scalar(value)}
                continue
            if in_volumes:
                _validate_volume_entry(
                    item,
                    allow_docker_socket=allow_docker_socket,
                )
            if in_ports:
                _validate_port_mapping(item)
        elif pending_entry and indent > section_indent:
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", stripped)
            if match:
                pending_entry[match.group(1)] = clean_scalar(match.group(2))
    flush_pending()
    if not service_seen:
        raise ValueError("Compose file must define services")
    return True


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
