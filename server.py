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

from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket, \
    WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, \
    RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from modules import (system, terminal, files, storage, docker_mgr, services,
                     vms, backup, shares, network, security, monitoring,
                     system_mgr, apps, dashboard, metrics, accounts, audit)
from modules import security_tokens

app = FastAPI(title="runvard", docs_url=None, redoc_url=None)
http_basic = HTTPBasic()


@app.exception_handler(Exception)
async def _api_exception_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api"):
        message = str(exc) or exc.__class__.__name__
        return JSONResponse({"ok": False, "error": message}, status_code=500)
    raise exc


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

RUNVARD_USER = os.environ.get("RUNVARD_USER", "admin")
RUNVARD_PASS = os.environ.get("RUNVARD_PASS", "runvard")
RUNVARD_LANG = os.environ.get("RUNVARD_LANG", "en")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ Auth: Session-Cookies + Login an/aus ============
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
AUTH_CFG_FILE = os.path.join(DATA_DIR, "auth.json")
COOKIE_NAME = "runvard_session"
SESSION_TTL = 8 * 3600              # 8 Stunden
SESSION_TTL_REMEMBER = 30 * 86400   # 30 Tage

EXPERT_ONLY_PATHS = {
    "/api/monitoring/alerts",
    "/api/monitoring/alerts/add",
    "/api/monitoring/alerts/history",
    "/api/security/users/sudo",
    "/api/services/action",
    "/api/storage/btrfs",
    "/api/storage/btrfs/create",
    "/api/storage/btrfs/scrub",
    "/api/storage/iscsi",
    "/api/storage/iscsi/discover",
    "/api/storage/iscsi/login",
    "/api/storage/iscsi/logout",
    "/api/storage/luks",
    "/api/storage/luks/close",
    "/api/storage/luks/format",
    "/api/storage/luks/open",
    "/api/storage/lvm",
    "/api/storage/lvm/lv-create",
    "/api/storage/lvm/lv-extend",
    "/api/storage/lvm/lv-remove",
    "/api/storage/lvm/vg-create",
    "/api/storage/swap",
    "/api/storage/swap/action",
    "/api/storage/swap/create",
    "/api/storage/zfs",
    "/api/storage/zfs/create",
    "/api/storage/zfs/destroy",
    "/api/storage/zfs/scrub",
    "/api/sysmgr/apparmor",
    "/api/sysmgr/apparmor/set",
    "/api/sysmgr/cron",
    "/api/sysmgr/cron/add",
    "/api/sysmgr/kdump",
    "/api/sysmgr/kdump/action",
    "/api/sysmgr/packages/install",
    "/api/sysmgr/packages/remove",
    "/api/sysmgr/packages/search",
    "/api/sysmgr/sosreport",
    "/api/sysmgr/sosreport/run",
    "/api/sysmgr/tuned",
    "/api/sysmgr/tuned/set",
    "/api/sysmgr/unattended",
    "/api/sysmgr/unattended/set",
    "/api/vms/pool/action",
    "/api/vms/pool/create",
    "/api/vms/pool/vol-create",
    "/api/vms/pool/vol-delete",
    "/api/vms/pool/volumes",
    "/api/vms/pools",
}


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
    try:
        with open(AUTH_CFG_FILE) as f:
            return bool(json.load(f).get("login_enabled", True))
    except (FileNotFoundError, ValueError):
        return True


def set_login_enabled(val):
    with open(AUTH_CFG_FILE, "w") as f:
        json.dump({"login_enabled": bool(val)}, f)


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(username, ttl, role="admin", expert=False):
    expert_flag = "1" if expert else "0"
    payload = f"{username}|{role}|{expert_flag}|{int(time.time()) + ttl}".encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return _b64e(payload) + "." + _b64e(sig)


def _parse_token(token):
    """Gibt (username, role, expert) zurück oder None. Akzeptiert Alt-Tokens."""
    if not token or "." not in token:
        return None
    try:
        p_b64, s_b64 = token.split(".", 1)
        payload = _b64d(p_b64)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(s_b64), expected):
            return None
        parts = payload.decode().split("|")
        if len(parts) == 4:
            username, role, expert, exp = parts
        elif len(parts) == 3:
            username, role, exp = parts
            expert = "0"
        elif len(parts) == 2:
            username, exp = parts
            role = "admin"
            expert = "0"
        else:
            return None
        if int(exp) < int(time.time()):
            return None
        return (username, role, expert == "1")
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
    username, role = parsed[0], parsed[1]
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


def is_expert_request(request: Request):
    if not login_enabled():
        return True
    parsed = _parse_token(request.cookies.get(COOKIE_NAME))
    return bool(parsed and parsed[1] == "admin" and parsed[2])


def require_expert(request: Request):
    user = require_admin(request)
    if not is_expert_request(request):
        raise HTTPException(status_code=403, detail="Expert mode required")
    return user


@app.middleware("http")
async def _expert_only_mw(request: Request, call_next):
    if request.url.path in EXPERT_ONLY_PATHS and not is_expert_request(request):
        return JSONResponse({"detail": "Expert mode required"}, status_code=403)
    return await call_next(request)


def _form_value(form, *names, default=""):
    for name in names:
        value = form.get(name)
        if value not in (None, ""):
            return str(value)
    return default


def _danger_confirm_meta(path, form):
    simple = {
        "/api/accounts/password": ("accounts:password", "username"),
        "/api/accounts/role": ("accounts:role", "username"),
        "/api/accounts/delete": ("accounts:delete", "username"),
        "/api/files/delete": ("files:delete", "path"),
        "/api/files/trash/empty": ("files:trash-empty", "trash"),
        "/api/files/shares/delete": ("files:share-delete", "token"),
        "/api/storage/format": ("storage:format", "partition"),
        "/api/storage/unmount": ("storage:unmount", "mountpoint"),
        "/api/storage/raid/create": ("storage:raid-create", "name"),
        "/api/storage/lvm/lv-remove": ("storage:lvm-lv-remove", "lv_path"),
        "/api/storage/luks/format": ("storage:luks-format", "device"),
        "/api/storage/luks/close": ("storage:luks-close", "name"),
        "/api/storage/zfs/destroy": ("storage:zfs-destroy", "name"),
        "/api/storage/zfs/scrub": ("storage:zfs-scrub", "name"),
        "/api/storage/btrfs/scrub": ("storage:btrfs-scrub", "mountpoint"),
        "/api/storage/iscsi/logout": ("storage:iscsi-logout", "target"),
        "/api/docker/images/remove": ("docker:remove-image", "image_id"),
        "/api/docker/volumes/remove": ("docker:remove-volume", "name"),
        "/api/docker/compose/remove": ("docker:compose-remove", "name"),
        "/api/vms/disk/detach": ("vms:disk-detach", "name"),
        "/api/vms/pool/vol-delete": ("vms:volume-delete", "vol"),
        "/api/network/bond/delete": ("network:bond-delete", "name"),
        "/api/network/firewall/remove": ("network:firewall-remove", "num"),
        "/api/network/configure-ip": ("network:configure-ip", "iface"),
        "/api/security/users/smb-password": ("security:smb-password", "name"),
        "/api/security/users/password": ("security:password", "name"),
        "/api/security/users/ssh-keys/remove": ("security:ssh-key-remove", "name"),
        "/api/security/users/aging": ("security:aging", "name"),
        "/api/security/groups/delete": ("security:group-delete", "name"),
        "/api/security/groups/remove-member": ("security:group-remove-member", "group"),
        "/api/sysmgr/power/logind/set": ("sysmgr:logind-power", "logind"),
        "/api/sysmgr/hostname": ("sysmgr:hostname", "name"),
        "/api/sysmgr/packages/remove": ("sysmgr:package-remove", "name"),
        "/api/sysmgr/unattended/set": ("sysmgr:unattended", "unattended-upgrades"),
        "/api/sysmgr/tuned/set": ("sysmgr:tuned", "profile"),
        "/api/dashboard/remove": ("dashboard:remove", "tile_id"),
    }
    if path == "/api/auth/toggle":
        if str(form.get("enabled", "")) == "1":
            return None
        return ("auth:disable", "login")
    if path == "/api/docker/action":
        action = _form_value(form, "action")
        if action == "remove":
            return ("docker:remove-container", _form_value(form, "container_id"))
        return None
    if path == "/api/docker/networks/prune":
        return ("docker:network-prune", "Docker")
    if path == "/api/files/job":
        if _form_value(form, "action") == "delete":
            return ("files:delete", _form_value(form, "paths"))
        return None
    if path == "/api/docker/compose/action":
        action = _form_value(form, "action")
        if action == "remove":
            return ("docker:compose-remove", _form_value(form, "name"))
        return None
    if path == "/api/vms/action":
        if _form_value(form, "action") == "delete":
            return ("vms:delete", _form_value(form, "name"))
        return None
    if path == "/api/vms/pool/action":
        if _form_value(form, "action") == "delete":
            return ("vms:pool-delete", _form_value(form, "name"))
        return None
    if path == "/api/vms/snapshot/action":
        if _form_value(form, "action") == "delete":
            return ("vms:snapshot-delete", _form_value(form, "name"))
        return None
    if path == "/api/sysmgr/power":
        action = _form_value(form, "action")
        if action and action != "cancel":
            return (f"power:{action}", action)
        return None
    if path == "/api/apps/action":
        if _form_value(form, "action") == "down":
            return ("apps:uninstall", _form_value(form, "app_id"))
        return None
    if path in simple:
        spec = simple[path]
        return (spec[0], _form_value(form, *spec[1:]))
    return None


def require_danger_confirm(user, action, target, token):
    try:
        security_tokens.require_confirm_token(user, action, target, token)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


async def confirmed_admin(request: Request):
    user = require_admin(request)
    form = await request.form()
    meta = _danger_confirm_meta(request.url.path, form)
    if meta:
        action, target = meta
        require_danger_confirm(user, action, target, str(form.get("confirm_token", "")))
    return user


async def confirmed_auth(request: Request):
    user = auth(request)
    form = await request.form()
    meta = _danger_confirm_meta(request.url.path, form)
    if meta:
        user = require_admin(request)
        action, target = meta
        require_danger_confirm(user, action, target, str(form.get("confirm_token", "")))
    return user


# ============ Frontend ============

def _default_lang():
    return RUNVARD_LANG if RUNVARD_LANG in {"en", "de"} else "en"


def _frontend_html(name: str) -> str:
    with open(os.path.join(BASE_DIR, "static", name), encoding="utf-8") as f:
        html = f.read()
    return html.replace("__RUNVARD_DEFAULT_LANG__", _default_lang())

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if login_enabled() and not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse(_frontend_html("index.html"), headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if login_enabled() and _current_user(request):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(_frontend_html("login.html"), headers={"Cache-Control": "no-store"})


@app.post("/api/login")
def api_login(request: Request, username: str = Form(...),
              password: str = Form(...), remember: str = Form("0")):
    ip = request.client.host if request.client else "?"
    if not _rate_ok(ip):
        raise HTTPException(status_code=429, detail="Too many attempts")
    role = accounts.verify(username, password)
    if not role and secrets.compare_digest(username, RUNVARD_USER) \
            and secrets.compare_digest(password, RUNVARD_PASS):
        role = "admin"
    if not role:
        raise HTTPException(status_code=401, detail="Unauthorized")
    ttl = SESSION_TTL_REMEMBER if remember == "1" else SESSION_TTL
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
        return {"login_enabled": False, "user": None, "role": "admin", "expert": True}
    parsed = _parse_token(request.cookies.get(COOKIE_NAME))
    return {"login_enabled": True,
            "user": parsed[0] if parsed else None,
            "role": parsed[1] if parsed else None,
            "expert": bool(parsed[2]) if parsed else False}


@app.post("/api/confirm-token")
def api_confirm_token(action: str = Form(...), target: str = Form(...),
                      user: str = Depends(require_admin)):
    return security_tokens.issue_confirm_token(user, action, target)


@app.post("/api/expert-mode")
def api_expert_mode(request: Request, enabled: str = Form(...),
                    user: str = Depends(require_admin)):
    on = str(enabled).lower() in ("1", "true", "on", "yes")
    resp = JSONResponse({"ok": True, "expert": on})
    if login_enabled():
        parsed = _parse_token(request.cookies.get(COOKIE_NAME))
        role = parsed[1] if parsed else "admin"
        resp.set_cookie(COOKIE_NAME, make_token(user, SESSION_TTL, role, on),
                        max_age=SESSION_TTL, httponly=True, samesite="strict",
                        secure=(request.url.scheme == "https"),
                        path="/")
    return resp


@app.post("/api/auth/toggle")
def api_auth_toggle(enabled: str = Form(...), user: str = Depends(confirmed_admin)):
    set_login_enabled(enabled == "1")
    return {"ok": True, "login_enabled": login_enabled()}


# ============ runvard-Konten (RBAC) ============

@app.get("/api/accounts")
def accounts_list(user: str = Depends(require_admin)):
    return {"users": accounts.list_users()}


@app.post("/api/accounts/add")
def accounts_add(username: str = Form(...), password: str = Form(...),
                 role: str = Form("readonly"), user: str = Depends(confirmed_admin)):
    return accounts.add_user(username, password, role)


@app.post("/api/accounts/password")
def accounts_password(username: str = Form(...), password: str = Form(...),
                      user: str = Depends(confirmed_admin)):
    return accounts.set_password(username, password)


@app.post("/api/accounts/role")
def accounts_role(username: str = Form(...), role: str = Form(...),
                  user: str = Depends(confirmed_admin)):
    return accounts.set_role(username, role)


@app.post("/api/accounts/delete")
def accounts_delete(username: str = Form(...), user: str = Depends(confirmed_admin)):
    return accounts.delete_user(username)


@app.get("/btop", response_class=HTMLResponse)
def btop_page(user: str = Depends(auth)):
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>btop – runvard</title>
<link rel="icon" type="image/svg+xml" href="/static/btop-icon.svg">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css">
<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-unicode11@0.6.0/lib/xterm-addon-unicode11.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;background:#0a0810;overflow:hidden}
#t{width:100%;height:100vh}
#msg{position:fixed;inset:0;display:none;align-items:center;justify-content:center;flex-direction:column;gap:14px;background:rgba(10,8,16,.85);backdrop-filter:blur(4px);color:#e2e8f0;font-family:'JetBrains Mono',monospace;text-align:center;padding:24px;z-index:10}
#msg.show{display:flex}
#msg .ic{font-size:34px}
#msg .tx{font-size:14px;color:rgba(240,240,255,.7);max-width:420px;line-height:1.6}
#msg button{background:#7a4aff;color:#fff;border:none;padding:8px 18px;border-radius:8px;font-size:13px;cursor:pointer;font-family:inherit}
#msg button:hover{background:#8d64ff}
::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:rgba(139,92,246,.3);border-radius:3px}::-webkit-scrollbar-thumb:hover{background:rgba(139,92,246,.6)}
</style>
</head><body>
<div id="t"></div>
<div id="msg"><div class="ic">⚠️</div><div class="tx" id="msg-tx"></div><button onclick="location.reload()">↻ Neu laden</button></div>
<script>
const THEME={
  background:'#0a0810',foreground:'#e2e8f0',cursor:'#a78bfa',
  cursorAccent:'#0a0810',selectionBackground:'rgba(139,92,246,.35)',
  black:'#1e293b',brightBlack:'#64748b',
  red:'#f87171',brightRed:'#fca5a5',
  green:'#4ade80',brightGreen:'#86efac',
  yellow:'#fbbf24',brightYellow:'#fde68a',
  blue:'#60a5fa',brightBlue:'#93c5fd',
  magenta:'#c084fc',brightMagenta:'#d8b4fe',
  cyan:'#22d3ee',brightCyan:'#67e8f9',
  white:'#e2e8f0',brightWhite:'#f8fafc',
};
const term=new Terminal({fontFamily:"'JetBrains Mono','Cascadia Code',monospace",fontSize:13,lineHeight:1.2,
  cursorBlink:false,copyOnSelect:true,scrollback:1000,allowProposedApi:true,theme:THEME});
const fit=new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('t'));
try{term.loadAddon(new Unicode11Addon.Unicode11Addon());term.unicode.activeVersion='11';}catch(e){}
fit.fit();
term.focus();

function showMsg(t){document.getElementById('msg-tx').textContent=t;document.getElementById('msg').classList.add('show');}

const proto=location.protocol==='https:'?'wss':'ws';
const ws=new WebSocket(proto+'://'+location.host+'/ws/btop');
ws.onopen=()=>{ws.send(JSON.stringify({type:'resize',rows:term.rows,cols:term.cols}));term.focus();};
ws.onmessage=e=>term.write(e.data);
ws.onclose=ev=>{
  if(ev.code===1008)showMsg('Kein Zugriff. Bitte in runvard anmelden – Konten mit „Nur-Lesen" können den Monitor nicht öffnen.');
  else showMsg('Sitzung beendet. (Monitor geschlossen oder Verbindung getrennt.)');
};
term.onData(d=>{if(ws.readyState===1)ws.send(JSON.stringify({type:'input',data:d}));});
window.addEventListener('resize',()=>{fit.fit();if(ws.readyState===1)ws.send(JSON.stringify({type:'resize',rows:term.rows,cols:term.cols}));});
</script>
</body></html>"""


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
def files_list(path: str = "/root", user: str = Depends(auth)):
    try: return files.list_dir(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/files/read")
def files_read(path: str, user: str = Depends(auth)):
    try: return files.read_file(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/write")
def files_write(path: str = Form(...), content: str = Form(...), user: str = Depends(auth)):
    try: return files.write_file(path, content)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/rename")
def files_rename(path: str = Form(...), new_name: str = Form(...), user: str = Depends(auth)):
    try: return files.rename(path, new_name)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/copy")
def files_copy(src: str = Form(...), dst_dir: str = Form(...), user: str = Depends(auth)):
    try: return files.copy_item(src, dst_dir)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/move")
def files_move(src: str = Form(...), dst_dir: str = Form(...), user: str = Depends(auth)):
    try: return files.move(src, dst_dir)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/mkdir")
def files_mkdir(path: str = Form(...), name: str = Form(...), user: str = Depends(auth)):
    try: return files.mkdir(path, name)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/delete")
def files_delete(path: str = Form(...), user: str = Depends(confirmed_auth)):
    try: return files.move_to_trash(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/files/download")
def files_download(path: str, user: str = Depends(auth)):
    if files._bl(path): raise HTTPException(403, "Gesperrt")
    return FileResponse(path, filename=os.path.basename(path))

@app.get("/api/files/preview")
def files_preview(path: str, user: str = Depends(auth)):
    if files._bl(path): raise HTTPException(403, "Gesperrt")
    import mimetypes as mt; mime, _ = mt.guess_type(path)
    return FileResponse(path, media_type=mime or "application/octet-stream")

@app.post("/api/files/upload")
async def files_upload(path: str = Form(...), file: UploadFile = File(...), user: str = Depends(auth)):
    try:
        files._ok(path)
    except Exception as e:
        raise HTTPException(400, str(e))
    safe_name = os.path.basename(file.filename)
    if not safe_name:
        raise HTTPException(400, "Ungültiger Dateiname")
    dest = files._unique_dst(os.path.join(path, safe_name))
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
def files_zip(paths: str = Form(...), output: str = Form(...), user: str = Depends(auth)):
    try: return files.make_zip(paths.split("|"), output)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/unzip")
def files_unzip(path: str = Form(...), dst_dir: str = Form(...), user: str = Depends(auth)):
    try: return files.extract_zip(path, dst_dir)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/job")
def files_job(action: str = Form(...), paths: str = Form(...),
    dst_dir: str = Form(""), output: str = Form(""),
    user: str = Depends(confirmed_auth)):
    try:
        return files.start_job(action, paths.split("|"), dst_dir, output)
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
def files_trash_restore(item_id: str = Form(...), user: str = Depends(confirmed_admin)):
    try: return files.restore_trash(item_id)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/files/trash/empty")
def files_trash_empty(user: str = Depends(confirmed_admin)):
    return files.empty_trash()

@app.post("/api/files/share")
def files_share(path: str = Form(...), user: str = Depends(confirmed_admin)):
    try: return files.create_share_link(path)
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/files/shares")
def files_shares(user: str = Depends(auth)):
    return files.list_shares()

@app.get("/api/files/mounts")
def files_mounts(user: str = Depends(auth)):
    return {"mounts": files.list_mounts()}

@app.post("/api/files/shares/delete")
def files_share_delete(token: str = Form(...), user: str = Depends(confirmed_auth)):
    return files.delete_share(token)

@app.get("/dl/{token}")
def files_public_download(token: str):
    share = files.resolve_share(token)
    if not share: raise HTTPException(404, "Link ungültig")
    return FileResponse(share["path"], filename=share["name"])

@app.post("/api/files/samba-share")
def files_samba_share(path: str = Form(...), name: str = Form(...),
                      writable: bool = Form(True), user: str = Depends(confirmed_admin)):
    from modules import shares as sh
    return sh.add_samba_share(name, path, writable)

@app.post("/api/files/mount-smb")
def files_mount_smb(server: str = Form(...), share_name: str = Form(...),
                    mountpoint: str = Form(...), username: str = Form("guest"),
                    password: str = Form(""), user: str = Depends(confirmed_admin)):
    return files.mount_smb(server, share_name, mountpoint, username, password)

@app.post("/api/files/mount-nfs")
def files_mount_nfs(server: str = Form(...), export: str = Form(...),
                    mountpoint: str = Form(...), options: str = Form(""),
                    user: str = Depends(confirmed_admin)):
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
                   user: str = Depends(confirmed_admin)):
    return storage.create_partition_table(device, label)


@app.post("/api/storage/partition")
def storage_partition(device: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.create_partition(device)


@app.post("/api/storage/format")
def storage_format(partition: str = Form(...), fstype: str = Form("ext4"),
                   user: str = Depends(confirmed_admin)):
    return storage.format_partition(partition, fstype)


@app.post("/api/storage/mount")
def storage_mount(partition: str = Form(...), mountpoint: str = Form(...),
                  persist: bool = Form(False), user: str = Depends(confirmed_admin)):
    return storage.mount_device(partition, mountpoint, persist)


@app.post("/api/storage/unmount")
def storage_unmount(mountpoint: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.unmount_device(mountpoint)


@app.get("/api/storage/swap")
def storage_swap(user: str = Depends(auth)):
    return storage.list_swap()


@app.post("/api/storage/swap/create")
def storage_swap_create(path: str = Form(...), size_mb: int = Form(...),
                        persist: bool = Form(False), user: str = Depends(confirmed_admin)):
    return storage.create_swapfile(path, size_mb, persist)


@app.post("/api/storage/swap/action")
def storage_swap_action(target: str = Form(...), action: str = Form(...),
                        user: str = Depends(confirmed_admin)):
    return storage.swap_action(target, action)


@app.get("/api/storage/raid")
def storage_raid(user: str = Depends(auth)):
    return storage.list_raid()


@app.post("/api/storage/raid/create")
def storage_raid_create(name: str = Form(...), level: int = Form(...),
                        devices: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.create_raid(name, level, devices.split(","))


@app.get("/api/storage/lvm")
def storage_lvm(user: str = Depends(auth)):
    return storage.lvm_overview()


@app.post("/api/storage/lvm/vg-create")
def storage_lvm_vg(name: str = Form(...), devices: str = Form(...),
                   user: str = Depends(confirmed_admin)):
    return storage.vg_create(name, devices.split(","))


@app.post("/api/storage/lvm/lv-create")
def storage_lvm_lv(vg: str = Form(...), name: str = Form(...),
                   size: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.lv_create(vg, name, size)


@app.post("/api/storage/lvm/lv-extend")
def storage_lvm_extend(lv_path: str = Form(...), size: str = Form(...),
                       user: str = Depends(confirmed_admin)):
    return storage.lv_extend(lv_path, size)


@app.post("/api/storage/lvm/lv-remove")
def storage_lvm_remove(lv_path: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.lv_remove(lv_path)


# ---- LUKS-Verschlüsselung ----

@app.get("/api/storage/luks")
def storage_luks(user: str = Depends(auth)):
    return storage.luks_list()


@app.post("/api/storage/luks/format")
def storage_luks_format(device: str = Form(...), passphrase: str = Form(...),
                        user: str = Depends(confirmed_admin)):
    try:
        return storage.luks_format(device, passphrase)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/luks/open")
def storage_luks_open(device: str = Form(...), name: str = Form(...),
                      passphrase: str = Form(...), user: str = Depends(confirmed_admin)):
    try:
        return storage.luks_open(device, name, passphrase)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/luks/close")
def storage_luks_close(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.luks_close(name)


# ---- Dateisystem-Resize (non-LVM) ----

@app.post("/api/storage/fs-grow")
def storage_fs_grow(device: str = Form(""), mountpoint: str = Form(""),
                    size: str = Form("max"), user: str = Depends(confirmed_admin)):
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
                       devices: str = Form(...), user: str = Depends(confirmed_admin)):
    try:
        devs = [d.strip() for d in devices.split(",") if d.strip()]
        return storage.zpool_create(name, raid, devs)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/zfs/destroy")
def storage_zfs_destroy(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.zpool_destroy(name)


@app.post("/api/storage/zfs/scrub")
def storage_zfs_scrub(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.zpool_scrub(name)


# ---- Btrfs-Pools ----

@app.get("/api/storage/btrfs")
def storage_btrfs(user: str = Depends(auth)):
    return storage.btrfs_filesystems()


@app.post("/api/storage/btrfs/create")
def storage_btrfs_create(label: str = Form(""), profile: str = Form("single"),
                         devices: str = Form(...), user: str = Depends(confirmed_admin)):
    try:
        devs = [d.strip() for d in devices.split(",") if d.strip()]
        return storage.btrfs_create(label, profile, devs)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/btrfs/scrub")
def storage_btrfs_scrub(mountpoint: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.btrfs_scrub(mountpoint)


# ---- iSCSI-Initiator ----

@app.get("/api/storage/iscsi")
def storage_iscsi(user: str = Depends(auth)):
    return storage.iscsi_sessions()


@app.post("/api/storage/iscsi/discover")
def storage_iscsi_discover(portal: str = Form(...), user: str = Depends(confirmed_admin)):
    return storage.iscsi_discover(portal)


@app.post("/api/storage/iscsi/login")
def storage_iscsi_login(portal: str = Form(...), target: str = Form(...),
                        user: str = Depends(confirmed_admin)):
    return storage.iscsi_login(portal, target)


@app.post("/api/storage/iscsi/logout")
def storage_iscsi_logout(portal: str = Form(...), target: str = Form(...),
                         user: str = Depends(confirmed_admin)):
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
                  user: str = Depends(confirmed_admin)):
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
                  user: str = Depends(confirmed_admin)):
    return docker_mgr.create_container(image, name, ports, volumes, env,
                                       restart, cpus, memory)


@app.post("/api/docker/update")
def docker_update(container_id: str = Form(...), cpus: str = Form(""),
                  memory: str = Form(""), user: str = Depends(auth)):
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
def docker_pull(name: str = Form(...), user: str = Depends(auth)):
    return docker_mgr.pull_image(name)


@app.post("/api/docker/images/remove")
def docker_image_remove(image_id: str = Form(...), user: str = Depends(confirmed_admin)):
    return docker_mgr.remove_image(image_id)


@app.get("/api/docker/volumes")
def docker_volumes(user: str = Depends(auth)):
    return docker_mgr.list_volumes()


@app.post("/api/docker/volumes/remove")
def docker_volume_remove(name: str = Form(...), user: str = Depends(confirmed_admin)):
    try:
        return docker_mgr.remove_volume(name)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/docker/networks")
def docker_networks(user: str = Depends(auth)):
    try:
        return docker_mgr.list_networks()
    except Exception as e:
        if docker_mgr.is_network_not_found_error(e):
            return []
        raise HTTPException(400, str(e))


@app.post("/api/docker/networks/prune")
def docker_networks_prune(user: str = Depends(confirmed_admin)):
    try:
        return docker_mgr.prune_networks()
    except Exception as e:
        if docker_mgr.is_network_not_found_error(e):
            return {"ok": True, "deleted": [], "deleted_count": 0, "skipped": []}
        raise HTTPException(400, str(e))


@app.get("/api/docker/compose")
def docker_compose_list(user: str = Depends(auth)):
    return docker_mgr.list_compose_projects()


@app.get("/api/docker/compose/get")
def docker_compose_get(name: str, user: str = Depends(auth)):
    return docker_mgr.get_compose(name)


@app.post("/api/docker/compose/check-ports")
def docker_compose_check_ports(name: str = Form(...), content: str = Form(...),
                               user: str = Depends(confirmed_admin)):
    return docker_mgr.check_compose_ports(name, content)


@app.post("/api/docker/compose/save")
def docker_compose_save(name: str = Form(...), content: str = Form(...),
                        env_enabled: bool = Form(False),
                        env_content: str = Form(""),
                        user: str = Depends(require_admin)):
    port_check = docker_mgr.check_compose_ports(name, content)
    if not port_check["ok"]:
        return {
            **port_check,
            "stderr": "One or more Compose ports are already in use.",
        }
    return docker_mgr.save_compose(name, content, env_enabled, env_content)


@app.post("/api/docker/compose/action")
def docker_compose_action(name: str = Form(...), action: str = Form(...),
                          user: str = Depends(confirmed_admin)):
    return docker_mgr.compose_action(name, action)


@app.post("/api/docker/compose/remove")
def docker_compose_remove(name: str = Form(...), user: str = Depends(confirmed_admin)):
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
                    user: str = Depends(confirmed_admin)):
    return services.service_action(name, action)


@app.get("/api/services/logs")
def services_logs(name: str, user: str = Depends(auth)):
    return services.service_logs(name)


# ============ VMs ============

@app.get("/api/vms/available")
def vms_available(user: str = Depends(auth)):
    return {"available": vms.available()}


@app.get("/api/vms/list")
def vms_list(user: str = Depends(auth)):
    try:
        return vms.list_vms()
    except Exception:
        return []


@app.get("/api/vms/diagnostics")
def vms_diagnostics(user: str = Depends(auth)):
    return vms.diagnostics()


@app.post("/api/vms/action")
def vms_action(name: str = Form(...), action: str = Form(...),
               user: str = Depends(confirmed_admin)):
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
                    user: str = Depends(confirmed_admin)):
    return vms.attach_disk(name, source, target, bus)


@app.post("/api/vms/disk/detach")
def vms_disk_detach(name: str = Form(...), target: str = Form(...),
                    user: str = Depends(confirmed_admin)):
    return vms.detach_disk(name, target)


@app.post("/api/vms/nic/attach")
def vms_nic_attach(name: str = Form(...), network: str = Form(...),
                   model: str = Form("virtio"), user: str = Depends(confirmed_admin)):
    return vms.attach_nic(name, network, model)


@app.post("/api/vms/nic/detach")
def vms_nic_detach(name: str = Form(...), type: str = Form(...),
                   mac: str = Form(...), user: str = Depends(confirmed_admin)):
    return vms.detach_nic(name, type, mac)


@app.post("/api/vms/pool/create")
def vms_pool_create(name: str = Form(...), ptype: str = Form("dir"),
                    target: str = Form(...), user: str = Depends(confirmed_admin)):
    return vms.pool_create(name, ptype, target)


@app.post("/api/vms/pool/action")
def vms_pool_action(name: str = Form(...), action: str = Form(...),
                    user: str = Depends(confirmed_admin)):
    return vms.pool_action(name, action)


@app.get("/api/vms/pool/volumes")
def vms_pool_volumes(pool: str, user: str = Depends(auth)):
    return vms.pool_volumes(pool)


@app.post("/api/vms/pool/vol-create")
def vms_vol_create(pool: str = Form(...), name: str = Form(...),
                   size_gb: int = Form(...), format: str = Form("qcow2"),
                   user: str = Depends(confirmed_admin)):
    return vms.vol_create(pool, name, size_gb, format)


@app.post("/api/vms/pool/vol-delete")
def vms_vol_delete(pool: str = Form(...), vol: str = Form(...),
                   user: str = Depends(confirmed_admin)):
    return vms.vol_delete(pool, vol)


@app.post("/api/vms/clone")
def vms_clone(name: str = Form(...), newname: str = Form(...),
              user: str = Depends(confirmed_admin)):
    return vms.clone_vm(name, newname)


@app.post("/api/vms/cdrom")
def vms_cdrom(name: str = Form(...), iso: str = Form(""),
              user: str = Depends(confirmed_admin)):
    return vms.change_cdrom(name, iso)


@app.post("/api/vms/create")
def vms_create(name: str = Form(...), memory_mb: int = Form(...),
               vcpus: int = Form(...), disk_gb: int = Form(...),
               iso: str = Form(""), network: str = Form("default"),
               user: str = Depends(confirmed_admin)):
    return vms.create_vm(name, memory_mb, vcpus, disk_gb, iso, network)


@app.get("/api/vms/snapshots")
def vms_snapshots(name: str, user: str = Depends(auth)):
    return vms.list_snapshots(name)


@app.post("/api/vms/snapshot")
def vms_snapshot(name: str = Form(...), snap_name: str = Form(...),
                 user: str = Depends(confirmed_admin)):
    return vms.create_snapshot(name, snap_name)


@app.post("/api/vms/snapshot/action")
def vms_snapshot_action(name: str = Form(...), snap_name: str = Form(...),
                        action: str = Form(...), user: str = Depends(confirmed_admin)):
    return vms.snapshot_action(name, snap_name, action)


# ============ Backup ============

@app.get("/api/backup/jobs")
def backup_jobs(user: str = Depends(auth)):
    return backup.list_jobs()


@app.post("/api/backup/add")
def backup_add(name: str = Form(...), source: str = Form(...),
               dest: str = Form(...), schedule: str = Form("manual"),
               user: str = Depends(auth)):
    return backup.add_job(name, source, dest, schedule)


@app.post("/api/backup/run")
def backup_run(job_id: int = Form(...), user: str = Depends(auth)):
    return backup.run_job(job_id)


@app.get("/api/backup/history")
def backup_history(user: str = Depends(auth)):
    return backup.get_history()


# ============ Freigaben ============

@app.get("/api/shares/samba")
def shares_samba(user: str = Depends(auth)):
    return shares.list_samba_shares()


@app.post("/api/shares/samba/add")
def shares_samba_add(name: str = Form(...), path: str = Form(...),
                     writable: bool = Form(True), user: str = Depends(confirmed_admin)):
    return shares.add_samba_share(name, path, writable)


@app.get("/api/shares/nfs")
def shares_nfs(user: str = Depends(auth)):
    return shares.list_nfs_exports()


@app.post("/api/shares/nfs/add")
def shares_nfs_add(path: str = Form(...), clients: str = Form("*"),
                   user: str = Depends(confirmed_admin)):
    return shares.add_nfs_export(path, clients)


@app.get("/api/shares/ftp")
def shares_ftp(user: str = Depends(auth)):
    return shares.ftp_status()


# ============ Netzwerk ============

@app.get("/api/network/interfaces")
def network_interfaces(user: str = Depends(auth)):
    ifaces = network.list_interfaces()
    for i in ifaces:
        if i["is_bond"]:
            i["members"] = network.bond_members(i["name"])
    return ifaces


@app.post("/api/network/bond/create")
def network_bond_create(name: str = Form(...), members: str = Form(...),
                        mode: str = Form("802.3ad"), user: str = Depends(confirmed_admin)):
    return network.create_bond(name, members.split(","), mode)


@app.post("/api/network/bond/delete")
def network_bond_delete(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return network.delete_bond(name)


@app.get("/api/network/firewall")
def network_firewall(user: str = Depends(auth)):
    return network.firewall_status()


@app.get("/api/network/firewall/rules")
def network_firewall_rules(user: str = Depends(auth)):
    return network.firewall_rules()


@app.post("/api/network/firewall/add")
def network_firewall_add(port: int = Form(...), proto: str = Form("tcp"),
                         action: str = Form("allow"), user: str = Depends(confirmed_admin)):
    return network.firewall_add_rule(port, proto, action)


@app.post("/api/network/firewall/remove")
def network_firewall_remove(num: int = Form(...), user: str = Depends(confirmed_admin)):
    return network.firewall_remove_rule(num)


@app.post("/api/network/configure-ip")
def network_configure_ip(iface: str = Form(...), mode: str = Form("static"),
                         ip: str = Form(""), netmask: str = Form("24"),
                         gateway: str = Form(""), dns: str = Form(""),
                         persist: bool = Form(False), user: str = Depends(confirmed_admin)):
    return network.configure_ip(iface, mode, ip, netmask, gateway, dns, persist)


@app.post("/api/network/bridge/create")
def network_bridge_create(name: str = Form(...), members: str = Form(""),
                          user: str = Depends(confirmed_admin)):
    return network.create_bridge(name, [m for m in members.split(",") if m])


@app.post("/api/network/vlan/create")
def network_vlan_create(parent: str = Form(...), vlan_id: int = Form(...),
                        name: str = Form(""), user: str = Depends(confirmed_admin)):
    return network.create_vlan(parent, vlan_id, name)


@app.post("/api/network/link/delete")
def network_link_delete(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return network.delete_link(name)


# ============ Sicherheit ============

@app.get("/api/security/users")
def security_users(user: str = Depends(auth)):
    return security.list_users()


@app.post("/api/security/users/add")
def security_users_add(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return security.add_user(name)


@app.post("/api/security/users/smb-password")
def security_smb_password(name: str = Form(...), password: str = Form(...),
                          user: str = Depends(confirmed_admin)):
    return security.set_smb_password(name, password)


@app.get("/api/security/smb-users")
def security_smb_users(user: str = Depends(auth)):
    return {"users": security.list_smb_users()}


@app.post("/api/security/users/password")
def security_password(name: str = Form(...), password: str = Form(...),
                      user: str = Depends(confirmed_admin)):
    return security.set_password(name, password)


@app.get("/api/security/users/ssh-keys")
def security_ssh_keys(name: str, user: str = Depends(auth)):
    return {"keys": security.list_ssh_keys(name)}


@app.post("/api/security/users/ssh-keys/add")
def security_ssh_keys_add(name: str = Form(...), key: str = Form(...),
                          user: str = Depends(confirmed_admin)):
    return security.add_ssh_key(name, key)


@app.post("/api/security/users/ssh-keys/remove")
def security_ssh_keys_remove(name: str = Form(...), key: str = Form(...),
                             user: str = Depends(confirmed_admin)):
    return security.remove_ssh_key(name, key)


@app.get("/api/security/users/secinfo")
def security_user_secinfo(name: str, user: str = Depends(auth)):
    return security.user_security(name)


@app.post("/api/security/users/sudo")
def security_user_sudo(name: str = Form(...), enable: bool = Form(...),
                       nopasswd: bool = Form(False), user: str = Depends(confirmed_admin)):
    return security.set_sudo(name, enable, nopasswd)


@app.post("/api/security/users/aging")
def security_user_aging(name: str = Form(...), max_days: str = Form(""),
                        min_days: str = Form(""), warn_days: str = Form(""),
                        expire: str = Form(""), user: str = Depends(confirmed_admin)):
    return security.set_password_aging(name, max_days, min_days, warn_days, expire)


@app.post("/api/security/users/expire")
def security_user_expire(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return security.expire_password_now(name)


@app.post("/api/security/groups/add")
def security_group_add(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return security.add_group(name)


@app.post("/api/security/groups/delete")
def security_group_delete(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return security.delete_group(name)


@app.post("/api/security/groups/add-member")
def security_group_add_member(group: str = Form(...), member: str = Form(...),
                              user: str = Depends(confirmed_admin)):
    return security.add_to_group(member, group)


@app.post("/api/security/groups/remove-member")
def security_group_remove_member(group: str = Form(...), member: str = Form(...),
                                 user: str = Depends(confirmed_admin)):
    return security.remove_from_group(member, group)


@app.get("/api/security/groups")
def security_groups(user: str = Depends(auth)):
    return security.list_groups()


@app.get("/api/security/certs")
def security_certs(user: str = Depends(auth)):
    return security.list_certificates()


@app.post("/api/security/certs/generate")
def security_cert_gen(common_name: str = Form(...), user: str = Depends(confirmed_admin)):
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
def monitoring_alert_add(metric: str = Form(...), threshold: float = Form(...),
                         channel: str = Form(...), user: str = Depends(auth)):
    return monitoring.add_alert_rule(metric, threshold, channel)


@app.get("/api/monitoring/alerts/history")
def monitoring_alert_history(user: str = Depends(auth)):
    return monitoring.get_alert_history()


@app.get("/api/monitoring/audit")
def monitoring_audit(lines: int = 200, user: str = Depends(auth)):
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
def sysmgr_updates(user: str = Depends(auth)):
    return system_mgr.check_updates()


@app.get("/api/sysmgr/upgradable")
def sysmgr_upgradable(user: str = Depends(auth)):
    return system_mgr.list_upgradable()


@app.get("/api/sysmgr/runvard-release")
def sysmgr_runvard_release(user: str = Depends(auth)):
    return system_mgr.runvard_release_status()


@app.post("/api/sysmgr/runvard-update/apply")
def sysmgr_runvard_update_apply(user: str = Depends(require_admin)):
    try:
        return system_mgr.start_runvard_update()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/sysmgr/runvard-update/log")
def sysmgr_runvard_update_log(user: str = Depends(auth)):
    return system_mgr.runvard_update_log()


@app.post("/api/sysmgr/updates/apply")
def sysmgr_updates_apply(user: str = Depends(require_admin)):
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
                    user: str = Depends(confirmed_admin)):
    return system_mgr.add_cron_job(schedule, command)


@app.post("/api/sysmgr/power")
def sysmgr_power(action: str = Form(...), delay: int = Form(0),
                 confirm_token: str = Form(""),
                 user: str = Depends(confirmed_admin)):
    return system_mgr.power_action(action, delay)


@app.get("/api/sysmgr/power/status")
def sysmgr_power_status(user: str = Depends(auth)):
    return system_mgr.power_status()


@app.get("/api/sysmgr/power/profiles")
def sysmgr_power_profiles(user: str = Depends(auth)):
    return system_mgr.power_profiles_status()


@app.post("/api/sysmgr/power/profiles/set")
def sysmgr_power_profiles_set(profile: str = Form(...),
                              user: str = Depends(confirmed_admin)):
    return system_mgr.power_profiles_set(profile)


@app.get("/api/sysmgr/power/logind")
def sysmgr_power_logind(user: str = Depends(auth)):
    return system_mgr.logind_power_status()


@app.post("/api/sysmgr/power/logind/set")
def sysmgr_power_logind_set(
    IdleAction: str = Form("ignore"),
    IdleActionSec: str = Form("30min"),
    HandlePowerKey: str = Form("poweroff"),
    HandleLidSwitch: str = Form("suspend"),
    HandleLidSwitchDocked: str = Form("ignore"),
    HandleLidSwitchExternalPower: str = Form("suspend"),
    user: str = Depends(confirmed_admin),
):
    return system_mgr.logind_power_set({
        "IdleAction": IdleAction,
        "IdleActionSec": IdleActionSec,
        "HandlePowerKey": HandlePowerKey,
        "HandleLidSwitch": HandleLidSwitch,
        "HandleLidSwitchDocked": HandleLidSwitchDocked,
        "HandleLidSwitchExternalPower": HandleLidSwitchExternalPower,
    })


@app.get("/api/sysmgr/power/inhibitors")
def sysmgr_power_inhibitors(user: str = Depends(auth)):
    return system_mgr.power_inhibitors()


@app.get("/api/sysmgr/power/tools")
def sysmgr_power_tools(user: str = Depends(auth)):
    return system_mgr.power_tools_status()


@app.post("/api/sysmgr/hostname")
def sysmgr_hostname(name: str = Form(...), user: str = Depends(confirmed_admin)):
    return system_mgr.set_hostname(name)


@app.get("/api/sysmgr/gpu")
def sysmgr_gpu(user: str = Depends(auth)):
    return system_mgr.gpu_info()


@app.get("/api/sysmgr/apparmor")
def sysmgr_apparmor(user: str = Depends(auth)):
    return system_mgr.apparmor_status()


@app.post("/api/sysmgr/apparmor/set")
def sysmgr_apparmor_set(profile: str = Form(...), mode: str = Form(...),
                        user: str = Depends(confirmed_admin)):
    return system_mgr.apparmor_set(profile, mode)


@app.get("/api/sysmgr/packages/search")
def sysmgr_pkg_search(q: str, user: str = Depends(auth)):
    return system_mgr.pkg_search(q)


@app.post("/api/sysmgr/packages/install")
def sysmgr_pkg_install(name: str = Form(...), user: str = Depends(confirmed_admin)):
    from modules import jobs
    return jobs.start_job("apt-install", system_mgr.pkg_install, name)


@app.post("/api/sysmgr/packages/remove")
def sysmgr_pkg_remove(name: str = Form(...), user: str = Depends(confirmed_admin)):
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
def sysmgr_unattended_set(enable: bool = Form(...),
                          auto_reboot: bool = Form(False),
                          reboot_time: str = Form("02:00"),
                          user: str = Depends(confirmed_admin)):
    return system_mgr.unattended_set(enable, auto_reboot, reboot_time)


@app.get("/api/sysmgr/tuned")
def sysmgr_tuned(user: str = Depends(auth)):
    return system_mgr.tuned_status()


@app.post("/api/sysmgr/tuned/set")
def sysmgr_tuned_set(profile: str = Form(...), user: str = Depends(confirmed_admin)):
    return system_mgr.tuned_set(profile)


@app.get("/api/sysmgr/kdump")
def sysmgr_kdump(user: str = Depends(auth)):
    return system_mgr.kdump_status()


@app.post("/api/sysmgr/kdump/action")
def sysmgr_kdump_action(action: str = Form(...), user: str = Depends(confirmed_admin)):
    return system_mgr.kdump_action(action)


@app.get("/api/sysmgr/sosreport")
def sysmgr_sosreport(user: str = Depends(auth)):
    return system_mgr.sosreport_list()


@app.post("/api/sysmgr/sosreport/run")
def sysmgr_sosreport_run(user: str = Depends(auth)):
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
                 user: str = Depends(confirmed_admin)):
    try:
        return apps.install(app_id, content)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/apps/save-compose")
def apps_save_compose(app_id: str = Form(...), content: str = Form(...),
                      user: str = Depends(confirmed_admin)):
    try:
        if app_id.startswith("compose:"):
            name = app_id.split(":", 1)[1]
            port_check = docker_mgr.check_compose_ports(name, content)
            if not port_check["ok"]:
                return {
                    **port_check,
                    "stderr": "One or more Compose ports are already in use.",
                }
        return apps.save_compose(app_id, content)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps/install-status")
def apps_install_status(job_id: str, user: str = Depends(auth)):
    return apps.install_status(job_id)


@app.post("/api/apps/action")
def apps_action(app_id: str = Form(...), action: str = Form(...),
                user: str = Depends(confirmed_admin)):
    try:
        return apps.action(app_id, action)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps/check-updates")
def apps_check_updates(force: bool = False, user: str = Depends(auth)):
    return apps.check_updates(force=force)


# ============ Dashboard ============

@app.get("/api/dashboard")
def dashboard_get(user: str = Depends(auth)):
    return dashboard.get_dashboard()


@app.post("/api/dashboard/add")
def dashboard_add(tile_type: str = Form(...), tile_id: str = Form(...),
                  name: str = Form(""), url: str = Form(""),
                  icon: str = Form(""), port: int = Form(0),
                  user: str = Depends(auth)):
    return dashboard.add_tile(tile_type, tile_id, name, url, icon, port)


@app.post("/api/dashboard/remove")
def dashboard_remove(tile_id: str = Form(...), user: str = Depends(confirmed_auth)):
    return dashboard.remove_tile(tile_id)


@app.post("/api/dashboard/order")
def dashboard_order(order: str = Form(...), user: str = Depends(auth)):
    return dashboard.save_order(json.loads(order))


@app.post("/api/dashboard/toggle-url")
def dashboard_toggle_url(tile_id: str = Form(...), show: bool = Form(...),
                         user: str = Depends(auth)):
    return dashboard.toggle_url(tile_id, show)


@app.post("/api/dashboard/update")
def dashboard_update(tile_id: str = Form(...), name: str = Form(""),
                     url: str = Form(""), icon: str = Form(""),
                     host: str = Form(None),
                     show_url: bool = Form(None),
                     accent: str = Form(None), note: str = Form(None),
                     user: str = Depends(auth)):
    return dashboard.update_tile(tile_id, name or None, url or None,
                                 icon or None, host, show_url,
                                 accent, note)


# ============ WebSocket Terminal ============

@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket):
    # Auth über Session-Cookie (bei deaktiviertem Login freier Zugriff); Readonly verboten
    if login_enabled():
        parsed = _parse_token(websocket.cookies.get(COOKIE_NAME))
        if not parsed or parsed[1] == "readonly":
            await websocket.close(code=1008)
            return
    await websocket.accept()
    session = terminal.TerminalSession()
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
            data = json.loads(msg)
            if data["type"] == "input":
                session.write(data["data"])
            elif data["type"] == "resize":
                session.resize(data["rows"], data["cols"])
            elif data["type"] == "ping":
                await websocket.send_text("\0runvard:pong")
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        session.kill()


@app.websocket("/ws/docker-exec")
async def ws_docker_exec(websocket: WebSocket):
    # Auth über Session-Cookie (bei deaktiviertem Login freier Zugriff); Readonly verboten
    if login_enabled():
        parsed = _parse_token(websocket.cookies.get(COOKIE_NAME))
        if not parsed or parsed[1] == "readonly":
            await websocket.close(code=1008)
            return
    import re as _re
    cid = websocket.query_params.get("id", "")
    if not _re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", cid):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    session = terminal.TerminalSession(
        cwd="/", argv=["docker", "exec", "-it", cid, "/bin/sh"]
    )
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
            data = json.loads(msg)
            if data["type"] == "input":
                session.write(data["data"])
            elif data["type"] == "resize":
                session.resize(data["rows"], data["cols"])
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        session.kill()


@app.websocket("/ws/btop")
async def ws_btop(websocket: WebSocket):
    """Startet btop direkt im PTY (Fallback btop -> htop -> top), ohne Eingabe-Rennen."""
    # Auth über Session-Cookie (bei deaktiviertem Login freier Zugriff); Readonly verboten
    if login_enabled():
        parsed = _parse_token(websocket.cookies.get(COOKIE_NAME))
        if not parsed or parsed[1] == "readonly":
            await websocket.close(code=1008)
            return
    await websocket.accept()
    session = terminal.TerminalSession(
        cwd="/",
        argv=["/bin/bash", "-lc",
              "exec btop 2>/dev/null || exec htop 2>/dev/null || exec top"],
    )
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
            data = json.loads(msg)
            if data["type"] == "input":
                session.write(data["data"])
            elif data["type"] == "resize":
                session.resize(data["rows"], data["cols"])
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        session.kill()


@app.websocket("/ws/vnc")
async def ws_vnc(websocket: WebSocket):
    """Bridge zwischen Browser-WebSocket (noVNC) und dem VNC-TCP-Port einer VM."""
    if login_enabled():
        parsed = _parse_token(websocket.cookies.get(COOKIE_NAME))
        if not parsed or parsed[1] == "readonly":
            await websocket.close(code=1008)
            return
    import re as _re
    name = websocket.query_params.get("name", "")
    if not _re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\- ]{0,127}$", name):
        await websocket.close(code=1008)
        return
    try:
        info = vms.get_vnc_port(name)
    except Exception:
        info = {"port": None}
    port = info.get("port")
    if not port or str(port) in ("", "-1", "None"):
        await websocket.accept()
        await websocket.send_text("Keine VNC-Konsole verfügbar")
        await websocket.close()
        return
    await websocket.accept()
    try:
        tcp_reader, tcp_writer = await asyncio.open_connection("127.0.0.1", int(port))
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
