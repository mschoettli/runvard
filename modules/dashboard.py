"""
Dashboard-Modul – App-Kacheln auf der Startseite.
Speichert Reihenfolge, URL-Toggle und Custom-Links in dashboard.json.
"""
import os
import json
import re
import subprocess
import time

from modules.compose_utils import best_web_port_from_compose
from modules.runtime import data_path

DASH_FILE = data_path("dashboard.json")
APPS_DIR = data_path("apps")
COMPOSE_DIR = data_path("compose")
TILE_ID_RE = re.compile(r"^(compose:)?[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}$")
TILE_TYPES = {"app", "compose", "custom"}


def _clean_text(value, limit=160):
    return str(value or "").replace("\x00", "").strip()[:limit]


def _require_tile_id(tile_id):
    tile_id = str(tile_id or "").strip()
    if not TILE_ID_RE.fullmatch(tile_id):
        raise ValueError("Invalid dashboard tile id")
    return tile_id


def _require_tile_type(tile_type):
    tile_type = str(tile_type or "").strip()
    if tile_type not in TILE_TYPES:
        raise ValueError("Invalid dashboard tile type")
    return tile_type


def _clean_port(port):
    try:
        port = int(port or 0)
    except (TypeError, ValueError):
        raise ValueError("Invalid dashboard tile port") from None
    if not (0 <= port <= 65535):
        raise ValueError("Invalid dashboard tile port")
    return port


def _load():
    try:
        with open(DASH_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"tiles": []}
    except json.JSONDecodeError:
        try:
            os.replace(DASH_FILE, f"{DASH_FILE}.corrupt-{int(time.time())}")
        except OSError:
            pass
        return {"tiles": []}
    except OSError:
        return {"tiles": []}


def _save(data):
    os.makedirs(os.path.dirname(DASH_FILE), exist_ok=True)
    with open(DASH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _compose_project_name(tile):
    project = str(tile.get("project") or tile.get("id") or "").strip()
    if project.startswith("compose:"):
        project = project.split(":", 1)[1]
    return os.path.basename(_require_tile_id(project))


def _compose_running(path, service=None):
    if not os.path.isdir(path):
        return False
    try:
        cmd = ["docker", "compose", "ps", "--status", "running", "-q"]
        if service:
            cmd.append(service)
        r = subprocess.run(cmd,
                           cwd=path, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            r = subprocess.run(["docker", "compose", "ps", "--status", "running", "-q"],
                               cwd=path, capture_output=True, text=True,
                               timeout=15)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _compose_port_from_path(path):
    """Liest den besten Web-Port aus dem Compose-File."""
    compose = os.path.join(path, "docker-compose.yml")
    if not os.path.isfile(compose):
        return 0
    try:
        with open(compose) as f:
            return best_web_port_from_compose(f.read())
    except Exception:
        pass
    return 0


def get_dashboard():
    """Gibt alle Dashboard-Kacheln mit Live-Status zurück."""
    data = _load()
    tiles = []
    for t in data.get("tiles", []):
        tile = dict(t)
        if tile.get("type") == "app":
            path = os.path.join(APPS_DIR, tile["id"])
            tile["running"] = _compose_running(path, tile["id"])
            tile["installed"] = os.path.isfile(
                os.path.join(path, "docker-compose.yml"))
            if not tile["installed"]:
                continue  # App wurde deinstalliert → nicht anzeigen
            if not tile.get("port"):
                tile["port"] = _compose_port_from_path(path)
        elif tile.get("type") == "compose":
            project = _compose_project_name(tile)
            path = os.path.join(COMPOSE_DIR, project)
            tile["project"] = project
            tile["running"] = _compose_running(path)
            tile["installed"] = os.path.isfile(
                os.path.join(path, "docker-compose.yml"))
            if not tile["installed"]:
                continue
            tile["port"] = _compose_port_from_path(path)
        tiles.append(tile)
    return {"tiles": tiles}


def add_tile(tile_type, tile_id, name="", url="", icon="", port=0):
    """Fügt eine Kachel hinzu (app oder custom)."""
    tile_type = _require_tile_type(tile_type)
    tile_id = _require_tile_id(tile_id)
    port = _clean_port(port)
    data = _load()
    # Duplikat-Check
    for t in data["tiles"]:
        if t["id"] == tile_id:
            if tile_type == "compose" and port:
                t["port"] = port
            return {"ok": True, "msg": "Bereits vorhanden"}
    tile = {
        "id": tile_id,
        "type": tile_type,
        "name": _clean_text(name),
        "icon": _clean_text(icon, 512),
        "show_url": False,
        "order": len(data["tiles"]),
    }
    if tile_type == "custom":
        tile["url"] = _clean_text(url, 512)
    if tile_type == "compose":
        tile["project"] = _compose_project_name(tile)
    if port:
        tile["port"] = port
    data["tiles"].append(tile)
    _save(data)
    return {"ok": True}


def remove_tile(tile_id):
    """Entfernt eine Kachel vom Dashboard."""
    tile_id = _require_tile_id(tile_id)
    data = _load()
    data["tiles"] = [t for t in data["tiles"] if t["id"] != tile_id]
    _save(data)
    return {"ok": True}


def save_order(order):
    """Speichert die Kachel-Reihenfolge. order = Liste von IDs."""
    if not isinstance(order, list):
        raise ValueError("Invalid dashboard order")
    order = [_require_tile_id(tid) for tid in order]
    data = _load()
    id_map = {t["id"]: t for t in data["tiles"]}
    reordered = []
    for i, tid in enumerate(order):
        if tid in id_map:
            tile = id_map[tid]
            tile["order"] = i
            reordered.append(tile)
    # Tiles die nicht in order sind, hinten anhängen
    for t in data["tiles"]:
        if t["id"] not in order:
            t["order"] = len(reordered)
            reordered.append(t)
    data["tiles"] = reordered
    _save(data)
    return {"ok": True}


def toggle_url(tile_id, show):
    """Schaltet die URL-Anzeige für eine Kachel um."""
    tile_id = _require_tile_id(tile_id)
    data = _load()
    for t in data["tiles"]:
        if t["id"] == tile_id:
            t["show_url"] = show
            break
    _save(data)
    return {"ok": True}


def _normalize_host(host):
    value = str(host or "").strip()
    value = value.removeprefix("http://").removeprefix("https://")
    value = value.split("/", 1)[0].strip()
    if value and not HOST_RE.fullmatch(value):
        raise ValueError("Invalid dashboard tile host")
    return value


def update_tile(tile_id, name=None, url=None, icon=None, host=None):
    """Aktualisiert eine Custom-Kachel."""
    tile_id = _require_tile_id(tile_id)
    data = _load()
    for t in data["tiles"]:
        if t["id"] == tile_id:
            if name is not None:
                t["name"] = _clean_text(name)
            if url is not None:
                t["url"] = _clean_text(url, 512)
            if icon is not None:
                t["icon"] = _clean_text(icon, 512)
            if host is not None:
                clean_host = _normalize_host(host)
                if clean_host:
                    t["host"] = clean_host
                else:
                    t.pop("host", None)
            break
    _save(data)
    return {"ok": True}
