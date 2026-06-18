"""runvard-Anwendungskonten (getrennt von OS-Benutzern): Store mit Rollen + Hash-Passwörtern."""
import os
import json
import hmac
import hashlib
import secrets

STORE = "/opt/runvard/data/users.json"
ROLES = ("admin", "readonly")


def _load():
    try:
        with open(STORE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w") as f:
        json.dump(d, f, indent=2)
    try:
        os.chmod(STORE, 0o600)
    except OSError:
        pass


def _hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200000
    ).hex()


def list_users():
    return [{"username": u, "role": v.get("role", "admin")}
            for u, v in sorted(_load().items())]


def add_user(username, password, role="readonly"):
    username = (username or "").strip()
    if not username or "|" in username:
        return {"ok": False, "error": "Ungueltiger Benutzername"}
    if not password:
        return {"ok": False, "error": "Passwort erforderlich"}
    if role not in ROLES:
        role = "readonly"
    d = _load()
    salt = secrets.token_hex(16)
    d[username] = {"role": role, "salt": salt, "hash": _hash(password, salt)}
    _save(d)
    return {"ok": True}


def set_password(username, password):
    d = _load()
    if username not in d:
        return {"ok": False, "error": "Unbekannter Benutzer"}
    if not password:
        return {"ok": False, "error": "Passwort erforderlich"}
    salt = secrets.token_hex(16)
    d[username]["salt"] = salt
    d[username]["hash"] = _hash(password, salt)
    _save(d)
    return {"ok": True}


def set_role(username, role):
    d = _load()
    if username not in d:
        return {"ok": False, "error": "Unbekannter Benutzer"}
    if role not in ROLES:
        return {"ok": False, "error": "Ungueltige Rolle"}
    d[username]["role"] = role
    _save(d)
    return {"ok": True}


def delete_user(username):
    d = _load()
    d.pop(username, None)
    _save(d)
    return {"ok": True}


def verify(username, password):
    """Gibt die Rolle zurück, wenn Benutzer/Passwort passen, sonst None."""
    u = _load().get(username)
    if not u:
        return None
    calc = _hash(password, u.get("salt", ""))
    if hmac.compare_digest(calc, u.get("hash", "")):
        return u.get("role", "admin")
    return None
