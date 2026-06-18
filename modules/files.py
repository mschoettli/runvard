"""Dateimanager – komplett neu, sauber, ohne Altlasten."""
import os, shutil, stat, mimetypes, json, time, uuid, zipfile, subprocess, threading
from pathlib import Path

from modules.runtime import data_path
from modules import validators

BLOCKED  = validators.BLOCKED_PATHS
READONLY = validators.READONLY_PATHS
TRASH    = data_path("trash")
TRASHMETA= data_path("trash", ".meta.json")
SHAREDB  = data_path("shares.json")
JOBDB    = data_path("file_jobs.json")
MAX_EDIT = 2 * 1024 * 1024
_job_lock = threading.Lock()
_active_jobs = set()

def _r(p):  return os.path.realpath(p)
def _bl(p): return validators.is_blocked_path(p)
def _ro(p): return validators.is_readonly_path(p)
def _ok(p):
    return validators.guard_write_path(p)

def _unique_dst(dst):
    if not os.path.exists(dst):
        return dst
    parent = os.path.dirname(dst)
    name = os.path.basename(dst)
    stem, ext = os.path.splitext(name)
    for i in range(1, 10000):
        suffix = " copy" if i == 1 else f" copy {i}"
        cand = os.path.join(parent, f"{stem}{suffix}{ext}")
        if not os.path.exists(cand):
            return cand
    raise FileExistsError(dst)

def _load_json_or_quarantine(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        try:
            os.replace(path, f"{path}.corrupt-{int(time.time())}")
        except OSError:
            pass
        return default
    except OSError:
        return default

def list_dir(path):
    path = validators.guard_read_path(path or "/")
    if not os.path.isdir(path): raise NotADirectoryError(path)
    entries = []
    with os.scandir(path) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
                full = os.path.join(path, e.name)
                mime, _ = mimetypes.guess_type(full)
                entries.append({
                    "name": e.name, "path": full,
                    "is_dir": e.is_dir(follow_symlinks=False),
                    "size": st.st_size, "modified": st.st_mtime,
                    "mode": stat.filemode(st.st_mode), "mime": mime or "",
                    "readonly": _ro(full), "blocked": _bl(full),
                })
            except: continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"path": path, "parent": os.path.dirname(path), "entries": entries}

def read_file(path):
    path = validators.guard_read_path(path)
    if os.path.getsize(path) > MAX_EDIT: raise ValueError("Datei zu groß")
    with open(path, "r", errors="replace") as f:
        return {"path": path, "content": f.read(), "readonly": _ro(path)}

def write_file(path, content):
    path = _ok(path)
    with open(path, "w") as f: f.write(content)
    return {"ok": True}

def rename(path, new_name):
    path = _ok(path)
    dst = validators.safe_join(os.path.dirname(path), new_name)
    os.rename(path, dst)
    return {"ok": True, "path": dst}

def copy_item(src, dst_dir):
    src = validators.guard_read_path(src)
    dst_dir = _ok(dst_dir)
    dst = _unique_dst(os.path.join(dst_dir, os.path.basename(src)))
    if os.path.isdir(src):
        _copy_tree_progress(src, dst, lambda _current="": None)
    else:
        shutil.copy2(src, dst)
    return {"ok": True, "dst": dst}

def move(src, dst_dir):
    src = _ok(src); dst_dir = _ok(dst_dir)
    dst = _unique_dst(os.path.join(dst_dir, os.path.basename(src)))
    shutil.move(src, dst)
    return {"ok": True, "dst": dst}

def mkdir(path, name):
    new = validators.safe_join(path, name)
    os.makedirs(new, exist_ok=False)
    return {"ok": True, "path": new}

def file_info(path):
    path = validators.guard_read_path(path); st = os.stat(path)
    mime, _ = mimetypes.guess_type(path)
    return {"name": os.path.basename(path), "path": path, "size": st.st_size,
            "modified": st.st_mtime, "mode": stat.filemode(st.st_mode),
            "octal": oct(stat.S_IMODE(st.st_mode))[2:], "uid": st.st_uid,
            "gid": st.st_gid, "mime": mime, "is_dir": os.path.isdir(path)}

def search(base, query, max_results=200):
    base = validators.guard_read_path(base); results = []; q = query.lower()
    for root, dirs, files in os.walk(base):
        if _bl(root): dirs.clear(); continue
        dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))]
        for name in dirs + files:
            if q in name.lower():
                full = os.path.join(root, name)
                results.append({"name": name, "path": full, "is_dir": os.path.isdir(full)})
                if len(results) >= max_results: return results
    return results

# ── ZIP ──
def make_zip(paths, output_path):
    output_path = validators.guard_write_path(output_path)
    paths = [validators.guard_read_path(p) for p in paths]
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            if os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    if _bl(root):
                        dirs.clear()
                        continue
                    dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))]
                    for f in files:
                        full = os.path.join(root, f)
                        validators.guard_read_path(full)
                        zf.write(full, os.path.relpath(full, os.path.dirname(p)))
            else:
                validators.guard_read_path(p)
                zf.write(p, os.path.basename(p))
    return {"ok": True, "path": output_path}

def extract_zip(path, dst_dir):
    path = validators.guard_read_path(path)
    dst_real = validators.guard_write_path(dst_dir)
    with zipfile.ZipFile(path, "r") as zf:
        validators.validate_zip_members(zf, dst_real)
        zf.extractall(dst_real)
    return {"ok": True, "dst": dst_real}

# ── Papierkorb ──
def _load_meta():
    return _load_json_or_quarantine(TRASHMETA, [])

def _save_meta(data):
    os.makedirs(TRASH, exist_ok=True)
    with open(TRASHMETA, "w") as f: json.dump(data, f, indent=2)

def delete(path):
    """Datei/Ordner in den Papierkorb verschieben."""
    path = _ok(path)
    os.makedirs(TRASH, exist_ok=True)
    item_id = str(uuid.uuid4()).replace("-", "")
    dst = os.path.join(TRASH, item_id)
    shutil.move(path, dst)
    meta = _load_meta()
    meta.append({"id": item_id, "original": path, "name": os.path.basename(path),
                 "deleted": time.time(), "is_dir": os.path.isdir(dst)})
    _save_meta(meta)
    return {"ok": True}

move_to_trash = delete  # Alias

def list_trash():
    return _load_meta()

def restore_trash(item_id):
    item_id = validators.require_slug(item_id, "trash item")
    meta = _load_meta()
    item = next((m for m in meta if m["id"] == item_id), None)
    if not item: raise FileNotFoundError("Nicht im Papierkorb")
    src = os.path.join(TRASH, item_id)
    original = validators.guard_write_path(item["original"])
    if os.path.exists(original):
        original = _unique_dst(original)
    shutil.move(src, original)
    _save_meta([m for m in meta if m["id"] != item_id])
    return {"ok": True}

def empty_trash():
    for item in _load_meta():
        try:
            item_id = validators.require_slug(item.get("id", ""), "trash item")
        except ValueError:
            continue
        p = os.path.join(TRASH, item_id)
        if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p): os.remove(p)
    _save_meta([])
    return {"ok": True}

# ── Hintergrund-Jobs für große Dateioperationen ──
def _load_jobs():
    return _load_json_or_quarantine(JOBDB, [])

def _save_jobs(data):
    os.makedirs(os.path.dirname(JOBDB), exist_ok=True)
    tmp = JOBDB + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data[-80:], f, indent=2)
    os.replace(tmp, JOBDB)

def _patch_job(job_id, **patch):
    with _job_lock:
        jobs = _load_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(patch)
                job["updated"] = time.time()
                break
        _save_jobs(jobs)

def _count_work(paths):
    total = 0
    for p in paths:
        p = validators.guard_read_path(p)
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                if _bl(root):
                    dirs.clear()
                    continue
                dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))]
                total += max(1, len(files))
        else:
            total += 1
    return max(1, total)

def _copy_tree_progress(src, dst, tick):
    os.makedirs(dst, exist_ok=False)
    shutil.copystat(src, dst, follow_symlinks=False)
    for root, dirs, files in os.walk(src):
        if _bl(root):
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))]
        rel = os.path.relpath(root, src)
        target_root = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_root, exist_ok=True)
        for d in dirs:
            os.makedirs(os.path.join(target_root, d), exist_ok=True)
        for f in files:
            src_file = validators.guard_read_path(os.path.join(root, f))
            shutil.copy2(src_file, os.path.join(target_root, f))
            tick(src_file)

def _run_file_job(job_id, action, paths, dst_dir="", output=""):
    done = 0

    def tick(current=""):
        nonlocal done
        done += 1
        _patch_job(job_id, progress=min(99, int(done * 100 / total)),
                   current=current)

    _active_jobs.add(job_id)
    try:
        total = _count_work(paths) if action in ("copy", "move", "delete") else 100
        _patch_job(job_id, status="running", progress=1, total=total)
        if action == "copy":
            dst_dir_r = _ok(dst_dir)
            for src in paths:
                src = validators.guard_read_path(src)
                dst = _unique_dst(os.path.join(dst_dir_r, os.path.basename(src)))
                if os.path.isdir(src):
                    _copy_tree_progress(src, dst, tick)
                else:
                    shutil.copy2(src, dst)
                    tick(src)
        elif action == "move":
            dst_dir_r = _ok(dst_dir)
            for src in paths:
                src = _ok(src)
                shutil.move(src, _unique_dst(os.path.join(dst_dir_r, os.path.basename(src))))
                tick(src)
        elif action == "delete":
            for src in paths:
                delete(src)
                tick(src)
        elif action == "zip":
            make_zip(paths, output)
            _patch_job(job_id, progress=99, current=output)
        else:
            raise ValueError("Unbekannter Job")
        _patch_job(job_id, status="done", progress=100, current="")
    except Exception as e:
        _patch_job(job_id, status="error", error=str(e), current="")
    finally:
        _active_jobs.discard(job_id)

def start_job(action, paths, dst_dir="", output=""):
    paths = [validators.guard_read_path(p) for p in paths if p]
    if not paths:
        raise ValueError("Keine Dateien ausgewählt")
    if action not in ("copy", "move", "delete", "zip"):
        raise ValueError("Unbekannter Job")
    if action in ("copy", "move"):
        dst_dir = _ok(dst_dir)
    if action == "zip":
        output = validators.guard_write_path(output)
    job = {
        "id": str(uuid.uuid4()).replace("-", ""),
        "action": action,
        "status": "queued",
        "progress": 0,
        "paths": paths,
        "dst_dir": dst_dir,
        "output": output,
        "current": "",
        "error": "",
        "created": time.time(),
        "updated": time.time(),
    }
    with _job_lock:
        jobs = _load_jobs()
        jobs.append(job)
        _save_jobs(jobs)
    t = threading.Thread(target=_run_file_job,
                         args=(job["id"], action, paths, dst_dir, output),
                         daemon=True)
    t.start()
    return job

def list_jobs(active_only=False):
    jobs = _load_jobs()
    for job in jobs:
        if job.get("status") == "running" and job.get("id") not in _active_jobs:
            job["status"] = "unknown"
            job["error"] = job.get("error") or "Status nach Neustart unbekannt"
    if active_only:
        jobs = [j for j in jobs if j.get("status") in ("queued", "running")]
    return sorted(jobs, key=lambda j: j.get("created", 0), reverse=True)

def get_job(job_id):
    for job in _load_jobs():
        if job["id"] == job_id:
            if job.get("status") == "running" and job.get("id") not in _active_jobs:
                job["status"] = "unknown"
                job["error"] = job.get("error") or "Status nach Neustart unbekannt"
            return job
    raise FileNotFoundError("Job nicht gefunden")

# ── Share-Links ──
def _load_shares():
    return _load_json_or_quarantine(SHAREDB, {})

def _save_shares(data):
    os.makedirs(os.path.dirname(SHAREDB), exist_ok=True)
    with open(SHAREDB, "w") as f: json.dump(data, f, indent=2)

def create_share_link(path):
    path = validators.guard_read_path(path)
    if not os.path.exists(path): raise FileNotFoundError(path)
    if not os.path.isfile(path): raise ValueError("Share-Link nur für Dateien")
    token = str(uuid.uuid4()).replace("-", "")[:16]
    s = _load_shares()
    s[token] = {"path": path, "name": os.path.basename(path), "created": time.time()}
    _save_shares(s)
    return {"token": token, "name": os.path.basename(path)}

def resolve_share(token):
    try:
        token = validators.require_slug(token, "share token")
    except ValueError:
        return None
    share = _load_shares().get(token)
    if not share:
        return None
    try:
        share["path"] = validators.guard_read_path(share["path"])
    except Exception:
        return None
    if not os.path.exists(share["path"]):
        return None
    if not os.path.isfile(share["path"]):
        return None
    return share

def list_shares():
    return _load_shares()

def delete_share(token):
    token = validators.require_slug(token, "share token")
    s = _load_shares(); s.pop(token, None); _save_shares(s)
    return {"ok": True}

def list_mounts():
    mounts = []
    try:
        with open("/proc/mounts") as f:
            lines = f.readlines()
    except Exception:
        return mounts

    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        source, mountpoint, fstype = parts[:3]
        if fstype not in {"cifs", "smb3", "nfs", "nfs4"}:
            continue
        source = source.replace("\\040", " ")
        mountpoint = mountpoint.replace("\\040", " ")
        kind = "SMB" if fstype in {"cifs", "smb3"} else "NFS"
        label = source.replace("//", "", 1) if kind == "SMB" else source
        mounts.append({
            "source": source,
            "mountpoint": mountpoint,
            "type": kind,
            "label": label or mountpoint,
        })
    return mounts

# ── Externes SMB mounten ──
def _mount_result(cmd, mountpoint):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout,
            "stderr": r.stderr,
            "mountpoint": os.path.realpath(mountpoint),
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": 127,
            "stdout": "",
            "stderr": f"{exc.filename or cmd[0]} nicht gefunden",
            "mountpoint": os.path.realpath(mountpoint),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": "",
            "stderr": " ".join(cmd) + " timed out",
            "mountpoint": os.path.realpath(mountpoint),
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
            "mountpoint": os.path.realpath(mountpoint),
        }


def mount_smb(server, share_name, mountpoint, username="guest", password=""):
    server = validators.require_remote_host(server)
    share_name = validators.require_remote_share(share_name)
    username = validators.require_mount_option_value(username, "username")
    password = validators.require_mount_option_value(password, "password")
    mountpoint = validators.guard_mountpoint(mountpoint)
    os.makedirs(mountpoint, exist_ok=True)
    opts = f"username={username},password={password},uid=0,gid=0"
    return _mount_result(
        ["mount", "-t", "cifs", f"//{server}/{share_name}", mountpoint, "-o", opts],
        mountpoint,
    )

# ── Externes NFS mounten ──
def mount_nfs(server, export, mountpoint, options=""):
    server = validators.require_remote_host(server)
    export = validators.require_remote_export(export)
    options = validators.require_mount_options(options)
    mountpoint = validators.guard_mountpoint(mountpoint)
    os.makedirs(mountpoint, exist_ok=True)
    cmd = ["mount", "-t", "nfs", f"{server}:{export}", mountpoint]
    if options:
        cmd += ["-o", options]
    return _mount_result(cmd, mountpoint)

# ── Helpers ──
def is_image(p): return Path(p).suffix.lower() in {".jpg",".jpeg",".png",".gif",".webp",".svg",".bmp"}
def is_video(p): return Path(p).suffix.lower() in {".mp4",".webm",".ogg",".mkv"}
def is_zip(p):   return Path(p).suffix.lower() in {".zip"}
