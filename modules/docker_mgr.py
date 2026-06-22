"""Docker: Container, Images, Volumes, Compose - volle Verwaltung."""
import json
import os
import re
import shutil
import socket
import subprocess

from modules.compose_utils import best_web_port_from_compose, published_ports_from_compose

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

_client = None
COMPOSE_DIR = "/opt/runvard/data/compose"


def _get_client():
    global _client
    if not HAS_DOCKER:
        raise RuntimeError("docker SDK nicht installiert")
    if _client is None:
        _client = docker.from_env()
    return _client


def available():
    if not HAS_DOCKER:
        return False
    try:
        _get_client().ping()
        return True
    except Exception:
        return False


# --- Container ---

def _container_labels(container):
    attrs = getattr(container, "attrs", {}) or {}
    config = attrs.get("Config", {}) or {}
    labels = config.get("Labels") or getattr(container, "labels", None) or {}
    return labels if isinstance(labels, dict) else {}


def _container_app_group(container):
    labels = _container_labels(container)
    compose_project = labels.get("com.docker.compose.project", "")
    compose_service = labels.get("com.docker.compose.service", "")
    if compose_project:
        name = compose_project
        if compose_service:
            name = f"{compose_project} ({compose_service})"
        return {
            "type": "compose",
            "id": f"compose:{compose_project}",
            "name": name,
            "project": compose_project,
            "service": compose_service,
        }
    return {
        "type": "container",
        "id": f"container:{container.name}",
        "name": container.name,
        "project": "",
        "service": "",
    }


def list_containers():
    client = _get_client()
    result = []
    for c in client.containers.list(all=True):
        ports = []
        for cport, host in (c.attrs["NetworkSettings"]["Ports"] or {}).items():
            if host:
                ports.append(f"{host[0]['HostPort']}:{cport}")
            else:
                ports.append(cport)
        result.append({
            "id": c.short_id,
            "name": c.name,
            "image": c.image.tags[0] if c.image.tags else c.image.short_id,
            "status": c.status,
            "state": c.attrs["State"]["Status"],
            "ports": ports,
            "nano_cpus": (c.attrs.get("HostConfig", {}) or {}).get("NanoCpus", 0) or 0,
            "mem_limit": (c.attrs.get("HostConfig", {}) or {}).get("Memory", 0) or 0,
            "app_group": _container_app_group(c),
        })
    return result


def container_action(container_id: str, action: str):
    client = _get_client()
    c = client.containers.get(container_id)
    if action == "start":
        c.start()
    elif action == "stop":
        c.stop()
    elif action == "restart":
        c.restart()
    elif action == "remove":
        c.remove(force=True)
        return {"ok": True, "removed": True}
    else:
        raise ValueError("Unbekannte Aktion")
    c.reload()
    return {"ok": True, "status": c.status}


def container_stats(container_id: str):
    """Momentaufnahme der Ressourcennutzung eines Containers."""
    client = _get_client()
    c = client.containers.get(container_id)
    s = c.stats(stream=False)
    cpu_pct = 0.0
    try:
        cpu, pre = s["cpu_stats"], s["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or [1]) or 1
        if sys_delta > 0 and cpu_delta > 0:
            cpu_pct = (cpu_delta / sys_delta) * ncpu * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        cpu_pct = 0.0
    mem = s.get("memory_stats", {}) or {}
    cache = (mem.get("stats", {}) or {}).get("cache", 0)
    mem_used = max(0, mem.get("usage", 0) - cache)
    mem_limit = mem.get("limit", 0)
    net = s.get("networks", {}) or {}
    return {
        "cpu_percent": round(cpu_pct, 1),
        "mem_used": mem_used,
        "mem_limit": mem_limit,
        "mem_percent": round(mem_used / mem_limit * 100, 1) if mem_limit else 0,
        "net_rx": sum(n.get("rx_bytes", 0) for n in net.values()),
        "net_tx": sum(n.get("tx_bytes", 0) for n in net.values()),
    }


def create_container(image, name="", ports="", volumes="", env="", restart="no",
                     cpus="", memory=""):
    """Container erstellen & starten.

    ports:   "8080:80,8443:443"
    volumes: "/host/path:/container/path,..."
    env:     "KEY=val,KEY2=val2"
    cpus:    CPU-Limit als Dezimalzahl, z. B. "1.5" (0/leer = unbegrenzt)
    memory:  RAM-Limit, z. B. "512m" oder "2g" (leer = unbegrenzt)
    """
    client = _get_client()

    port_map = {}
    for p in filter(None, ports.split(",")):
        if ":" in p:
            host_p, cont_p = p.split(":")
            port_map[f"{cont_p}/tcp"] = int(host_p)

    vol_map = {}
    for v in filter(None, volumes.split(",")):
        if ":" in v:
            host_v, cont_v = v.split(":", 1)
            vol_map[host_v] = {"bind": cont_v, "mode": "rw"}

    env_list = [e for e in env.split(",") if "=" in e]

    extra = {}
    try:
        if cpus and float(cpus) > 0:
            extra["nano_cpus"] = int(float(cpus) * 1_000_000_000)
    except ValueError:
        pass
    if memory and memory.strip():
        extra["mem_limit"] = memory.strip()

    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        client.images.pull(image)

    c = client.containers.run(
        image, name=name or None, detach=True,
        ports=port_map or None, volumes=vol_map or None,
        environment=env_list or None,
        restart_policy={"Name": restart} if restart != "no" else None,
        **extra,
    )
    return {"ok": True, "id": c.short_id, "name": c.name}


def update_container(container_id, cpus="", memory=""):
    """CPU-/RAM-Limits eines laufenden Containers ändern (docker update).

    cpus:   Dezimalzahl wie "1.5"; "0" hebt das CPU-Limit auf; leer = unverändert.
    memory: z. B. "512m"/"2g"; leer = unverändert.
    """
    client = _get_client()
    c = client.containers.get(container_id)
    kwargs = {}
    if cpus not in ("", None):
        try:
            val = float(cpus)
        except ValueError:
            return {"ok": False, "stderr": "Ungueltiger CPU-Wert"}
        period = 100000
        kwargs["cpu_period"] = period
        kwargs["cpu_quota"] = int(val * period) if val > 0 else -1
    if memory and memory.strip():
        kwargs["mem_limit"] = memory.strip()
    if not kwargs:
        return {"ok": False, "stderr": "Keine Änderung angegeben"}
    c.update(**kwargs)
    return {"ok": True}


def search_images(term, limit=25):
    """Docker-Registry (Docker Hub) nach Images durchsuchen."""
    client = _get_client()
    try:
        results = client.images.search(term)
    except Exception as e:
        return {"ok": False, "results": [], "stderr": str(e)}
    out = []
    for r in results[:limit]:
        out.append({
            "name": r.get("name", ""),
            "description": (r.get("description", "") or "")[:120],
            "stars": r.get("star_count", 0),
            "official": bool(r.get("is_official")),
        })
    out.sort(key=lambda x: x["stars"], reverse=True)
    return {"ok": True, "results": out}


def container_logs(container_id, tail=100):
    client = _get_client()
    c = client.containers.get(container_id)
    return {"logs": c.logs(tail=tail).decode(errors="replace")}


# --- Images ---

def list_images():
    client = _get_client()
    return [{
        "id": img.short_id,
        "tags": img.tags,
        "size": img.attrs["Size"],
    } for img in client.images.list()]


def pull_image(name):
    client = _get_client()
    client.images.pull(name)
    return {"ok": True}


def remove_image(image_id):
    client = _get_client()
    client.images.remove(image_id, force=True)
    return {"ok": True}


# --- Volumes ---

def list_volumes():
    client = _get_client()
    return [{"name": v.name, "driver": v.attrs.get("Driver"),
             "mountpoint": v.attrs.get("Mountpoint")}
            for v in client.volumes.list()]


def remove_volume(name):
    client = _get_client()
    v = client.volumes.get(name)
    v.remove(force=True)
    return {"ok": True}


# --- Networks ---

_NETWORK_NOT_FOUND_RE = re.compile(
    r"(no such network|network .* not found|not found|not find|did not find)",
    re.I,
)


def _network_not_found(text):
    return bool(_NETWORK_NOT_FOUND_RE.search(str(text or "")))


def is_network_not_found_error(text):
    return _network_not_found(text)


def _normalize_network(attrs):
    ipam = attrs.get("IPAM", {}) or {}
    configs = ipam.get("Config", []) or []
    containers = attrs.get("Containers") or {}
    name = attrs.get("Name") or ""
    network_id = attrs.get("Id") or attrs.get("ID") or ""
    builtin = name in ("bridge", "host", "none")
    return {
        "id": (network_id or "")[:12],
        "name": name,
        "driver": attrs.get("Driver") or "",
        "scope": attrs.get("Scope") or "",
        "internal": bool(attrs.get("Internal")),
        "attachable": bool(attrs.get("Attachable")),
        "containers": len(containers),
        "subnets": [c.get("Subnet", "") for c in configs if c.get("Subnet")],
        "gateways": [c.get("Gateway", "") for c in configs if c.get("Gateway")],
        "builtin": builtin,
        "unused": not builtin and len(containers) == 0,
    }


def _list_networks_sdk():
    client = _get_client()
    networks = []
    for net in client.networks.list():
        attrs = getattr(net, "attrs", {}) or {}
        if not attrs:
            attrs = {
                "Name": getattr(net, "name", ""),
                "Id": getattr(net, "id", ""),
            }
        networks.append(_normalize_network(attrs))
    return sorted(networks, key=lambda n: (not n["builtin"], n["name"]))


def _list_networks_cli():
    listed = subprocess.run(
        ["docker", "network", "ls", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if listed.returncode != 0:
        raise RuntimeError((listed.stderr or listed.stdout).strip() or "docker network ls failed")
    networks = []
    for line in listed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = row.get("Name") or row.get("ID") or ""
        if not name:
            continue
        inspected = subprocess.run(
            ["docker", "network", "inspect", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if inspected.returncode != 0:
            output = (inspected.stdout or "") + (inspected.stderr or "")
            if _network_not_found(output):
                continue
            raise RuntimeError(output.strip() or f"docker network inspect failed: {name}")
        try:
            data = json.loads(inspected.stdout or "[]")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Could not parse Docker network inspect output: {e}") from e
        if data:
            networks.append(_normalize_network(data[0] or {}))
    return sorted(networks, key=lambda n: (not n["builtin"], n["name"]))


def list_networks():
    try:
        return _list_networks_sdk()
    except Exception as e:
        if _network_not_found(e):
            return _list_networks_cli()
        raise


def prune_networks():
    deleted = []
    skipped = []
    errors = []
    try:
        networks = list_networks()
    except Exception as e:
        if _network_not_found(e):
            return {"ok": True, "deleted": deleted, "deleted_count": 0, "skipped": skipped}
        raise
    for network in networks:
        if not network.get("unused"):
            continue
        name = network.get("name") or network.get("id")
        if not name:
            continue
        result = subprocess.run(
            ["docker", "network", "rm", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode == 0:
            deleted.append(name)
            continue
        if _network_not_found(output):
            skipped.append(name)
            continue
        errors.append(output or f"Could not remove Docker network {name}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return {
        "ok": True,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "skipped": skipped,
    }


# --- Docker Compose ---

def list_compose_projects():
    os.makedirs(COMPOSE_DIR, exist_ok=True)
    projects = []
    for name in os.listdir(COMPOSE_DIR):
        path = os.path.join(COMPOSE_DIR, name)
        compose_file = os.path.join(path, "docker-compose.yml")
        if os.path.isfile(compose_file):
            projects.append({
                "name": name,
                "running": _compose_running(path),
                "port": _compose_port(compose_file),
            })
    return projects


def _compose_port(compose_file):
    try:
        with open(compose_file) as f:
            return best_web_port_from_compose(f.read())
    except Exception:
        pass
    return 0


def _compose_running(path):
    try:
        r = subprocess.run(["docker", "compose", "ps", "--status", "running", "-q"],
                           cwd=path, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            r = subprocess.run(["docker", "compose", "ps", "-q"],
                               cwd=path, capture_output=True, text=True,
                               timeout=15)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _compose_project_path(name):
    safe = os.path.basename(str(name or "").strip())
    if not safe or safe in (".", "..") or safe != str(name or "").strip():
        raise ValueError("Ungültiger Compose-Projektname")
    return os.path.join(COMPOSE_DIR, safe)


def save_compose(name, content, env_enabled=False, env_content=""):
    path = _compose_project_path(name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "docker-compose.yml"), "w") as f:
        f.write(content)
    env_path = os.path.join(path, ".env")
    if env_enabled:
        with open(env_path, "w") as f:
            f.write(env_content or "")
    else:
        try:
            os.remove(env_path)
        except FileNotFoundError:
            pass
    return {"ok": True}


def get_compose(name):
    path = _compose_project_path(name)
    compose_path = os.path.join(path, "docker-compose.yml")
    env_path = os.path.join(path, ".env")
    data = {"content": "", "env_enabled": False, "env_content": ""}
    try:
        with open(compose_path) as f:
            data["content"] = f.read()
    except OSError:
        pass
    try:
        with open(env_path) as f:
            data["env_content"] = f.read()
            data["env_enabled"] = True
    except OSError:
        pass
    return data


def _port_is_available(port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if port <= 0 or port > 65535:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


def _compose_owned_ports(name):
    ports = set()
    if not HAS_DOCKER:
        return ports
    try:
        client = _get_client()
        containers = client.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={name}"},
        )
    except Exception:
        return ports
    for container in containers:
        network = (getattr(container, "attrs", {}) or {}).get("NetworkSettings", {})
        mappings = network.get("Ports", {}) or {}
        for bindings in mappings.values():
            for binding in bindings or []:
                try:
                    ports.add(int(binding.get("HostPort")))
                except (TypeError, ValueError):
                    continue
    return ports


def check_compose_ports(name, content):
    entries = published_ports_from_compose(content)
    owned = _compose_owned_ports(name)
    counts = {}
    for entry in entries:
        counts[entry["port"]] = counts.get(entry["port"], 0) + 1

    conflicts = []
    for entry in entries:
        port = entry["port"]
        if counts.get(port, 0) > 1:
            conflicts.append({**entry, "reason": "duplicate"})
        elif port in owned:
            continue
        elif not _port_is_available(port):
            conflicts.append({**entry, "reason": "in_use"})

    return {"ok": not conflicts, "ports": entries, "conflicts": conflicts}


def compose_action(name, action):
    path = _compose_project_path(name)
    cmd_map = {
        "up": ["docker", "compose", "up", "-d"],
        "stop": ["docker", "compose", "stop"],
        "down": ["docker", "compose", "down"],
        "restart": ["docker", "compose", "restart"],
    }
    if action not in cmd_map:
        raise ValueError("Unbekannte Aktion")
    r = subprocess.run(cmd_map[action], cwd=path, capture_output=True,
                       text=True, timeout=300)
    return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}


def remove_compose_project(name):
    """
    Stop and remove a saved Compose project.

    Args:
    -----
        name (str):
            Compose project directory name.

    Returns:
    --------
        dict[str, bool]:
            Contains the deletion result.

    Raises:
    -------
        ValueError:
            Raised when the project name or path is unsafe.
    """
    if not name or name in (".", "..") or os.path.basename(name) != name:
        raise ValueError("Invalid Compose project name")
    root = os.path.abspath(COMPOSE_DIR)
    path = os.path.abspath(os.path.join(root, name))
    if path == root or not path.startswith(root + os.sep):
        raise ValueError("Invalid Compose project path")
    if not os.path.isdir(path):
        return {"ok": True}
    subprocess.run(["docker", "compose", "down"], cwd=path,
                   capture_output=True, text=True, timeout=300)
    shutil.rmtree(path)
    return {"ok": True}
