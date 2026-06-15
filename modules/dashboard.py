"""
Dashboard-Modul – App-Kacheln auf der Startseite.
Speichert Reihenfolge, URL-Toggle und Custom-Links in dashboard.json.
"""
import os
import json
import subprocess

DASH_FILE = "/opt/runvard/data/dashboard.json"
APPS_DIR = "/opt/runvard/data/apps"


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


def _compose_running(app_id):
    path = os.path.join(APPS_DIR, app_id)
    if not os.path.isdir(path):
        return False
    try:
        r = subprocess.run(["docker", "compose", "ps", "--status", "running", "-q", app_id],
                           cwd=path, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            r = subprocess.run(["docker", "compose", "ps", "--status", "running", "-q"],
                               cwd=path, capture_output=True, text=True,
                               timeout=15)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _compose_port(app_id):
    """Liest den ersten Port aus dem Compose-File."""
    compose = os.path.join(APPS_DIR, app_id, "docker-compose.yml")
    if not os.path.isfile(compose):
        return 0
    try:
        with open(compose) as f:
            for line in f:
                line = line.strip()
                if ':' in line and line.startswith('- "'):
                    # Format: - "8096:8096"
                    port_str = line.strip('- "\'')
                    host_port = port_str.split(':')[0].split('/')
                    return int(host_port[0])
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
            tile["running"] = _compose_running(tile["id"])
            tile["installed"] = os.path.isfile(
                os.path.join(APPS_DIR, tile["id"], "docker-compose.yml"))
            if not tile["installed"]:
                continue  # App wurde deinstalliert → nicht anzeigen
            if not tile.get("port"):
                tile["port"] = _compose_port(tile["id"])
        tiles.append(tile)
    return {"tiles": tiles}


def add_tile(tile_type, tile_id, name="", url="", icon="", port=0):
    """Fügt eine Kachel hinzu (app oder custom)."""
    data = _load()
    # Duplikat-Check
    for t in data["tiles"]:
        if t["id"] == tile_id:
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


def update_tile(tile_id, name=None, url=None, icon=None):
    """Aktualisiert eine Custom-Kachel."""
    data = _load()
    for t in data["tiles"]:
        if t["id"] == tile_id:
            if name is not None:
                t["name"] = name
            if url is not None:
                t["url"] = url
            if icon is not None:
                t["icon"] = icon
            break
    _save(data)
    return {"ok": True}
