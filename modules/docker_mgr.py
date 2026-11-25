"""Docker: Container, Images, Volumes, Compose - volle Verwaltung."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile

from modules.compose_utils import best_web_endpoint_from_compose, best_web_endpoint_from_services, published_ports_from_compose
from modules import compose_sources

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
        resources = c.attrs.get("HostConfig") or {}
        limit = resources.get("NanoCpus") or 0
        quota = resources.get("CpuQuota") or 0
        if not limit and quota > 0:
            limit = round(quota * 1_000_000_000 / (resources.get("CpuPeriod") or 100000))
        result.append({
            "id": c.short_id,
            "name": c.name,
            "image": c.image.tags[0] if c.image.tags else c.image.short_id,
            "status": c.status,
            "state": c.attrs["State"]["Status"],
            "ports": ports,
            "nano_cpus": limit,
            "nano_limit_mode": bool(resources.get("NanoCpus")),
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


def _container_stats_snapshot(container):
    s = container.stats(stream=False)
    cpu_pct = 0.0
    cpu_count = 1
    try:
        cpu, pre = s["cpu_stats"], s["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        cpu_count = (
            cpu.get("online_cpus")
            or len(cpu["cpu_usage"].get("percpu_usage") or [1])
            or 1
        )
        if sys_delta > 0 and cpu_delta > 0:
            cpu_pct = (cpu_delta / sys_delta) * cpu_count * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        cpu_pct = 0.0
    mem = s.get("memory_stats", {}) or {}
    cache = (mem.get("stats", {}) or {}).get("cache", 0)
    mem_used = max(0, mem.get("usage", 0) - cache)
    mem_limit = mem.get("limit", 0)
    net = s.get("networks", {}) or {}
    return {
        "cpu_percent": round(cpu_pct, 1),
        "cpu_count": cpu_count,
        "mem_used": mem_used,
        "mem_limit": mem_limit,
        "mem_percent": round(mem_used / mem_limit * 100, 1) if mem_limit else 0,
        "net_rx": sum(n.get("rx_bytes", 0) for n in net.values()),
        "net_tx": sum(n.get("tx_bytes", 0) for n in net.values()),
    }


def container_stats(container_id: str):
    """Momentaufnahme der Ressourcennutzung eines Containers."""
    client = _get_client()
    return _container_stats_snapshot(client.containers.get(container_id))


def list_container_stats():
    """Ressourcennutzung aller laufenden Container in einem Request."""
    containers = _get_client().containers.list(filters={"status": "running"})
    if not containers:
        return {}

    results = {}
    worker_count = min(8, len(containers))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        pending = {
            pool.submit(_container_stats_snapshot, container): container.short_id
            for container in containers
        }
        for future in as_completed(pending):
            container_id = pending[future]
            try:
                results[container_id] = future.result()
            except Exception as exc:
                results[container_id] = {"error": str(exc)}
    return results


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
            extra["cpu_period"] = 100000
            extra["cpu_quota"] = int(float(cpus) * extra["cpu_period"])
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
        if not math.isfinite(val) or val < 0:
            return {"ok": False, "stderr": "Ungueltiger CPU-Wert"}
        if c.attrs["HostConfig"].get("NanoCpus"):
            # Docker rejects CFS quota updates on containers created with NanoCPUs.
            # The pinned Python SDK does not expose NanoCPUs on update_container().
            if val == 0:
                # The engine ignores NanoCpus=0 on updates to an existing container.
                return {"ok": False, "error_code": "recreateForUnlimited"}
            command = ["docker", "update", "--cpus", str(val)]
            if memory and memory.strip():
                command.extend(["--memory", memory.strip()])
            result = subprocess.run([*command, c.id], capture_output=True, text=True, timeout=30)
            return {"ok": result.returncode == 0, "stderr": result.stderr.strip()}
        period = c.attrs["HostConfig"].get("CpuPeriod") or 100000
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
    r"(no such network|network .* not found|not found|not find|did not find|did not found)",
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

def _compose_containers():
    try:
        return _get_client().containers.list(all=True)
    except Exception:
        return []


def _compose_location(name, allow_new=False):
    standalone = str(name).startswith("container:")
    safe_name = "container-" + name.split(":", 1)[1] if standalone else name
    path = _compose_project_path(safe_name)
    containers, files, directory = compose_sources.discover(_compose_containers(), name)
    if files:
        return path, containers, files, directory or os.path.dirname(files[0])
    # App-store installations and saved manual projects can exist without containers.
    for root in (COMPOSE_DIR, os.path.join(os.path.dirname(COMPOSE_DIR), "apps")):
        candidate = os.path.join(root, safe_name)
        for filename in ("docker-compose.yml", "compose.yaml", "compose.yml", "docker-compose.yaml"):
            source = os.path.join(candidate, filename)
            if os.path.isfile(source):
                return candidate, containers, [source], candidate
    if containers or allow_new:
        return path, containers, [], path
    raise ValueError("Compose project not found")


def list_compose_projects():
    projects = {}
    if os.path.isdir(COMPOSE_DIR):
        for name in os.listdir(COMPOSE_DIR):
            path = os.path.join(COMPOSE_DIR, name)
            compose_file = os.path.join(path, "docker-compose.yml")
            if os.path.isfile(compose_file):
                port, scheme = _compose_endpoint(compose_file)
                projects[name] = {"name": name, "running": _compose_running(path),
                                  "port": port, "web_scheme": scheme}

    services_by_project = {}
    for container in _compose_containers():
        labels = _container_labels(container)
        name = labels.get("com.docker.compose.project")
        if not name:
            continue
        entry = projects.setdefault(name, {"name": name, "running": False, "port": 0, "web_scheme": ""})
        entry["running"] = entry["running"] or bool(container.attrs.get("State", {}).get("Running"))
        config = container.attrs.get("Config") or {}
        host = container.attrs.get("HostConfig") or {}
        bindings = (container.attrs.get("NetworkSettings") or {}).get("Ports") or host.get("PortBindings") or {}
        ports = []
        for container_port, values in bindings.items():
            target, _, protocol = container_port.partition("/")
            for binding in values or []:
                ports.append({"published": binding.get("HostPort"), "target": target,
                              "protocol": protocol or "tcp", "host_ip": binding.get("HostIp")})
                if not entry["port"] and str(binding.get("HostPort") or "").isdigit():
                    entry["port"] = int(binding["HostPort"])
        services = services_by_project.setdefault(name, {})
        service = services.setdefault(labels.get("com.docker.compose.service") or container.name, {
            "image": config.get("Image"), "environment": config.get("Env"),
            "network_mode": host.get("NetworkMode"), "ports": [],
        })
        service["ports"].extend(ports)
    for name, services in services_by_project.items():
        if not projects[name]["web_scheme"]:
            port, scheme = best_web_endpoint_from_services(services)
            if scheme:
                projects[name].update(port=port, web_scheme=scheme)
    return list(projects.values())


def _compose_endpoint(compose_file):
    try:
        with open(compose_file) as f:
            return best_web_endpoint_from_compose(f.read())
    except OSError:
        return 0, ""


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


def convert_compose(content=None, document=None, yaml_tags=None):
    return compose_sources.convert(content, document, yaml_tags)


def save_compose(name, content, env_enabled=False, env_content="", source_contents=None):
    path, containers, files, directory = _compose_location(name, allow_new=True)
    contents = source_contents if source_contents is not None else [content]
    if not isinstance(contents, list) or not contents or any(not isinstance(c, str) for c in contents):
        raise ValueError("Invalid Compose source contents")
    if files and len(contents) != len(files):
        raise ValueError("Compose source files changed; reload before saving")
    # Validate every source before writing any file. Source paths come only from
    # Docker metadata or the application's fixed storage roots, never the request.
    for source in contents:
        convert_compose(content=source)
    if not files:
        os.makedirs(path, exist_ok=True)
        files = [os.path.join(path, "docker-compose.yml")]
        directory = path
    changes = dict(zip(files, contents))
    changes[os.path.join(directory, ".env")] = env_content or "" if env_enabled else None
    compose_sources.write_sources(changes)
    return {"ok": True}


def get_compose(name):
    path, containers, files, directory = _compose_location(name)
    sources = []
    if files:
        for filename in files:
            try:
                with open(filename) as f:
                    content = f.read()
                parsed = convert_compose(content=content)
                sources.append({"path": filename, "content": content, "document": parsed["document"], "tags": parsed["tags"]})
            except (OSError, ValueError) as exc:
                raise ValueError(f"Cannot load Compose source {filename}: {exc}") from exc
    else:
        parsed = convert_compose(document=json.dumps(compose_sources.runtime_document(containers)))
        sources.append({"path": os.path.join(path, "docker-compose.yml"), **parsed})
    data = {"content": sources[0]["content"], "sources": sources,
            "reconstructed": not bool(files), "env_enabled": False, "env_content": ""}
    env_path = os.path.join(directory, ".env")
    try:
        with open(env_path) as f:
            data["env_content"] = f.read()
            data["env_enabled"] = True
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(f"Cannot load environment file {env_path}: {exc}") from exc
    return data


def _port_is_available(port, host_ip="0.0.0.0", protocol="tcp"):
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if port <= 0 or port > 65535:
        return False
    try:
        family = socket.AF_INET6 if ":" in host_ip else socket.AF_INET
        kind = socket.SOCK_DGRAM if protocol == "udp" else socket.SOCK_STREAM
        with socket.socket(family, kind) as sock:
            sock.bind((host_ip, port))
        return True
    except OSError:
        return False


def _compose_owned_ports(name):
    ports = set()
    if not HAS_DOCKER:
        return ports
    try:
        client = _get_client()
        if name.startswith("container:"):
            containers = [c for c in client.containers.list(all=True)
                          if c.name == name.split(":", 1)[1]]
        else:
            containers = client.containers.list(
                all=True,
                filters={"label": f"com.docker.compose.project={name}"},
            )
    except Exception:
        return ports
    for container in containers:
        network = (getattr(container, "attrs", {}) or {}).get("NetworkSettings", {})
        mappings = network.get("Ports", {}) or {}
        for target, bindings in mappings.items():
            protocol = target.partition("/")[2] or "tcp"
            for binding in bindings or []:
                try:
                    ports.add((int(binding.get("HostPort")), protocol,
                               binding.get("HostIp") or "0.0.0.0"))
                except (TypeError, ValueError):
                    continue
    return ports


def _bindings_overlap(left, right):
    if left["port"] != right["port"] or left["protocol"] != right["protocol"]:
        return False
    left_ip, right_ip = left["host_ip"], right["host_ip"]
    if left_ip == right_ip or "::" in (left_ip, right_ip):
        return True
    return (":" not in left_ip and ":" not in right_ip
            and "0.0.0.0" in (left_ip, right_ip))


def check_compose_ports(name, content):
    entries = published_ports_from_compose(content)
    owned = _compose_owned_ports(name)
    conflicts = []
    for index, entry in enumerate(entries):
        binding = (entry["port"], entry["protocol"], entry["host_ip"])
        if any(_bindings_overlap(entry, other)
               for other_index, other in enumerate(entries) if other_index != index):
            conflicts.append({**entry, "reason": "duplicate"})
        elif binding in owned:
            continue
        elif not _port_is_available(entry["port"], entry["host_ip"], entry["protocol"]):
            conflicts.append({**entry, "reason": "in_use"})
    return {"ok": not conflicts, "ports": entries, "conflicts": conflicts}


def check_compose_source_ports(name, contents, env_enabled=False, env_content=""):
    """Validate the effective project, including overrides and .env interpolation."""
    path, containers, files, directory = _compose_location(name, allow_new=True)
    for content in contents:
        convert_compose(content=content)
    with tempfile.TemporaryDirectory(prefix="runvard-compose-check-") as temporary:
        project_directory = directory if os.path.isdir(directory) else temporary
        env_path = os.path.join(temporary, ".env")
        with open(env_path, "w") as f:
            f.write(env_content if env_enabled else "")
        project = "container-" + name.split(":", 1)[1] if name.startswith("container:") else name
        cmd = ["docker", "compose", "-p", project, "--project-directory", project_directory,
               "--env-file", env_path]
        for index, content in enumerate(contents):
            filename = os.path.join(temporary, f"source-{index}.yaml")
            with open(filename, "w") as f:
                f.write(content)
            cmd.extend(["-f", filename])
        cmd.extend(["config", "--format", "json"])
        try:
            result = subprocess.run(cmd, cwd=project_directory, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Compose validation could not be completed") from exc
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Invalid Compose configuration")
        return check_compose_ports(name, result.stdout)


def compose_action(name, action):
    path, containers, files, directory = _compose_location(name)
    if action not in ("up", "stop", "down", "restart"):
        raise ValueError("Unbekannte Aktion")
    if not files:
        raise ValueError("Save the Compose configuration before starting this project")
    project = "container-" + name.split(":", 1)[1] if name.startswith("container:") else name
    cmd = ["docker", "compose", "-p", project]
    for filename in files:
        cmd.extend(["-f", filename])
    cmd.extend([action, "-d"] if action == "up" else [action])
    r = subprocess.run(cmd, cwd=directory, capture_output=True, text=True, timeout=300)
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
    result = subprocess.run(["docker", "compose", "-p", name, "down"], cwd=path,
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        return {"ok": False, "stderr": result.stderr.strip() or result.stdout.strip()}
    shutil.rmtree(path)
    return {"ok": True}
