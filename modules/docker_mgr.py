"""Docker: Container, Images, Volumes, Compose - volle Verwaltung."""
import os
import shutil
import subprocess

from modules.compose_utils import best_web_port_from_compose, validate_compose_content
from modules.runtime import data_path
from modules import validators

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

_client = None
COMPOSE_DIR = data_path("compose")


class DockerUnavailable(RuntimeError):
    """Raised when the Docker SDK or daemon is not reachable."""


def _docker_error(exc):
    return {"ok": False, "stderr": str(exc)}


def _empty_container_stats(error=""):
    return {
        "ok": False,
        "stderr": str(error or ""),
        "cpu_percent": 0,
        "mem_used": 0,
        "mem_limit": 0,
        "mem_percent": 0,
        "net_rx": 0,
        "net_tx": 0,
    }


def _require_container_ref(value: str) -> str:
    return validators.require_slug(str(value or "").strip(), "container")


def _get_client():
    global _client
    if not HAS_DOCKER:
        raise DockerUnavailable("docker SDK nicht installiert")
    if not os.environ.get("DOCKER_HOST") and not os.path.exists("/var/run/docker.sock"):
        raise DockerUnavailable("Docker-Socket nicht gefunden")
    if _client is None:
        try:
            client = docker.from_env()
            client.ping()
            _client = client
        except Exception as exc:
            if "client" in locals():
                close = getattr(client, "close", None)
                if close:
                    close()
            _client = None
            raise DockerUnavailable(f"Docker ist nicht erreichbar: {exc}") from exc
    return _client


def close_client():
    """Close the cached Docker SDK client if it was opened."""
    global _client
    client = _client
    _client = None
    if client is not None:
        close = getattr(client, "close", None)
        if close:
            close()


def available():
    if not HAS_DOCKER:
        return False
    try:
        _get_client().ping()
        return True
    except Exception:
        return False


# --- Container ---

def list_containers():
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return {"ok": False, "containers": [], "stderr": str(exc)}
    result = []
    try:
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
            })
    except Exception as exc:
        return {"ok": False, "containers": [], "stderr": str(exc)}
    return {"ok": True, "containers": result}


def container_action(container_id: str, action: str):
    try:
        container_id = _require_container_ref(container_id)
    except ValueError as exc:
        return _docker_error(exc)
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return _docker_error(exc)
    try:
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
            return {"ok": False, "stderr": "Unbekannte Aktion"}
        c.reload()
        return {"ok": True, "status": c.status}
    except Exception as exc:
        return _docker_error(exc)


def container_stats(container_id: str):
    """Momentaufnahme der Ressourcennutzung eines Containers."""
    _require_container_ref(container_id)
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return _empty_container_stats(exc)
    try:
        c = client.containers.get(container_id)
        s = c.stats(stream=False)
    except Exception as exc:
        return _empty_container_stats(exc)
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
    try:
        image = validators.require_docker_ref(image, "image")
        if name:
            name = validators.require_slug(name, "container name")
    except ValueError as exc:
        return _docker_error(exc)
    if restart not in ("no", "always", "unless-stopped", "on-failure"):
        return {"ok": False, "stderr": "Ungueltige Restart-Policy"}

    port_map = {}
    for p in filter(None, ports.split(",")):
        if ":" in p:
            host_p, cont_p = p.split(":", 1)
            try:
                host_port = int(host_p)
                container_port = int(cont_p.split("/", 1)[0])
            except ValueError:
                return {"ok": False, "stderr": "Ungueltiger Port"}
            if not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
                return {"ok": False, "stderr": "Ungueltiger Port"}
            proto = "udp" if cont_p.endswith("/udp") else "tcp"
            port_map[f"{container_port}/{proto}"] = host_port

    vol_map = {}
    for v in filter(None, volumes.split(",")):
        if ":" in v:
            host_v, cont_v = v.split(":", 1)
            try:
                host_v = validators.guard_host_mount(host_v)
            except (PermissionError, ValueError) as exc:
                return {"ok": False, "stderr": str(exc)}
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
        client = _get_client()
    except DockerUnavailable as exc:
        return _docker_error(exc)

    try:
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
    except Exception as exc:
        return _docker_error(exc)


def update_container(container_id, cpus="", memory=""):
    """CPU-/RAM-Limits eines laufenden Containers ändern (docker update).

    cpus:   Dezimalzahl wie "1.5"; "0" hebt das CPU-Limit auf; leer = unverändert.
    memory: z. B. "512m"/"2g"; leer = unverändert.
    """
    try:
        container_id = _require_container_ref(container_id)
    except ValueError as exc:
        return _docker_error(exc)
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return _docker_error(exc)
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
    try:
        c = client.containers.get(container_id)
        c.update(**kwargs)
        return {"ok": True}
    except Exception as exc:
        return _docker_error(exc)


def search_images(term, limit=25):
    """Docker-Registry (Docker Hub) nach Images durchsuchen."""
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return {"ok": False, "results": [], "stderr": str(exc)}
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
    try:
        container_id = _require_container_ref(container_id)
    except ValueError as exc:
        return {"logs": "", "ok": False, "stderr": str(exc)}
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return {"logs": "", "ok": False, "stderr": str(exc)}
    try:
        c = client.containers.get(container_id)
        return {"logs": c.logs(tail=tail).decode(errors="replace")}
    except Exception as exc:
        return {"logs": "", "ok": False, "stderr": str(exc)}


# --- Images ---

def list_images():
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return {"ok": False, "images": [], "stderr": str(exc)}
    try:
        images = [{
            "id": img.short_id,
            "tags": img.tags,
            "size": img.attrs["Size"],
        } for img in client.images.list()]
    except Exception as exc:
        return {"ok": False, "images": [], "stderr": str(exc)}
    return {"ok": True, "images": images}


def pull_image(name):
    try:
        name = validators.require_docker_ref(name, "image")
    except ValueError as exc:
        return _docker_error(exc)
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return _docker_error(exc)
    try:
        client.images.pull(name)
        return {"ok": True}
    except Exception as exc:
        return _docker_error(exc)


def remove_image(image_id):
    try:
        image_id = validators.require_docker_ref(image_id, "image")
    except ValueError as exc:
        return _docker_error(exc)
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return _docker_error(exc)
    try:
        client.images.remove(image_id, force=True)
        return {"ok": True}
    except Exception as exc:
        return _docker_error(exc)


# --- Volumes ---

def list_volumes():
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return {"ok": False, "volumes": [], "stderr": str(exc)}
    try:
        volumes = [{"name": v.name, "driver": v.attrs.get("Driver"),
                    "mountpoint": v.attrs.get("Mountpoint")}
                   for v in client.volumes.list()]
    except Exception as exc:
        return {"ok": False, "volumes": [], "stderr": str(exc)}
    return {"ok": True, "volumes": volumes}


def remove_volume(name):
    try:
        name = validators.require_docker_volume(name)
    except ValueError as exc:
        return _docker_error(exc)
    try:
        client = _get_client()
    except DockerUnavailable as exc:
        return _docker_error(exc)
    try:
        v = client.volumes.get(name)
        v.remove(force=True)
        return {"ok": True}
    except Exception as exc:
        return _docker_error(exc)


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
    safe = validators.require_slug(str(name or "").strip(), "Compose project name")
    return os.path.join(COMPOSE_DIR, safe)


def save_compose(name, content, env_enabled=False, env_content=""):
    validate_compose_content(content)
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


def compose_action(name, action):
    path = _compose_project_path(name)
    cmd_map = {
        "up": ["docker", "compose", "up", "-d"],
        "down": ["docker", "compose", "down"],
        "restart": ["docker", "compose", "restart"],
    }
    if action not in cmd_map:
        raise ValueError("Unbekannte Aktion")
    try:
        r = subprocess.run(cmd_map[action], cwd=path, capture_output=True,
                           text=True, timeout=300)
    except FileNotFoundError:
        return {"ok": False, "output": f"{cmd_map[action][0]} nicht gefunden"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": " ".join(cmd_map[action]) + " timed out"}
    except OSError as exc:
        return {"ok": False, "output": str(exc)}
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
    name = validators.require_slug(str(name or "").strip(), "Compose project name")
    root = os.path.abspath(COMPOSE_DIR)
    path = os.path.abspath(os.path.join(root, name))
    if path == root or not path.startswith(root + os.sep):
        raise ValueError("Invalid Compose project path")
    if not os.path.isdir(path):
        return {"ok": True}
    try:
        result = subprocess.run(["docker", "compose", "down"], cwd=path,
                                capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return {"ok": False, "output": "docker nicht gefunden"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "docker compose down timed out"}
    except OSError as exc:
        return {"ok": False, "output": str(exc)}
    if result.returncode != 0:
        return {"ok": False, "output": result.stdout + result.stderr}
    shutil.rmtree(path)
    return {"ok": True}
