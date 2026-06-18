"""
runvard - Server Panel
Haupt-Anwendung: FastAPI mit allen Routen, Auth und WebSocket-Terminal.
"""
import os
import secrets
import asyncio
import json
import hmac
import hashlib
import base64
import time
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket, \
    WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, \
    RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.exceptions import RequestValidationError

from modules.runtime import data_dir
from modules import (system, terminal, files, storage, docker_mgr, services,
                     vms, backup, shares, network, security, monitoring,
                     system_mgr, apps, dashboard, metrics, accounts, audit,
                     security_tokens, validators)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        yield
    finally:
        docker_mgr.close_client()


app = FastAPI(title="runvard", docs_url=None, redoc_url=None, lifespan=_lifespan)
http_basic = HTTPBasic()


@app.exception_handler(HTTPException)
async def _http_error_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        {"ok": False, "error": str(exc.detail), "status": exc.status_code},
        status_code=exc.status_code,
    )


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        {"ok": False, "error": str(exc), "status": 400},
        status_code=400,
    )


@app.exception_handler(PermissionError)
async def _permission_error_handler(request: Request, exc: PermissionError):
    return JSONResponse(
        {"ok": False, "error": str(exc), "status": 403},
        status_code=403,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        {"ok": False, "error": "Invalid request", "status": 422},
        status_code=422,
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        {"ok": False, "error": str(exc) or exc.__class__.__name__, "status": 500},
        status_code=500,
    )


@app.middleware("http")
async def _audit_mw(request: Request, call_next):
    response = await call_next(request)
    try:
        m = request.method
        if m in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api"):
            parsed = _parse_token(request.cookies.get(COOKIE_NAME))
            who = parsed[0] if parsed else ("guest" if not login_enabled() else "?")
            audit.record_event(
                user=who,
                action=f"{m} {request.url.path}",
                ok=response.status_code < 400,
                remote=request.client.host if request.client else "",
            )
    except Exception:
        pass
    return response

# Hintergrund-Thread: prüft alle 12h auf App-Updates
try:
    apps.start_update_checker()
except Exception:
    pass

# Hintergrund-Thread: sammelt Systemmetriken für den Verlauf
try:
    metrics.start_sampler()
except Exception:
    pass

RUNVARD_USER = os.environ.get("RUNVARD_USER", os.environ.get("ACTAX_USER", "admin"))
RUNVARD_PASS = os.environ.get("RUNVARD_PASS", os.environ.get("ACTAX_PASS", "runvard"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ Auth: Session-Cookies + Login an/aus ============
DATA_DIR = data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
AUTH_CFG_FILE = os.path.join(DATA_DIR, "auth.json")
COOKIE_NAME = "runvard_session"
SESSION_TTL = 8 * 3600              # 8 Stunden
SESSION_TTL_REMEMBER = 30 * 86400   # 30 Tage
CONFIRM_ACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,79}$")


def _secret():
    try:
        with open(SECRET_FILE, "rb") as f:
            return f.read()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        with open(SECRET_FILE, "wb") as f:
            f.write(key)
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
        return key


def login_enabled():
    cfg = _load_auth_config()
    return bool(cfg.get("login_enabled", True))


def _load_auth_config():
    try:
        with open(AUTH_CFG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        try:
            os.replace(AUTH_CFG_FILE, f"{AUTH_CFG_FILE}.corrupt-{int(time.time())}")
        except OSError:
            pass
        return {}
    except OSError:
        return {}


def set_login_enabled(val):
    os.makedirs(os.path.dirname(AUTH_CFG_FILE), exist_ok=True)
    with open(AUTH_CFG_FILE, "w") as f:
        json.dump({"login_enabled": bool(val)}, f)


def _parse_login_enabled_form(value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError("Ungueltiger Login-Status")
    return value == "1"


def _parse_remember_form(value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError("Ungueltiger Remember-Wert")
    return value == "1"


def _parse_bool_form(value: str, label: str) -> bool:
    value = str(value).strip().lower()
    if value in ("1", "true"):
        return True
    if value in ("0", "false"):
        return False
    raise ValueError(f"Ungueltiger {label}")


def _parse_float_range(value, minimum: float, maximum: float, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {label}") from None
    if not (minimum <= parsed <= maximum):
        raise ValueError(f"Invalid {label}")
    return parsed


def _parse_choice(value: str, choices: set[str], label: str) -> str:
    value = str(value or "").strip()
    if value not in choices:
        raise ValueError(f"Invalid {label}")
    return value


def _parse_dashboard_order_form(value: str) -> list[str]:
    try:
        order = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError("Ungueltige Dashboard-Reihenfolge") from None
    if not isinstance(order, list):
        raise ValueError("Ungueltige Dashboard-Reihenfolge")
    return order


def _parse_confirm_form(action: str, target: str) -> tuple[str, str]:
    action = str(action or "").strip()
    target = str(target or "").strip()
    if not CONFIRM_ACTION_RE.fullmatch(action):
        raise ValueError("Ungueltige Bestaetigung")
    if not target or len(target) > 240:
        raise ValueError("Ungueltige Bestaetigung")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        raise ValueError("Ungueltige Bestaetigung")
    return action, target


def _parse_file_paths_form(value: str) -> list[str]:
    parts = [str(part).strip() for part in str(value or "").split("|")]
    if not parts or any(not part for part in parts):
        raise ValueError("Ungueltige Dateiauswahl")
    if len(parts) > 200:
        raise ValueError("Zu viele Dateien ausgewaehlt")
    return parts


def _apply_terminal_ws_message(session, message: str) -> bool:
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    msg_type = data.get("type")
    if msg_type == "input":
        payload = data.get("data", "")
        if not isinstance(payload, str) or len(payload) > 8192:
            return False
        session.write(payload)
        return True
    if msg_type == "resize":
        try:
            rows = validators.require_int_range(data.get("rows"), 1, 200, "rows")
            cols = validators.require_int_range(data.get("cols"), 1, 400, "cols")
        except ValueError:
            return False
        session.resize(rows, cols)
        return True
    return True


def _parse_vnc_port(value) -> int | None:
    if value is None or str(value) in ("", "-1", "None"):
        return None
    try:
        return validators.require_int_range(value, 1, 65535, "VNC port")
    except ValueError:
        return None


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(username, ttl, role="admin"):
    payload = f"{username}|{role}|{int(time.time()) + ttl}".encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return _b64e(payload) + "." + _b64e(sig)


def _parse_token(token):
    """Gibt (username, role) zurück oder None. Akzeptiert auch Alt-Tokens (username|exp = admin)."""
    if not token or "." not in token:
        return None
    try:
        p_b64, s_b64 = token.split(".", 1)
        payload = _b64d(p_b64)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(s_b64), expected):
            return None
        parts = payload.decode().split("|")
        if len(parts) == 3:
            username, role, exp = parts
        elif len(parts) == 2:
            username, exp = parts
            role = "admin"
        else:
            return None
        if int(exp) < int(time.time()):
            return None
        return (username, role)
    except Exception:
        return None


def verify_token(token):
    r = _parse_token(token)
    return r[0] if r else None


_login_attempts = {}


def _rate_ok(ip):
    now = time.time()
    bucket = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    bucket.append(now)
    _login_attempts[ip] = bucket
    return len(bucket) <= 5


def _current_user(request: Request):
    return verify_token(request.cookies.get(COOKIE_NAME))


def auth(request: Request):
    """Session-Cookie-Auth. Bei deaktiviertem Login freier Zugriff. Readonly darf nur lesen."""
    if not login_enabled():
        return "guest"
    parsed = _parse_token(request.cookies.get(COOKIE_NAME))
    if not parsed:
        raise HTTPException(status_code=401, detail="Unauthorized")
    username, role = parsed
    if role == "readonly" and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(status_code=403, detail="Nur-Lese-Zugriff")
    return username


def require_admin(request: Request):
    """Wie auth, verlangt aber Admin-Rolle."""
    if not login_enabled():
        return "guest"
    parsed = _parse_token(request.cookies.get(COOKIE_NAME))
    if not parsed:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if parsed[1] != "admin":
        raise HTTPException(status_code=403, detail="Admin erforderlich")
    return parsed[0]


# ============ Frontend ============

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if login_enabled() and not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    with open(os.path.join(BASE_DIR, "static", "index.html")) as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if login_enabled() and _current_user(request):
        return RedirectResponse("/", status_code=302)
    with open(os.path.join(BASE_DIR, "static", "login.html")) as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.post("/api/login")
def api_login(request: Request, username: str = Form(...),
              password: str = Form(...), remember: str = Form("0")):
    ip = request.client.host if request.client else "?"
    if not _rate_ok(ip):
        raise HTTPException(status_code=429, detail="Too many attempts")
    remember_session = _parse_remember_form(remember)
    role = accounts.verify(username, password)
    if not role and secrets.compare_digest(username, RUNVARD_USER) \
            and secrets.compare_digest(password, RUNVARD_PASS):
        role = "admin"
    if not role:
        raise HTTPException(status_code=401, detail="Unauthorized")
    ttl = SESSION_TTL_REMEMBER if remember_session else SESSION_TTL
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE_NAME, make_token(username, ttl, role), max_age=ttl,
                    httponly=True, samesite="strict",
                    secure=(request.url.scheme == "https"), path="/")
    return resp


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.get("/api/auth/status")
def api_auth_status(request: Request):
    if not login_enabled():
        return {"login_enabled": False, "user": None, "role": "admin"}
    parsed = _parse_token(request.cookies.get(COOKIE_NAME))
    return {"login_enabled": True,
            "user": parsed[0] if parsed else None,
            "role": parsed[1] if parsed else None}


@app.get("/api/config")
def api_config(user: str = Depends(auth)):
    return {"data_dir": DATA_DIR}


def _confirm_action(user: str, action: str, target: str, token: str) -> None:
    security_tokens.require_confirm_token(user, action, target, token)


def _parse_device_csv(value: str, label: str = "devices") -> list[str]:
    devices = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if not devices:
        raise ValueError(f"Invalid {label}")
    for device in devices:
        validators.require_device(device)
    return devices


def _parse_lvm_size(value: str, allow_percent: bool = False) -> str:
    size = str(value or "").strip()
    patterns = [r"\+?\d+(\.\d+)?[KMGTP]?"]
    if allow_percent:
        patterns.append(r"\d+%FREE")
    if not any(re.fullmatch(pattern, size) for pattern in patterns):
        raise ValueError("Invalid LVM size")
    return size


def _file_response_path(path: str) -> str:
    path = validators.guard_read_path(path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="Pfad ist keine Datei")
    return path


@app.post("/api/confirm-token")
def api_confirm_token(action: str = Form(...), target: str = Form(...),
                      user: str = Depends(require_admin)):
    action, target = _parse_confirm_form(action, target)
    return security_tokens.issue_confirm_token(user, action, target)


@app.post("/api/auth/toggle")
def api_auth_toggle(enabled: str = Form(...), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    next_enabled = _parse_login_enabled_form(enabled)
    _confirm_action(user, "auth-toggle", enabled, confirm_token)
    set_login_enabled(next_enabled)
    return {"ok": True, "login_enabled": login_enabled()}


# ============ runvard-Konten (RBAC) ============

@app.get("/api/accounts")
def accounts_list(user: str = Depends(require_admin)):
    return {"users": accounts.list_users()}


@app.post("/api/accounts/add")
def accounts_add(username: str = Form(...), password: str = Form(...),
                 role: str = Form("readonly"), confirm_token: str = Form(""),
                 user: str = Depends(require_admin)):
    role = _parse_choice(role, {"admin", "readonly"}, "Account role")
    _confirm_action(user, "account-add", username, confirm_token)
    return accounts.add_user(username, password, role)


@app.post("/api/accounts/password")
def accounts_password(username: str = Form(...), password: str = Form(...),
                      confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    _confirm_action(user, "account-password", username, confirm_token)
    return accounts.set_password(username, password)


@app.post("/api/accounts/role")
def accounts_role(username: str = Form(...), role: str = Form(...),
                  confirm_token: str = Form(""),
                  user: str = Depends(require_admin)):
    role = _parse_choice(role, {"admin", "readonly"}, "Account role")
    _confirm_action(user, "account-role", username, confirm_token)
    return accounts.set_role(username, role)


@app.post("/api/accounts/delete")
def accounts_delete(username: str = Form(...), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    _confirm_action(user, "account-delete", username, confirm_token)
    return accounts.delete_user(username)


@app.get("/btop", response_class=HTMLResponse)
def btop_page(user: str = Depends(auth)):
    with open(os.path.join(BASE_DIR, "static", "btop.html")) as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


# ============ System ============

@app.get("/api/system/stats")
def system_stats(user: str = Depends(auth)):
    return system.get_stats()


@app.get("/api/system/info")
def system_info(user: str = Depends(auth)):
    info = system.get_system_info()
    info["user"] = user
    return info


@app.get("/api/system/disks")
def system_disks(user: str = Depends(auth)):
    return system.get_disk_usage()


@app.get("/api/system/temps")
def system_temps(user: str = Depends(auth)):
    return system.get_temps()


@app.get("/api/system/processes")
def system_processes(sort_by: str = "cpu", user: str = Depends(auth)):
    return system.get_processes(sort_by)


@app.get("/api/system/disk-io")
def system_disk_io(user: str = Depends(auth)):
    return system.get_disk_io()


@app.get("/api/system/net-detail")
def system_net_detail(user: str = Depends(auth)):
    return system.get_net_detail()


@app.get("/api/system/history")
def system_history(minutes: int = 60, user: str = Depends(auth)):
    return metrics.get_history(minutes)


# ============ Dateien ============

@app.get("/api/files/list")
def files_list(path: str = "/home", user: str = Depends(auth)):
    try: return files.list_dir(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/files/read")
def files_read(path: str, user: str = Depends(auth)):
    try: return files.read_file(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/write")
def files_write(path: str = Form(...), content: str = Form(...),
                confirm_token: str = Form(""),
                user: str = Depends(require_admin)):
    _confirm_action(user, "files-write", path, confirm_token)
    try: return files.write_file(path, content)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/rename")
def files_rename(path: str = Form(...), new_name: str = Form(...),
                 confirm_token: str = Form(""),
                 user: str = Depends(require_admin)):
    _confirm_action(user, "files-rename", path, confirm_token)
    try: return files.rename(path, new_name)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/copy")
def files_copy(src: str = Form(...), dst_dir: str = Form(...),
               confirm_token: str = Form(""),
               user: str = Depends(require_admin)):
    _confirm_action(user, "files-copy", src, confirm_token)
    try: return files.copy_item(src, dst_dir)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/move")
def files_move(src: str = Form(...), dst_dir: str = Form(...),
               confirm_token: str = Form(""),
               user: str = Depends(require_admin)):
    _confirm_action(user, "files-move", src, confirm_token)
    try: return files.move(src, dst_dir)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/mkdir")
def files_mkdir(path: str = Form(...), name: str = Form(...),
                confirm_token: str = Form(""),
                user: str = Depends(require_admin)):
    _confirm_action(user, "files-mkdir", f"{path}/{name}", confirm_token)
    try: return files.mkdir(path, name)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/delete")
def files_delete(path: str = Form(...), confirm_token: str = Form(""),
                 user: str = Depends(require_admin)):
    _confirm_action(user, "files-delete", path, confirm_token)
    try: return files.move_to_trash(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/files/download")
def files_download(path: str, user: str = Depends(auth)):
    path = _file_response_path(path)
    return FileResponse(path, filename=os.path.basename(path))

@app.get("/api/files/preview")
def files_preview(path: str, user: str = Depends(auth)):
    path = _file_response_path(path)
    import mimetypes as mt; mime, _ = mt.guess_type(path)
    return FileResponse(path, media_type=mime or "application/octet-stream")

@app.post("/api/files/upload")
async def files_upload(path: str = Form(...), file: UploadFile = File(...),
                       confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    try:
        upload_dir = files._ok(path)
    except Exception as e:
        raise HTTPException(400, str(e))
    try:
        dest = validators.safe_join(upload_dir, os.path.basename(file.filename or ""))
    except Exception as e:
        raise HTTPException(400, str(e))
    _confirm_action(user, "files-upload", path, confirm_token)
    dest = files._unique_dst(dest)
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024): f.write(chunk)
    return {"ok": True, "path": dest}

@app.get("/api/files/info")
def files_info(path: str, user: str = Depends(auth)):
    return files.file_info(path)

@app.get("/api/files/search")
def files_search(base: str, q: str, user: str = Depends(auth)):
    try: return {"results": files.search(base, q)}
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/zip")
def files_zip(paths: str = Form(...), output: str = Form(...),
              confirm_token: str = Form(""),
              user: str = Depends(require_admin)):
    selected_paths = _parse_file_paths_form(paths)
    _confirm_action(user, "files-zip", output, confirm_token)
    try: return files.make_zip(selected_paths, output)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/unzip")
def files_unzip(path: str = Form(...), dst_dir: str = Form(...),
                confirm_token: str = Form(""),
                user: str = Depends(require_admin)):
    _confirm_action(user, "files-unzip", path, confirm_token)
    try: return files.extract_zip(path, dst_dir)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/job")
def files_job(action: str = Form(...), paths: str = Form(...),
              dst_dir: str = Form(""), output: str = Form(""),
              confirm_token: str = Form(""),
              user: str = Depends(require_admin)):
    action = _parse_choice(action, {"copy", "move", "delete", "zip"}, "File job action")
    selected_paths = _parse_file_paths_form(paths)
    _confirm_action(user, "files-job:" + action, "|".join(selected_paths)[:240], confirm_token)
    try:
        return files.start_job(action, selected_paths, dst_dir, output)
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/files/jobs")
def files_jobs(active: bool = False, user: str = Depends(auth)):
    return {"jobs": files.list_jobs(active)}

@app.get("/api/files/job")
def files_job_status(job_id: str, user: str = Depends(auth)):
    try:
        return files.get_job(job_id)
    except Exception as e:
        raise HTTPException(404, str(e))

@app.get("/api/files/trash")
def files_trash_list(user: str = Depends(auth)):
    return {"items": files.list_trash()}

@app.post("/api/files/trash/restore")
def files_trash_restore(item_id: str = Form(...), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    _confirm_action(user, "files-trash-restore", item_id, confirm_token)
    try: return files.restore_trash(item_id)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/trash/empty")
def files_trash_empty(confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    _confirm_action(user, "files-trash-empty", "trash", confirm_token)
    return files.empty_trash()

@app.post("/api/files/share")
def files_share(path: str = Form(...), confirm_token: str = Form(""),
                user: str = Depends(require_admin)):
    _confirm_action(user, "files-share-link", path, confirm_token)
    try: return files.create_share_link(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/files/shares")
def files_shares(user: str = Depends(auth)):
    return files.list_shares()

@app.get("/api/files/mounts")
def files_mounts(user: str = Depends(auth)):
    return {"mounts": files.list_mounts()}

@app.post("/api/files/shares/delete")
def files_share_delete(token: str = Form(...), confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    _confirm_action(user, "files-share-delete", token, confirm_token)
    return files.delete_share(token)

@app.get("/dl/{token}")
def files_public_download(token: str):
    share = files.resolve_share(token)
    if not share: raise HTTPException(404, "Link ungültig")
    return FileResponse(share["path"], filename=share["name"])

@app.post("/api/files/samba-share")
def files_samba_share(path: str = Form(...), name: str = Form(...),
                      writable: str = Form("true"),
                      confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    writable_value = _parse_bool_form(writable, "Writable-Wert")
    _confirm_action(user, "files-samba-share", name, confirm_token)
    from modules import shares as sh
    return sh.add_samba_share(name, path, writable_value)

@app.post("/api/files/mount-smb")
def files_mount_smb(server: str = Form(...), share_name: str = Form(...),
                    mountpoint: str = Form(...), username: str = Form("guest"),
                    password: str = Form(""), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    _confirm_action(user, "files-mount-smb", mountpoint, confirm_token)
    return files.mount_smb(server, share_name, mountpoint, username, password)

@app.post("/api/files/mount-nfs")
def files_mount_nfs(server: str = Form(...), export: str = Form(...),
                    mountpoint: str = Form(...), options: str = Form(""),
                    confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    _confirm_action(user, "files-mount-nfs", mountpoint, confirm_token)
    return files.mount_nfs(server, export, mountpoint, options)

# ============ Speicher (Disks/RAID/SMART) ============

# ============ Speicher (Disks/RAID/SMART) ============

@app.get("/api/storage/devices")
def storage_devices(user: str = Depends(auth)):
    return storage.list_block_devices()


@app.get("/api/storage/smart")
def storage_smart(device: str, user: str = Depends(auth)):
    return storage.smart_data(device)


@app.post("/api/storage/partition-table")
def storage_ptable(device: str = Form(...), label: str = Form("gpt"),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    label = _parse_choice(label, {"gpt", "msdos"}, "Partition table")
    device = validators.require_device(device)
    _confirm_action(user, "storage-partition-table", device, confirm_token)
    return storage.create_partition_table(device, label)


@app.post("/api/storage/partition")
def storage_partition(device: str = Form(...), confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    device = validators.require_device(device)
    _confirm_action(user, "storage-partition", device, confirm_token)
    return storage.create_partition(device)


@app.post("/api/storage/format")
def storage_format(partition: str = Form(...), fstype: str = Form("ext4"),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    fstype = _parse_choice(fstype, {"ext4", "xfs", "btrfs"}, "Filesystem type")
    partition = validators.require_device(partition)
    _confirm_action(user, "storage-format", partition, confirm_token)
    return storage.format_partition(partition, fstype)


@app.post("/api/storage/mount")
def storage_mount(partition: str = Form(...), mountpoint: str = Form(...),
                  persist: str = Form("false"), confirm_token: str = Form(""),
                  user: str = Depends(require_admin)):
    persist_mount = _parse_bool_form(persist, "Persist-Wert")
    partition = validators.require_device(partition)
    mountpoint = validators.guard_mountpoint(mountpoint)
    _confirm_action(user, "storage-mount", partition, confirm_token)
    return storage.mount_device(
        partition, mountpoint, persist_mount
    )


@app.post("/api/storage/unmount")
def storage_unmount(mountpoint: str = Form(...), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    mountpoint = validators.guard_mountpoint(mountpoint)
    _confirm_action(user, "storage-unmount", mountpoint, confirm_token)
    return storage.unmount_device(mountpoint)


@app.get("/api/storage/swap")
def storage_swap(user: str = Depends(auth)):
    return storage.list_swap()


@app.post("/api/storage/swap/create")
def storage_swap_create(path: str = Form(...), size_mb: str = Form(...),
                        persist: str = Form("false"),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    swap_size = validators.require_int_range(size_mb, 1, 1024 * 1024, "Swap size")
    persist_swap = _parse_bool_form(persist, "Persist-Wert")
    path = validators.guard_write_path(path)
    _confirm_action(user, "storage-swap-create", path, confirm_token)
    return storage.create_swapfile(
        path,
        swap_size,
        persist_swap,
    )


@app.post("/api/storage/swap/action")
def storage_swap_action(target: str = Form(...), action: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    action = _parse_choice(action, {"on", "off"}, "Swap action")
    if str(target or "").strip().startswith("/dev/"):
        target = validators.require_device(target)
    else:
        target = validators.guard_write_path(target)
    _confirm_action(user, "storage-swap-action:" + action, target,
                    confirm_token)
    return storage.swap_action(target, action)


@app.get("/api/storage/raid")
def storage_raid(user: str = Depends(auth)):
    return storage.list_raid()


@app.post("/api/storage/raid/create")
def storage_raid_create(name: str = Form(...), level: str = Form(...),
                        devices: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    name = validators.require_slug(name, "RAID name")
    raid_level = validators.require_int_range(level, 0, 10, "RAID level")
    if raid_level not in (0, 1, 5, 6, 10):
        raise ValueError("Invalid RAID level")
    device_list = _parse_device_csv(devices)
    _confirm_action(user, "storage-raid-create", name, confirm_token)
    return storage.create_raid(name, raid_level, device_list)


@app.get("/api/storage/lvm")
def storage_lvm(user: str = Depends(auth)):
    return storage.lvm_overview()


@app.post("/api/storage/lvm/vg-create")
def storage_lvm_vg(name: str = Form(...), devices: str = Form(...),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    name = validators.require_slug(name, "volume group name")
    device_list = _parse_device_csv(devices)
    _confirm_action(user, "storage-vg-create", name, confirm_token)
    return storage.vg_create(name, device_list)


@app.post("/api/storage/lvm/lv-create")
def storage_lvm_lv(vg: str = Form(...), name: str = Form(...),
                   size: str = Form(...), confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    vg = validators.require_slug(vg, "volume group name")
    name = validators.require_slug(name, "logical volume name")
    size = _parse_lvm_size(size, allow_percent=True)
    _confirm_action(user, "storage-lv-create", f"{vg}/{name}", confirm_token)
    return storage.lv_create(vg, name, size)


@app.post("/api/storage/lvm/lv-extend")
def storage_lvm_extend(lv_path: str = Form(...), size: str = Form(...),
                       confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    lv_path = validators.require_device(lv_path)
    size = _parse_lvm_size(size)
    _confirm_action(user, "storage-lv-extend", lv_path, confirm_token)
    return storage.lv_extend(lv_path, size)


@app.post("/api/storage/lvm/lv-remove")
def storage_lvm_remove(lv_path: str = Form(...),
                       confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    lv_path = validators.require_device(lv_path)
    _confirm_action(user, "storage-lv-remove", lv_path, confirm_token)
    return storage.lv_remove(lv_path)


# ---- LUKS-Verschlüsselung ----

@app.get("/api/storage/luks")
def storage_luks(user: str = Depends(auth)):
    return storage.luks_list()


@app.post("/api/storage/luks/format")
def storage_luks_format(device: str = Form(...), passphrase: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    device = validators.require_device(device)
    _confirm_action(user, "storage-luks-format", device, confirm_token)
    try:
        return storage.luks_format(device, passphrase)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/luks/open")
def storage_luks_open(device: str = Form(...), name: str = Form(...),
                      passphrase: str = Form(...),
                      confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    device = validators.require_device(device)
    name = validators.require_slug(name, "LUKS mapper name")
    _confirm_action(user, "storage-luks-open", device, confirm_token)
    try:
        return storage.luks_open(device, name, passphrase)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/luks/close")
def storage_luks_close(name: str = Form(...), confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    name = validators.require_slug(name, "LUKS mapper name")
    _confirm_action(user, "storage-luks-close", name, confirm_token)
    return storage.luks_close(name)


# ---- Dateisystem-Resize (non-LVM) ----

@app.post("/api/storage/fs-grow")
def storage_fs_grow(device: str = Form(""), mountpoint: str = Form(""),
                    size: str = Form("max"), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    if device:
        device = validators.require_device(device)
    if mountpoint:
        mountpoint = validators.guard_mountpoint(mountpoint)
    if size and size != "max":
        _parse_lvm_size(size)
    _confirm_action(user, "storage-fs-grow", device, confirm_token)
    try:
        return storage.fs_grow(device, mountpoint, size)
    except Exception as e:
        raise HTTPException(400, str(e))


# ---- ZFS-Pools ----

@app.get("/api/storage/zfs")
def storage_zfs(user: str = Depends(auth)):
    return {"pools": storage.zfs_pools(), "datasets": storage.zfs_datasets()}


@app.post("/api/storage/zfs/create")
def storage_zfs_create(name: str = Form(...), raid: str = Form("stripe"),
                       devices: str = Form(...),
                       confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    name = validators.require_slug(name, "ZFS pool name")
    raid = _parse_choice(
        raid, {"stripe", "mirror", "raidz", "raidz2", "raidz3"}, "ZFS RAID layout"
    )
    devs = _parse_device_csv(devices)
    _confirm_action(user, "storage-zfs-create", name, confirm_token)
    try:
        return storage.zpool_create(name, raid, devs)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/zfs/destroy")
def storage_zfs_destroy(name: str = Form(...), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    name = validators.require_slug(name, "ZFS pool name")
    _confirm_action(user, "storage-zfs-destroy", name, confirm_token)
    return storage.zpool_destroy(name)


@app.post("/api/storage/zfs/scrub")
def storage_zfs_scrub(name: str = Form(...), confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    name = validators.require_slug(name, "ZFS pool name")
    _confirm_action(user, "storage-zfs-scrub", name, confirm_token)
    return storage.zpool_scrub(name)


# ---- Btrfs-Pools ----

@app.get("/api/storage/btrfs")
def storage_btrfs(user: str = Depends(auth)):
    return storage.btrfs_filesystems()


@app.post("/api/storage/btrfs/create")
def storage_btrfs_create(label: str = Form(""), profile: str = Form("single"),
                         devices: str = Form(...),
                         confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    if label:
        label = validators.require_slug(label, "Btrfs label")
    profile = _parse_choice(
        profile, {"single", "raid0", "raid1", "raid10"}, "Btrfs profile"
    )
    devs = _parse_device_csv(devices)
    _confirm_action(user, "storage-btrfs-create", label or devices,
                    confirm_token)
    try:
        return storage.btrfs_create(label, profile, devs)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/btrfs/scrub")
def storage_btrfs_scrub(mountpoint: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    mountpoint = validators.guard_mountpoint(mountpoint)
    _confirm_action(user, "storage-btrfs-scrub", mountpoint, confirm_token)
    return storage.btrfs_scrub(mountpoint)


# ---- iSCSI-Initiator ----

@app.get("/api/storage/iscsi")
def storage_iscsi(user: str = Depends(auth)):
    return storage.iscsi_sessions()


@app.post("/api/storage/iscsi/discover")
def storage_iscsi_discover(portal: str = Form(...), confirm_token: str = Form(""),
                           user: str = Depends(require_admin)):
    _confirm_action(user, "storage-iscsi-discover", portal, confirm_token)
    return storage.iscsi_discover(portal)


@app.post("/api/storage/iscsi/login")
def storage_iscsi_login(portal: str = Form(...), target: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    _confirm_action(user, "storage-iscsi-login", f"{portal}/{target}",
                    confirm_token)
    return storage.iscsi_login(portal, target)


@app.post("/api/storage/iscsi/logout")
def storage_iscsi_logout(portal: str = Form(...), target: str = Form(...),
                         confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    _confirm_action(user, "storage-iscsi-logout", f"{portal}/{target}",
                    confirm_token)
    return storage.iscsi_logout(portal, target)


# ============ Docker ============

@app.get("/api/docker/available")
def docker_available(user: str = Depends(auth)):
    return {"available": docker_mgr.available()}


@app.get("/api/docker/containers")
def docker_containers(user: str = Depends(auth)):
    return docker_mgr.list_containers()


@app.post("/api/docker/action")
def docker_action(container_id: str = Form(...), action: str = Form(...),
                  confirm_token: str = Form(""),
                  user: str = Depends(require_admin)):
    action = _parse_choice(
        action, {"start", "stop", "restart", "remove"}, "Docker action"
    )
    if action == "remove":
        _confirm_action(user, "docker-container-remove", container_id,
                        confirm_token)
    else:
        _confirm_action(user, "docker-container-action:" + action,
                        container_id, confirm_token)
    return docker_mgr.container_action(container_id, action)


@app.get("/api/docker/logs")
def docker_logs(container_id: str, user: str = Depends(auth)):
    return docker_mgr.container_logs(container_id)


@app.get("/api/docker/stats")
def docker_stats(container_id: str, user: str = Depends(auth)):
    return docker_mgr.container_stats(container_id)


@app.post("/api/docker/create")
def docker_create(image: str = Form(...), name: str = Form(""),
                  ports: str = Form(""), volumes: str = Form(""),
                  env: str = Form(""), restart: str = Form("no"),
                  cpus: str = Form(""), memory: str = Form(""),
                  confirm_token: str = Form(""),
                  user: str = Depends(require_admin)):
    _confirm_action(user, "docker-container-create", name or image,
                    confirm_token)
    return docker_mgr.create_container(image, name, ports, volumes, env,
                                       restart, cpus, memory)


@app.post("/api/docker/update")
def docker_update(container_id: str = Form(...), cpus: str = Form(""),
                  memory: str = Form(""), confirm_token: str = Form(""),
                  user: str = Depends(require_admin)):
    _confirm_action(user, "docker-container-update", container_id,
                    confirm_token)
    try:
        return docker_mgr.update_container(container_id, cpus, memory)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/docker/search")
def docker_search(term: str, user: str = Depends(auth)):
    try:
        return docker_mgr.search_images(term)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/docker/images")
def docker_images(user: str = Depends(auth)):
    return docker_mgr.list_images()


@app.post("/api/docker/images/pull")
def docker_pull(name: str = Form(...), confirm_token: str = Form(""),
                user: str = Depends(require_admin)):
    _confirm_action(user, "docker-image-pull", name, confirm_token)
    return docker_mgr.pull_image(name)


@app.post("/api/docker/images/remove")
def docker_image_remove(image_id: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    _confirm_action(user, "docker-image-remove", image_id, confirm_token)
    return docker_mgr.remove_image(image_id)


@app.get("/api/docker/volumes")
def docker_volumes(user: str = Depends(auth)):
    return docker_mgr.list_volumes()


@app.post("/api/docker/volumes/remove")
def docker_volume_remove(name: str = Form(...), confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    _confirm_action(user, "docker-volume-remove", name, confirm_token)
    try:
        return docker_mgr.remove_volume(name)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/docker/compose")
def docker_compose_list(user: str = Depends(auth)):
    return docker_mgr.list_compose_projects()


@app.get("/api/docker/compose/get")
def docker_compose_get(name: str, user: str = Depends(auth)):
    return docker_mgr.get_compose(name)


@app.post("/api/docker/compose/save")
def docker_compose_save(name: str = Form(...), content: str = Form(...),
                        env_enabled: str = Form("false"),
                        env_content: str = Form(""),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    env_file_enabled = _parse_bool_form(env_enabled, "Env-Wert")
    _confirm_action(user, "docker-compose-save", name, confirm_token)
    return docker_mgr.save_compose(
        name, content, env_file_enabled, env_content
    )


@app.post("/api/docker/compose/action")
def docker_compose_action(name: str = Form(...), action: str = Form(...),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    action = _parse_choice(action, {"up", "down", "restart"}, "Compose action")
    _confirm_action(user, "docker-compose-action:" + action, name,
                    confirm_token)
    return docker_mgr.compose_action(name, action)


@app.post("/api/docker/compose/remove")
def docker_compose_remove(name: str = Form(...),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    _confirm_action(user, "docker-compose-remove", name, confirm_token)
    try:
        return docker_mgr.remove_compose_project(name)
    except Exception as e:
        raise HTTPException(400, str(e))


# ============ Services ============

@app.get("/api/services/list")
def services_list(user: str = Depends(auth)):
    return services.list_services()


@app.post("/api/services/action")
def services_action(name: str = Form(...), action: str = Form(...),
                    confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    action = _parse_choice(
        action, {"start", "stop", "restart", "enable", "disable"}, "Service action"
    )
    _confirm_action(user, "service-action:" + action, name, confirm_token)
    return services.service_action(name, action)


@app.get("/api/services/logs")
def services_logs(name: str, lines: int = 100, user: str = Depends(auth)):
    return services.service_logs(name, lines)


# ============ VMs ============

@app.get("/api/vms/available")
def vms_available(user: str = Depends(auth)):
    return {"available": vms.available()}


@app.get("/api/vms/list")
def vms_list(user: str = Depends(auth)):
    return vms.list_vms()


@app.post("/api/vms/action")
def vms_action(name: str = Form(...), action: str = Form(...),
               confirm_token: str = Form(""),
               user: str = Depends(require_admin)):
    action = _parse_choice(
        action,
        {"start", "shutdown", "reboot", "force-off", "autostart-on",
         "autostart-off", "delete"},
        "VM action",
    )
    if action == "delete":
        _confirm_action(user, "vm-delete", name, confirm_token)
    else:
        _confirm_action(user, "vm-action:" + action, name, confirm_token)
    return vms.vm_action(name, action)


@app.get("/api/vms/isos")
def vms_isos(user: str = Depends(auth)):
    return {"isos": vms.list_isos()}


@app.get("/api/vms/networks")
def vms_networks(user: str = Depends(auth)):
    return vms.list_networks()


@app.get("/api/vms/pools")
def vms_pools(user: str = Depends(auth)):
    return vms.pool_details()


@app.get("/api/vms/hardware")
def vms_hardware(name: str, user: str = Depends(auth)):
    return vms.list_hardware(name)


@app.post("/api/vms/disk/attach")
def vms_disk_attach(name: str = Form(...), source: str = Form(...),
                    target: str = Form(...), bus: str = Form("virtio"),
                    confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    bus = _parse_choice(bus, {"virtio", "sata", "scsi", "ide"}, "VM disk bus")
    _confirm_action(user, "vm-disk-attach", f"{name}/{target}", confirm_token)
    return vms.attach_disk(name, source, target, bus)


@app.post("/api/vms/disk/detach")
def vms_disk_detach(name: str = Form(...), target: str = Form(...),
                    confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    _confirm_action(user, "vm-disk-detach", f"{name}/{target}", confirm_token)
    return vms.detach_disk(name, target)


@app.post("/api/vms/nic/attach")
def vms_nic_attach(name: str = Form(...), network: str = Form(...),
                   model: str = Form("virtio"),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    model = _parse_choice(model, {"virtio", "e1000", "rtl8139"}, "VM NIC model")
    _confirm_action(user, "vm-nic-attach", f"{name}/{network}", confirm_token)
    return vms.attach_nic(name, network, model)


@app.post("/api/vms/nic/detach")
def vms_nic_detach(name: str = Form(...), type: str = Form(...),
                   mac: str = Form(...), confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    type = _parse_choice(type, {"network", "bridge", "direct"}, "VM NIC type")
    _confirm_action(user, "vm-nic-detach", f"{name}/{mac}", confirm_token)
    return vms.detach_nic(name, type, mac)


@app.post("/api/vms/pool/create")
def vms_pool_create(name: str = Form(...), ptype: str = Form("dir"),
                    target: str = Form(...), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    ptype = _parse_choice(ptype, {"dir", "fs"}, "VM pool type")
    _confirm_action(user, "vm-pool-create", name, confirm_token)
    return vms.pool_create(name, ptype, target)


@app.post("/api/vms/pool/action")
def vms_pool_action(name: str = Form(...), action: str = Form(...),
                    confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    action = _parse_choice(
        action,
        {"start", "stop", "autostart-on", "autostart-off", "delete"},
        "VM pool action",
    )
    if action == "delete":
        _confirm_action(user, "vm-pool-delete", name, confirm_token)
    else:
        _confirm_action(user, "vm-pool-action:" + action, name,
                        confirm_token)
    return vms.pool_action(name, action)


@app.get("/api/vms/pool/volumes")
def vms_pool_volumes(pool: str, user: str = Depends(auth)):
    return vms.pool_volumes(pool)


@app.post("/api/vms/pool/vol-create")
def vms_vol_create(pool: str = Form(...), name: str = Form(...),
                   size_gb: str = Form(...), format: str = Form("qcow2"),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    format = _parse_choice(format, {"qcow2", "raw"}, "VM volume format")
    size = validators.require_int_range(
        size_gb, vms.VM_DISK_MIN_GB, vms.VM_DISK_MAX_GB, "VM volume size"
    )
    _confirm_action(user, "vm-volume-create", f"{pool}/{name}", confirm_token)
    return vms.vol_create(
        pool,
        name,
        size,
        format,
    )


@app.post("/api/vms/pool/vol-delete")
def vms_vol_delete(pool: str = Form(...), vol: str = Form(...),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    _confirm_action(user, "vm-volume-delete", f"{pool}/{vol}", confirm_token)
    return vms.vol_delete(pool, vol)


@app.post("/api/vms/clone")
def vms_clone(name: str = Form(...), newname: str = Form(...),
              confirm_token: str = Form(""),
              user: str = Depends(require_admin)):
    _confirm_action(user, "vm-clone", f"{name}/{newname}", confirm_token)
    return vms.clone_vm(name, newname)


@app.post("/api/vms/cdrom")
def vms_cdrom(name: str = Form(...), iso: str = Form(""),
              confirm_token: str = Form(""),
              user: str = Depends(require_admin)):
    _confirm_action(user, "vm-cdrom", name, confirm_token)
    return vms.change_cdrom(name, iso)


@app.post("/api/vms/create")
def vms_create(name: str = Form(...), memory_mb: str = Form(...),
               vcpus: str = Form(...), disk_gb: str = Form(...),
               iso: str = Form(""), network: str = Form("default"),
               confirm_token: str = Form(""),
               user: str = Depends(require_admin)):
    memory = validators.require_int_range(
        memory_mb, vms.VM_MEMORY_MIN_MB, vms.VM_MEMORY_MAX_MB, "VM memory"
    )
    cpu_count = validators.require_int_range(
        vcpus, vms.VM_VCPU_MIN, vms.VM_VCPU_MAX, "VM vCPUs"
    )
    disk_size = validators.require_int_range(
        disk_gb, vms.VM_DISK_MIN_GB, vms.VM_DISK_MAX_GB, "VM disk size"
    )
    _confirm_action(user, "vm-create", name, confirm_token)
    return vms.create_vm(
        name,
        memory,
        cpu_count,
        disk_size,
        iso,
        network,
    )


@app.get("/api/vms/snapshots")
def vms_snapshots(name: str, user: str = Depends(auth)):
    return vms.list_snapshots(name)


@app.post("/api/vms/snapshot")
def vms_snapshot(name: str = Form(...), snap_name: str = Form(...),
                 confirm_token: str = Form(""),
                 user: str = Depends(require_admin)):
    _confirm_action(user, "vm-snapshot-create", f"{name}/{snap_name}",
                    confirm_token)
    return vms.create_snapshot(name, snap_name)


@app.post("/api/vms/snapshot/action")
def vms_snapshot_action(name: str = Form(...), snap_name: str = Form(...),
                        action: str = Form(...), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    action = _parse_choice(action, {"revert", "delete"}, "VM snapshot action")
    if action == "delete":
        _confirm_action(user, "vm-snapshot-delete", f"{name}/{snap_name}",
                        confirm_token)
    else:
        _confirm_action(user, "vm-snapshot-action:" + action,
                        f"{name}/{snap_name}", confirm_token)
    return vms.snapshot_action(name, snap_name, action)


# ============ Backup ============

@app.get("/api/backup/jobs")
def backup_jobs(user: str = Depends(auth)):
    return backup.list_jobs()


@app.post("/api/backup/add")
def backup_add(name: str = Form(...), source: str = Form(...),
               dest: str = Form(...), schedule: str = Form("manual"),
               confirm_token: str = Form(""),
               user: str = Depends(require_admin)):
    _confirm_action(user, "backup-add", name, confirm_token)
    return backup.add_job(name, source, dest, schedule)


@app.post("/api/backup/run")
def backup_run(job_id: str = Form(...), confirm_token: str = Form(""),
               user: str = Depends(require_admin)):
    job_id_value = validators.require_int_range(job_id, 1, 2**31 - 1, "Backup job")
    _confirm_action(user, "backup-run", str(job_id_value), confirm_token)
    return backup.run_job(job_id_value)


@app.get("/api/backup/history")
def backup_history(user: str = Depends(auth)):
    return backup.get_history()


# ============ Freigaben ============

@app.get("/api/shares/samba")
def shares_samba(user: str = Depends(auth)):
    return shares.list_samba_shares()


@app.post("/api/shares/samba/add")
def shares_samba_add(name: str = Form(...), path: str = Form(...),
                     writable: str = Form("true"),
                     confirm_token: str = Form(""),
                     user: str = Depends(require_admin)):
    writable_value = _parse_bool_form(writable, "Writable-Wert")
    _confirm_action(user, "share-samba-add", name, confirm_token)
    return shares.add_samba_share(
        name, path, writable_value
    )


@app.get("/api/shares/nfs")
def shares_nfs(user: str = Depends(auth)):
    return shares.list_nfs_exports()


@app.post("/api/shares/nfs/add")
def shares_nfs_add(path: str = Form(...), clients: str = Form("*"),
                   options: str = Form("rw,sync,no_subtree_check"),
                   confirm_token: str = Form(""),
                   user: str = Depends(require_admin)):
    _confirm_action(user, "share-nfs-add", path, confirm_token)
    return shares.add_nfs_export(path, clients, options)


@app.get("/api/shares/ftp")
def shares_ftp(user: str = Depends(auth)):
    return shares.ftp_status()


# ============ Netzwerk ============

@app.get("/api/network/interfaces")
def network_interfaces(user: str = Depends(auth)):
    result = network.list_interfaces()
    ifaces = result if isinstance(result, list) else result.get("interfaces", [])
    for i in ifaces:
        if i["is_bond"]:
            i["members"] = network.bond_members(i["name"])
    if isinstance(result, list):
        return result
    result["interfaces"] = ifaces
    return result


@app.post("/api/network/bond/create")
def network_bond_create(name: str = Form(...), members: str = Form(...),
                        mode: str = Form("802.3ad"),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    mode = _parse_choice(
        mode, {"balance-rr", "active-backup", "802.3ad"}, "Bond mode"
    )
    _confirm_action(user, "network-bond-create", name, confirm_token)
    return network.create_bond(name, members.split(","), mode)


@app.post("/api/network/bond/delete")
def network_bond_delete(name: str = Form(...), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    _confirm_action(user, "network-bond-delete", name, confirm_token)
    return network.delete_bond(name)


@app.get("/api/network/firewall")
def network_firewall(user: str = Depends(auth)):
    return network.firewall_status()


@app.get("/api/network/firewall/rules")
def network_firewall_rules(user: str = Depends(auth)):
    return network.firewall_rules()


@app.post("/api/network/firewall/add")
def network_firewall_add(port: str = Form(...), proto: str = Form("tcp"),
                         action: str = Form("allow"),
                         confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    port_value = validators.require_int_range(port, 1, 65535, "Firewall port")
    proto = _parse_choice(proto, {"tcp", "udp"}, "Firewall protocol")
    action = _parse_choice(
        action, {"allow", "deny", "reject", "limit"}, "Firewall action"
    )
    _confirm_action(user, "network-firewall-add", str(port_value), confirm_token)
    return network.firewall_add_rule(port_value, proto, action)


@app.post("/api/network/firewall/remove")
def network_firewall_remove(num: str = Form(...),
                            confirm_token: str = Form(""),
                            user: str = Depends(require_admin)):
    rule_num = validators.require_int_range(num, 1, 99999, "Firewall rule")
    _confirm_action(user, "network-firewall-remove", str(rule_num), confirm_token)
    return network.firewall_remove_rule(rule_num)


@app.post("/api/network/configure-ip")
def network_configure_ip(iface: str = Form(...), mode: str = Form("static"),
                         ip: str = Form(""), netmask: str = Form("24"),
                         gateway: str = Form(""), dns: str = Form(""),
                         persist: str = Form("false"),
                         confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    mode = _parse_choice(mode, {"static", "dhcp"}, "Network mode")
    persist_network = _parse_bool_form(persist, "Persist-Wert")
    _confirm_action(user, "network-configure-ip", iface, confirm_token)
    return network.configure_ip(
        iface, mode, ip, netmask, gateway, dns, persist_network,
    )


@app.post("/api/network/bridge/create")
def network_bridge_create(name: str = Form(...), members: str = Form(""),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    _confirm_action(user, "network-bridge-create", name, confirm_token)
    return network.create_bridge(name, [m for m in members.split(",") if m])


@app.post("/api/network/vlan/create")
def network_vlan_create(parent: str = Form(...), vlan_id: str = Form(...),
                        name: str = Form(""), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    vlan_value = validators.require_int_range(vlan_id, 1, 4094, "VLAN ID")
    _confirm_action(user, "network-vlan-create", name or f"{parent}.{vlan_value}",
                    confirm_token)
    return network.create_vlan(parent, vlan_value, name)


@app.post("/api/network/link/delete")
def network_link_delete(name: str = Form(...), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    _confirm_action(user, "network-link-delete", name, confirm_token)
    return network.delete_link(name)


# ============ Sicherheit ============

@app.get("/api/security/users")
def security_users(user: str = Depends(auth)):
    return security.list_users()


@app.post("/api/security/users/add")
def security_users_add(name: str = Form(...), confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    _confirm_action(user, "security-user-add", name, confirm_token)
    return security.add_user(name)


@app.post("/api/security/users/smb-password")
def security_smb_password(name: str = Form(...), password: str = Form(...),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    _confirm_action(user, "security-smb-password", name, confirm_token)
    return security.set_smb_password(name, password)


@app.get("/api/security/smb-users")
def security_smb_users(user: str = Depends(auth)):
    return security.list_smb_users()


@app.post("/api/security/users/password")
def security_password(name: str = Form(...), password: str = Form(...),
                      confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    _confirm_action(user, "security-user-password", name, confirm_token)
    return security.set_password(name, password)


@app.get("/api/security/users/ssh-keys")
def security_ssh_keys(name: str, user: str = Depends(auth)):
    return {"keys": security.list_ssh_keys(name)}


@app.post("/api/security/users/ssh-keys/add")
def security_ssh_keys_add(name: str = Form(...), key: str = Form(...),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    _confirm_action(user, "security-ssh-key-add", name, confirm_token)
    return security.add_ssh_key(name, key)


@app.post("/api/security/users/ssh-keys/remove")
def security_ssh_keys_remove(name: str = Form(...), key: str = Form(...),
                             confirm_token: str = Form(""),
                             user: str = Depends(require_admin)):
    _confirm_action(user, "security-ssh-key-remove", name, confirm_token)
    return security.remove_ssh_key(name, key)


@app.get("/api/security/users/secinfo")
def security_user_secinfo(name: str, user: str = Depends(auth)):
    return security.user_security(name)


@app.post("/api/security/users/sudo")
def security_user_sudo(name: str = Form(...), enable: str = Form(...),
                       nopasswd: str = Form("false"),
                       confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    enable_sudo = _parse_bool_form(enable, "sudo-Wert")
    nopasswd_sudo = _parse_bool_form(nopasswd, "NOPASSWD-Wert")
    _confirm_action(user, "security-sudo", name, confirm_token)
    return security.set_sudo(name, enable_sudo, nopasswd_sudo)


@app.post("/api/security/users/aging")
def security_user_aging(name: str = Form(...), max_days: str = Form(""),
                        min_days: str = Form(""), warn_days: str = Form(""),
                        expire: str = Form(""),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    _confirm_action(user, "security-aging", name, confirm_token)
    return security.set_password_aging(name, max_days, min_days, warn_days, expire)


@app.post("/api/security/users/expire")
def security_user_expire(name: str = Form(...), confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    _confirm_action(user, "security-expire", name, confirm_token)
    return security.expire_password_now(name)


@app.post("/api/security/groups/add")
def security_group_add(name: str = Form(...), confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    _confirm_action(user, "security-group-add", name, confirm_token)
    return security.add_group(name)


@app.post("/api/security/groups/delete")
def security_group_delete(name: str = Form(...),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    _confirm_action(user, "security-group-delete", name, confirm_token)
    return security.delete_group(name)


@app.post("/api/security/groups/add-member")
def security_group_add_member(group: str = Form(...), member: str = Form(...),
                              confirm_token: str = Form(""),
                              user: str = Depends(require_admin)):
    _confirm_action(user, "security-group-add-member", f"{group}/{member}",
                    confirm_token)
    return security.add_to_group(member, group)


@app.post("/api/security/groups/remove-member")
def security_group_remove_member(group: str = Form(...), member: str = Form(...),
                                 confirm_token: str = Form(""),
                                 user: str = Depends(require_admin)):
    _confirm_action(user, "security-group-remove-member", f"{group}/{member}",
                    confirm_token)
    return security.remove_from_group(member, group)


@app.get("/api/security/groups")
def security_groups(user: str = Depends(auth)):
    return security.list_groups()


@app.get("/api/security/certs")
def security_certs(user: str = Depends(auth)):
    return security.list_certificates()


@app.post("/api/security/certs/generate")
def security_cert_gen(common_name: str = Form(...),
                      confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    _confirm_action(user, "security-cert-generate", common_name, confirm_token)
    return security.generate_self_signed(common_name)


# ============ Monitoring ============

@app.get("/api/monitoring/logs")
def monitoring_logs(source: str = "syslog", lines: int = 200,
                    priority: str = "", unit: str = "", grep: str = "",
                    user: str = Depends(auth)):
    return monitoring.get_logs(source, lines=lines, priority=priority,
                               unit=unit, grep=grep)


@app.get("/api/monitoring/alerts")
def monitoring_alerts(user: str = Depends(auth)):
    return monitoring.list_alert_rules()


@app.post("/api/monitoring/alerts/add")
def monitoring_alert_add(metric: str = Form(...), threshold: str = Form(...),
                         channel: str = Form(...),
                         confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    threshold_value = _parse_float_range(threshold, 0, 100000, "Alert threshold")
    _confirm_action(user, "monitoring-alert-add", metric, confirm_token)
    return monitoring.add_alert_rule(
        metric, threshold_value, channel
    )


@app.get("/api/monitoring/alerts/history")
def monitoring_alert_history(user: str = Depends(auth)):
    return monitoring.get_alert_history()


@app.get("/api/monitoring/audit")
def monitoring_audit(lines: int = 200, user: str = Depends(auth)):
    lines = validators.require_int_range(lines, 1, 5000, "lines")
    path = audit.audit_log()
    try:
        with open(path) as f:
            raw = f.read().splitlines()[-lines:]
    except OSError:
        raw = []
    events = []
    for ln in reversed(raw):
        try:
            events.append(json.loads(ln))
        except Exception:
            pass
    return {"events": events}


# ============ System-Management ============

@app.get("/api/sysmgr/updates")
def sysmgr_updates(request: Request, refresh: bool = False,
                   user: str = Depends(auth)):
    if refresh:
        require_admin(request)
    return system_mgr.check_updates(refresh=refresh)


@app.get("/api/sysmgr/upgradable")
def sysmgr_upgradable(user: str = Depends(auth)):
    return system_mgr.list_upgradable()


@app.get("/api/sysmgr/runvard-release")
def sysmgr_runvard_release(user: str = Depends(auth)):
    return system_mgr.runvard_release_status()


@app.post("/api/sysmgr/runvard-update/apply")
def sysmgr_runvard_update_apply(confirm_token: str = Form(""),
                                user: str = Depends(require_admin)):
    _confirm_action(user, "sysmgr-runvard-update", "runvard", confirm_token)
    return system_mgr.start_runvard_update()


@app.get("/api/sysmgr/runvard-update/log")
def sysmgr_runvard_update_log(user: str = Depends(auth)):
    return system_mgr.runvard_update_log()


@app.post("/api/sysmgr/updates/apply")
def sysmgr_updates_apply(confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    _confirm_action(user, "sysmgr-updates-apply", "apt-upgrade",
                    confirm_token)
    from modules import jobs
    return jobs.start_job("apt-upgrade", system_mgr.apply_updates)


@app.get("/api/sysmgr/updates/job")
def sysmgr_updates_job(id: str, user: str = Depends(auth)):
    from modules import jobs
    try:
        return jobs.get_job(id)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


@app.get("/api/sysmgr/cron")
def sysmgr_cron(user: str = Depends(auth)):
    return system_mgr.list_cron_jobs()


@app.post("/api/sysmgr/cron/add")
def sysmgr_cron_add(schedule: str = Form(...), command: str = Form(...),
                    confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    _confirm_action(user, "sysmgr-cron-add", schedule, confirm_token)
    return system_mgr.add_cron_job(schedule, command)


@app.post("/api/sysmgr/power")
def sysmgr_power(action: str = Form(...), delay: str = Form("0"),
                 confirm_token: str = Form(""),
                 user: str = Depends(require_admin)):
    action = _parse_choice(action, {"shutdown", "reboot", "cancel"}, "Power action")
    delay_min = validators.require_int_range(delay, 0, 1440, "Power delay")
    _confirm_action(user, "power:" + action, action, confirm_token)
    return system_mgr.power_action(action, delay_min)


@app.post("/api/sysmgr/hostname")
def sysmgr_hostname(name: str = Form(...), confirm_token: str = Form(""),
                    user: str = Depends(require_admin)):
    _confirm_action(user, "sysmgr-hostname", name, confirm_token)
    return system_mgr.set_hostname(name)


@app.get("/api/sysmgr/gpu")
def sysmgr_gpu(user: str = Depends(auth)):
    return system_mgr.gpu_info()


@app.get("/api/sysmgr/apparmor")
def sysmgr_apparmor(user: str = Depends(auth)):
    return system_mgr.apparmor_status()


@app.post("/api/sysmgr/apparmor/set")
def sysmgr_apparmor_set(profile: str = Form(...), mode: str = Form(...),
                        confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    mode = _parse_choice(mode, {"enforce", "complain", "disable"}, "AppArmor mode")
    _confirm_action(user, "sysmgr-apparmor-set", profile, confirm_token)
    return system_mgr.apparmor_set(profile, mode)


@app.get("/api/sysmgr/packages/search")
def sysmgr_pkg_search(q: str, user: str = Depends(auth)):
    return system_mgr.pkg_search(q)


@app.post("/api/sysmgr/packages/install")
def sysmgr_pkg_install(name: str = Form(...), confirm_token: str = Form(""),
                       user: str = Depends(require_admin)):
    name = validators.require_apt_package_name(name)
    _confirm_action(user, "sysmgr-package-install", name, confirm_token)
    from modules import jobs
    return jobs.start_job("apt-install", system_mgr.pkg_install, name)


@app.post("/api/sysmgr/packages/remove")
def sysmgr_pkg_remove(name: str = Form(...), confirm_token: str = Form(""),
                      user: str = Depends(require_admin)):
    name = validators.require_apt_package_name(name)
    _confirm_action(user, "sysmgr-package-remove", name, confirm_token)
    from modules import jobs
    return jobs.start_job("apt-remove", system_mgr.pkg_remove, name)


@app.get("/api/sysmgr/job")
def sysmgr_job(id: str, user: str = Depends(auth)):
    from modules import jobs
    try:
        return jobs.get_job(id)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


# ---- Wartung: unattended-upgrades / tuned / kdump / sosreport ----

@app.get("/api/sysmgr/unattended")
def sysmgr_unattended(user: str = Depends(auth)):
    return system_mgr.unattended_status()


@app.post("/api/sysmgr/unattended/set")
def sysmgr_unattended_set(enable: str = Form(...),
                          auto_reboot: str = Form("false"),
                          reboot_time: str = Form("02:00"),
                          confirm_token: str = Form(""),
                          user: str = Depends(require_admin)):
    enabled = _parse_bool_form(enable, "Unattended-Wert")
    auto_reboot_enabled = _parse_bool_form(auto_reboot, "Auto-Reboot-Wert")
    _confirm_action(user, "sysmgr-unattended-set", "unattended-upgrades",
                    confirm_token)
    return system_mgr.unattended_set(
        enabled,
        auto_reboot_enabled,
        reboot_time,
    )


@app.get("/api/sysmgr/tuned")
def sysmgr_tuned(user: str = Depends(auth)):
    return system_mgr.tuned_status()


@app.post("/api/sysmgr/tuned/set")
def sysmgr_tuned_set(profile: str = Form(...), confirm_token: str = Form(""),
                     user: str = Depends(require_admin)):
    _confirm_action(user, "sysmgr-tuned-set", profile, confirm_token)
    return system_mgr.tuned_set(profile)


@app.get("/api/sysmgr/kdump")
def sysmgr_kdump(user: str = Depends(auth)):
    return system_mgr.kdump_status()


@app.post("/api/sysmgr/kdump/action")
def sysmgr_kdump_action(action: str = Form(...), confirm_token: str = Form(""),
                        user: str = Depends(require_admin)):
    action = _parse_choice(action, {"start", "stop", "enable", "disable"}, "Kdump action")
    _confirm_action(user, "sysmgr-kdump-action:" + action, "kdump",
                    confirm_token)
    return system_mgr.kdump_action(action)


@app.get("/api/sysmgr/sosreport")
def sysmgr_sosreport(user: str = Depends(auth)):
    return system_mgr.sosreport_list()


@app.post("/api/sysmgr/sosreport/run")
def sysmgr_sosreport_run(confirm_token: str = Form(""),
                         user: str = Depends(require_admin)):
    _confirm_action(user, "sysmgr-sosreport-run", "sosreport", confirm_token)
    from modules import jobs
    return jobs.start_job("sosreport", system_mgr.sosreport_run)


# ============ Apps (App-Store) ============

@app.get("/api/apps/catalog")
def apps_catalog(user: str = Depends(auth)):
    return apps.get_catalog()


@app.get("/api/apps/get")
def apps_get(app_id: str, user: str = Depends(auth)):
    try:
        return apps.get_app(app_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/apps/install")
def apps_install(app_id: str = Form(...), content: str = Form(...),
                 confirm_token: str = Form(""),
                 user: str = Depends(require_admin)):
    _confirm_action(user, "apps-install", app_id, confirm_token)
    try:
        return apps.install(app_id, content)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps/install-status")
def apps_install_status(job_id: str, user: str = Depends(auth)):
    return apps.install_status(job_id)


@app.post("/api/apps/action")
def apps_action(app_id: str = Form(...), action: str = Form(...),
                confirm_token: str = Form(""),
                user: str = Depends(require_admin)):
    action = _parse_choice(
        action, {"start", "stop", "restart", "down", "update"}, "App action"
    )
    _confirm_action(user, "apps-action:" + action, app_id, confirm_token)
    try:
        return apps.action(app_id, action)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps/check-updates")
def apps_check_updates(request: Request, force: bool = False,
                       user: str = Depends(auth)):
    if force:
        require_admin(request)
    return apps.check_updates(force=force)


# ============ Dashboard ============

@app.get("/api/dashboard")
def dashboard_get(user: str = Depends(auth)):
    return dashboard.get_dashboard()


@app.post("/api/dashboard/add")
def dashboard_add(tile_type: str = Form(...), tile_id: str = Form(...),
                  name: str = Form(""), url: str = Form(""),
                  icon: str = Form(""), port: str = Form("0"),
                  user: str = Depends(require_admin)):
    return dashboard.add_tile(
        tile_type,
        tile_id,
        name,
        url,
        icon,
        validators.require_int_range(port, 0, 65535, "Dashboard port"),
    )


@app.post("/api/dashboard/remove")
def dashboard_remove(tile_id: str = Form(...), user: str = Depends(require_admin)):
    return dashboard.remove_tile(tile_id)


@app.post("/api/dashboard/order")
def dashboard_order(order: str = Form(...), user: str = Depends(require_admin)):
    return dashboard.save_order(_parse_dashboard_order_form(order))


@app.post("/api/dashboard/toggle-url")
def dashboard_toggle_url(tile_id: str = Form(...), show: str = Form(...),
                         user: str = Depends(require_admin)):
    return dashboard.toggle_url(tile_id, _parse_bool_form(show, "show-Wert"))


@app.post("/api/dashboard/update")
def dashboard_update(tile_id: str = Form(...), name: str = Form(""),
                     url: str = Form(""), icon: str = Form(""),
                     host: str = Form(None),
                     user: str = Depends(require_admin)):
    return dashboard.update_tile(tile_id, name or None, url or None, icon or None, host)


# ============ WebSocket Terminal ============


async def _require_ws_admin(websocket: WebSocket) -> bool:
    """Return True when the WebSocket may open an interactive session."""
    if login_enabled():
        parsed = _parse_token(websocket.cookies.get(COOKIE_NAME))
        if not parsed or parsed[1] == "readonly":
            await websocket.close(code=1008)
            return False
    return True


async def _run_terminal_websocket(websocket: WebSocket,
                                  session: terminal.TerminalSession) -> None:
    """Accept a WebSocket and bridge it to a PTY terminal session."""
    await websocket.accept()
    try:
        session.start()
    except Exception as e:
        await websocket.send_text(f"\r\nFehler: {e}\r\n")
        await websocket.close()
        return

    reader = asyncio.create_task(terminal.pty_to_ws(session, websocket))
    try:
        while True:
            msg = await websocket.receive_text()
            if not _apply_terminal_ws_message(session, msg):
                await websocket.close(code=1003)
                break
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        session.kill()


@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket):
    if not await _require_ws_admin(websocket):
        return
    await _run_terminal_websocket(websocket, terminal.TerminalSession())


@app.websocket("/ws/docker-exec")
async def ws_docker_exec(websocket: WebSocket):
    if not await _require_ws_admin(websocket):
        return
    try:
        cid = validators.require_slug(websocket.query_params.get("id", ""), "container")
    except ValueError:
        await websocket.close(code=1008)
        return
    await _run_terminal_websocket(
        websocket,
        terminal.TerminalSession(
            cwd="/", argv=["docker", "exec", "-it", cid, "/bin/sh"]
        ),
    )


@app.websocket("/ws/btop")
async def ws_btop(websocket: WebSocket):
    """Startet btop direkt im PTY (Fallback btop -> htop -> top), ohne Eingabe-Rennen."""
    if not await _require_ws_admin(websocket):
        return
    await _run_terminal_websocket(
        websocket,
        terminal.TerminalSession(
            cwd="/",
            argv=["/bin/bash", "-lc",
                  "exec btop 2>/dev/null || exec htop 2>/dev/null || exec top"],
        ),
    )


@app.websocket("/ws/vnc")
async def ws_vnc(websocket: WebSocket):
    """Bridge zwischen Browser-WebSocket (noVNC) und dem VNC-TCP-Port einer VM."""
    if not await _require_ws_admin(websocket):
        return
    try:
        name = validators.require_vm_name(websocket.query_params.get("name", ""))
    except ValueError:
        await websocket.close(code=1008)
        return
    try:
        info = vms.get_vnc_port(name)
    except Exception:
        info = {"port": None}
    port = _parse_vnc_port(info.get("port"))
    if port is None:
        await websocket.accept()
        await websocket.send_text("Keine VNC-Konsole verfügbar")
        await websocket.close()
        return
    await websocket.accept()
    try:
        tcp_reader, tcp_writer = await asyncio.open_connection("127.0.0.1", port)
    except Exception:
        await websocket.close()
        return

    async def ws_to_tcp():
        try:
            while True:
                data = await websocket.receive_bytes()
                tcp_writer.write(data)
                await tcp_writer.drain()
        except Exception:
            pass

    async def tcp_to_ws():
        try:
            while True:
                data = await tcp_reader.read(4096)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass

    t1 = asyncio.create_task(ws_to_tcp())
    t2 = asyncio.create_task(tcp_to_ws())
    _, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        tcp_writer.close()
    except Exception:
        pass


# Static files (CSS/JS falls ausgelagert)
static_path = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")
