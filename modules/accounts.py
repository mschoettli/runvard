"""runvard-Anwendungskonten (getrennt von OS-Benutzern): Store mit Rollen + Hash-Passwörtern."""
import os
import json
import hmac
import hashlib
import secrets
import logging
import tempfile
import fcntl
from contextlib import contextmanager

STORE = "/opt/runvard/data/users.json"
ROLES = ("admin", "readonly")


def _load():
    try:
        with open(STORE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


@contextmanager
def _write_lock():
    directory = os.path.dirname(STORE)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor = os.open(STORE + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _save(d):
    directory = os.path.dirname(STORE)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".users-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STORE)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200000
    ).hex()


def list_users():
    return [{"username": u, "role": v.get("role", "admin")}
            for u, v in sorted(_load().items())]


def _create_user(users, username, password, role):
    username = (username or "").strip()
    if not username or "|" in username:
        return {"ok": False, "error": "invalid_username"}
    if not password:
        return {"ok": False, "error": "password_required"}
    if role not in ROLES:
        return {"ok": False, "error": "invalid_role"}
    if username in users:
        return {"ok": False, "error": "account_exists"}
    salt = secrets.token_hex(16)
    users[username] = {"role": role, "salt": salt, "hash": _hash(password, salt)}
    _save(users)
    return {"ok": True}


def add_user(username, password, role="readonly"):
    with _write_lock():
        return _create_user(_load(), username, password, role)


def _last_admin(users, username):
    return users[username].get("role", "admin") == "admin" and not any(
        name != username and entry.get("role", "admin") == "admin"
        for name, entry in users.items()
    )


def set_password(username, password):
    with _write_lock():
        users = _load()
        if username not in users:
            return {"ok": False, "error": "unknown_account"}
        if not password:
            return {"ok": False, "error": "password_required"}
        salt = secrets.token_hex(16)
        users[username].update(salt=salt, hash=_hash(password, salt))
        _save(users)
        return {"ok": True}


def set_role(username, role):
    with _write_lock():
        users = _load()
        if username not in users:
            return {"ok": False, "error": "unknown_account"}
        if role not in ROLES:
            return {"ok": False, "error": "invalid_role"}
        if role != "admin" and _last_admin(users, username):
            return {"ok": False, "error": "last_admin"}
        users[username]["role"] = role
        _save(users)
        return {"ok": True}


def delete_user(username):
    with _write_lock():
        users = _load()
        if username not in users:
            return {"ok": False, "error": "unknown_account"}
        if _last_admin(users, username):
            return {"ok": False, "error": "last_admin"}
        del users[username]
        _save(users)
        return {"ok": True}


def session_account(username):
    return _load().get(username)


def authenticated_account(username, password):
    u = _load().get(username)
    if not u:
        return None
    calc = _hash(password, u.get("salt", ""))
    if hmac.compare_digest(calc, u.get("hash", "")):
        return u
    return None


def verify(username, password):
    account = authenticated_account(username, password)
    return account.get("role", "admin") if account else None


def ensure_bootstrap_account():
    """Require an account, migrating explicitly configured legacy credentials once."""
    with _write_lock():
        existing = _load()
        if existing:
            admins = [name for name, meta in existing.items() if meta.get("role", "admin") == "admin"]
            if not admins:
                raise RuntimeError("runvard has no administrator account")
            return admins[0]
        username = os.environ.get("RUNVARD_USER")
        password = os.environ.get("RUNVARD_PASS")
        if username is None and password is None:
            raise RuntimeError(
                "runvard has no administrator account; configure secure bootstrap credentials"
            )
        if not username or not username.strip() or not password:
            raise RuntimeError("RUNVARD_USER and RUNVARD_PASS must both be non-empty")
        if username.strip() == "admin" and password == "runvard":
            raise RuntimeError("the insecure legacy admin/runvard credentials are rejected")
        logging.getLogger(__name__).warning(
            "RUNVARD_USER/RUNVARD_PASS are deprecated; migrating them to the account database"
        )
        result = _create_user(existing, username, password, "admin")
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "account migration failed"))
        return username.strip()
