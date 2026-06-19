"""
Dashboard-Modul – App-Kacheln auf der Startseite.
Speichert Reihenfolge, URL-Toggle und Custom-Links in dashboard.json.
"""
import os
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

from modules.compose_utils import best_web_port_from_compose

DATA_DIR = os.getenv("RUNVARD_DATA_DIR", "/opt/runvard/data")
DASH_FILE = os.path.join(DATA_DIR, "dashboard.json")
APPS_DIR = os.path.join(DATA_DIR, "apps")
COMPOSE_DIR = os.path.join(DATA_DIR, "compose")
CUSTOM_HEALTH_TIMEOUT = 2
CUSTOM_HEALTH_TTL = 30
CUSTOM_HEALTH_WORKERS = 6
CUSTOM_HEALTH_OK_BELOW = 500
_custom_health_cache = {}
_custom_health_lock = threading.Lock()


def _load():
    try:
        with open(DASH_FILE) as f:
            return json.load(f)
    except Exception:
        return {"tiles": []}


def _save(data):
    os.makedirs(os.path.dirname(DASH_FILE), exist_ok=True)
    with open(DASH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _compose_project_name(tile):
    project = str(tile.get("project") or tile.get("id") or "").strip()
    if project.startswith("compose:"):
        project = project.split(":", 1)[1]
    return os.path.basename(project)


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


def _unknown_custom_health(label="Unknown", detail=""):
    return {
        "state": "unknown",
        "label": label,
        "detail": detail or label,
        "checked_at": int(time.time()),
    }


def _normalize_custom_url(url):
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


def _health_from_response(response):
    code = response.status_code
    if code < CUSTOM_HEALTH_OK_BELOW:
        return {
            "state": "run",
            "label": "Reachable",
            "detail": f"HTTP {code}",
            "checked_at": int(time.time()),
        }
    return {
        "state": "stop",
        "label": "Unreachable",
        "detail": f"HTTP {code}",
        "checked_at": int(time.time()),
    }


def _check_custom_url(url):
    normalized = _normalize_custom_url(url)
    if not normalized:
        return _unknown_custom_health("Invalid URL", "Only http:// and https:// URLs can be checked")
    try:
        response = requests.head(normalized, allow_redirects=True,
                                 timeout=CUSTOM_HEALTH_TIMEOUT)
        if response.status_code in {405, 501}:
            response = requests.get(normalized, allow_redirects=True,
                                    timeout=CUSTOM_HEALTH_TIMEOUT)
        return _health_from_response(response)
    except requests.Timeout:
        return {
            "state": "stop",
            "label": "Timeout",
            "detail": "Request timed out",
            "checked_at": int(time.time()),
        }
    except requests.RequestException as e:
        return {
            "state": "stop",
            "label": "Unreachable",
            "detail": str(e) or e.__class__.__name__,
            "checked_at": int(time.time()),
        }


def _cached_custom_health(url):
    normalized = _normalize_custom_url(url)
    cache_key = normalized or str(url or "").strip()
    now = time.time()
    with _custom_health_lock:
        cached = _custom_health_cache.get(cache_key)
        if cached and now - cached.get("_cached_at", 0) < CUSTOM_HEALTH_TTL:
            return dict(cached["health"])
    health = _check_custom_url(url)
    with _custom_health_lock:
        _custom_health_cache[cache_key] = {
            "_cached_at": now,
            "health": dict(health),
        }
    return health


def _attach_custom_health(tiles):
    custom_tiles = [t for t in tiles if t.get("type") == "custom"]
    if not custom_tiles:
        return
    workers = min(CUSTOM_HEALTH_WORKERS, len(custom_tiles))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_cached_custom_health, tile.get("url")): tile
            for tile in custom_tiles
        }
        for future in as_completed(future_map):
            tile = future_map[future]
            try:
                tile["health"] = future.result()
            except Exception as e:
                tile["health"] = {
                    "state": "stop",
                    "label": "Unreachable",
                    "detail": str(e) or e.__class__.__name__,
                    "checked_at": int(time.time()),
                }


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
    _attach_custom_health(tiles)
    return {"tiles": tiles}


def add_tile(tile_type, tile_id, name="", url="", icon="", port=0):
    """Fügt eine Kachel hinzu (app oder custom)."""
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
        "name": name,
        "icon": icon,
        "show_url": False,
        "order": len(data["tiles"]),
    }
    if tile_type == "custom":
        tile["url"] = url
    if tile_type == "compose":
        tile["project"] = _compose_project_name(tile)
    if port:
        tile["port"] = port
    data["tiles"].append(tile)
    _save(data)
    return {"ok": True}


def remove_tile(tile_id):
    """Entfernt eine Kachel vom Dashboard."""
    data = _load()
    data["tiles"] = [t for t in data["tiles"] if t["id"] != tile_id]
    _save(data)
    return {"ok": True}


def save_order(order):
    """Speichert die Kachel-Reihenfolge. order = Liste von IDs."""
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
    return value


def _normalize_accent(accent):
    value = str(accent or "").strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            return ""
    return ""


def update_tile(tile_id, name=None, url=None, icon=None, host=None,
                show_url=None, accent=None, note=None):
    """Aktualisiert Dashboard-Metadaten einer Kachel."""
    data = _load()
    for t in data["tiles"]:
        if t["id"] == tile_id:
            if name is not None:
                t["name"] = name
            if url is not None:
                t["url"] = url
            if icon is not None:
                t["icon"] = icon
            if show_url is not None:
                t["show_url"] = bool(show_url)
            if accent is not None:
                clean_accent = _normalize_accent(accent)
                if clean_accent:
                    t["accent"] = clean_accent
                else:
                    t.pop("accent", None)
            if note is not None:
                clean_note = str(note or "").strip()[:80]
                if clean_note:
                    t["note"] = clean_note
                else:
                    t.pop("note", None)
            if host is not None:
                clean_host = _normalize_host(host)
                if clean_host:
                    t["host"] = clean_host
                else:
                    t.pop("host", None)
            break
    _save(data)
    return {"ok": True}
