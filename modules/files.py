"""Dateimanager – komplett neu, sauber, ohne Altlasten."""
import os, re, shutil, stat, mimetypes, json, time, uuid, zipfile, subprocess, threading
import errno
import fcntl
import hashlib
import codecs
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

BLOCKED  = {"/proc", "/sys", "/dev", "/run"}
READONLY = {"/etc", "/bin", "/sbin", "/usr", "/lib", "/lib64", "/boot"}
TRASH    = "/opt/runvard/data/trash"
TRASHMETA= "/opt/runvard/data/trash/.meta.json"
SHAREDB  = "/opt/runvard/data/shares.json"
JOBDB    = "/opt/runvard/data/file_jobs.json"
MANAGED_DIRS = {"/opt/runvard/data/apps", "/opt/runvard/data/compose"}
MAX_EDIT = 2 * 1024 * 1024
_job_lock = threading.Lock()
_active_jobs = set()

def _r(p):  return os.path.realpath(p)
def _bl(p): return any(_r(p)==b or _r(p).startswith(b+"/") for b in BLOCKED)
def _ro(p): return any(_r(p)==r or _r(p).startswith(r+"/") for r in READONLY)
def _ok(p):
    if _bl(p): raise PermissionError("Gesperrt")
    if _ro(p): raise PermissionError("Schreibgeschützt")

def _unique_dst(dst):
    if not os.path.lexists(dst):
        return dst
    parent = os.path.dirname(dst)
    name = os.path.basename(dst)
    stem, ext = os.path.splitext(name)
    for i in range(1, 10000):
        suffix = " copy" if i == 1 else f" copy {i}"
        cand = os.path.join(parent, f"{stem}{suffix}{ext}")
        if not os.path.lexists(cand):
            return cand
    raise FileExistsError(dst)

def _entry(full, st):
    mime, _ = mimetypes.guess_type(full)
    return {
        "name": os.path.basename(full), "path": full,
        "is_dir": stat.S_ISDIR(st.st_mode),
        "size": st.st_size, "modified": st.st_mtime,
        "mode": stat.filemode(st.st_mode), "mime": mime or "",
        "readonly": _ro(full), "blocked": _bl(full),
    }

def list_dir(path):
    path = _r(path or "/")
    if _bl(path): raise PermissionError("Gesperrt")
    if path in MANAGED_DIRS:
        os.makedirs(path, exist_ok=True)
    if not os.path.isdir(path): raise NotADirectoryError(path)
    entries = []
    with os.scandir(path) as it:
        for e in it:
            try:
                entries.append(_entry(e.path, e.stat(follow_symlinks=False)))
            except OSError:
                continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"path": path, "parent": os.path.dirname(path), "entries": entries}

def _read_editable_stream(stream):
    if os.fstat(stream.fileno()).st_size > MAX_EDIT:
        raise ValueError("Datei zu groß")
    raw = stream.read(MAX_EDIT + 1)
    if len(raw) > MAX_EDIT:
        raise ValueError("Datei zu groß")
    return raw


def _editable_bytes(path):
    with open(path, "rb") as stream:
        if os.fstat(stream.fileno()).st_nlink > 1:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        return _read_editable_stream(stream)


def _editor_text(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "", "unsupportedEncoding"
    if "\x00" in text:
        return "", "", "unsupportedEncoding"
    endings = set(re.findall(r"\r\n|\r|\n", text))
    if len(endings) > 1:
        return text, "", "mixedEndings"
    return text, next(iter(endings), "\n"), ""


def read_file(path):
    path = _r(path)
    if _bl(path):
        raise PermissionError("Gesperrt")
    raw = _editable_bytes(path)
    text, _, reason = _editor_text(raw)
    return {"path": path, "content": text, "readonly": _ro(path) or bool(reason),
            "reason": reason, "revision": hashlib.sha256(raw).hexdigest()}


@contextmanager
def _directory_lock(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class FileEditConflict(ValueError):
    pass


def write_file(path, content, revision=None):
    path = _r(path)
    _ok(path)
    parent = os.path.dirname(path)
    with _directory_lock(parent), ExitStack() as opened:
        if not os.path.exists(path):
            if revision:
                raise FileEditConflict("editConflict")
            raw = content.encode("utf-8")
            with open(path, "xb") as stream:
                stream.write(raw)
            return {"ok": True, "revision": hashlib.sha256(raw).hexdigest()}
        info = os.stat(path)
        linked = None
        if info.st_nlink > 1:
            # Hardlink aliases can have different parent directories. Protect the
            # revision check and in-place update with the same inode lock.
            linked = opened.enter_context(open(path, "r+b"))
            fcntl.flock(linked.fileno(), fcntl.LOCK_EX)
            original = _read_editable_stream(linked)
        else:
            original = _editable_bytes(path)
        version = hashlib.sha256(original).hexdigest()
        if revision != version:
            raise FileEditConflict("editConflict")
        _, ending, reason = _editor_text(original)
        if reason:
            raise FileEditConflict(reason)
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        updated = normalized.replace("\n", ending).encode("utf-8")
        if original.startswith(codecs.BOM_UTF8):
            updated = codecs.BOM_UTF8 + updated
        if updated == original:
            return {"ok": True, "revision": version}
        if len(updated) > MAX_EDIT:
            raise ValueError("Datei zu groß")
        if linked is not None:
            try:
                linked.seek(0)
                linked.write(updated)
                linked.truncate()
                linked.flush()
                os.fsync(linked.fileno())
            except OSError:
                linked.seek(0)
                linked.write(original)
                linked.truncate()
                raise
        else:
            with tempfile.NamedTemporaryFile(dir=parent, delete=False) as staged:
                try:
                    current = os.fstat(staged.fileno())
                    if (current.st_uid, current.st_gid) != (info.st_uid, info.st_gid):
                        os.fchown(staged.fileno(), info.st_uid, info.st_gid)
                    shutil.copystat(path, staged.name)
                    staged.write(updated)
                    staged.flush()
                    os.fsync(staged.fileno())
                    os.replace(staged.name, path)
                finally:
                    Path(staged.name).unlink(missing_ok=True)
    return {"ok": True, "revision": hashlib.sha256(updated).hexdigest()}


def rename(path, new_name):
    path = os.path.abspath(path)
    _ok(path)
    if not new_name or "/" in new_name or new_name in (".", ".."):
        raise ValueError("Ungültiger Name")
    dst = os.path.join(os.path.dirname(path), new_name)
    if path != dst:
        _rename_without_overwrite(path, dst, stat.S_ISDIR(os.lstat(path).st_mode))
    return {"ok": True, "path": dst}

def copy_item(src, dst_dir):
    src = _r(src); dst_dir = _r(dst_dir)
    if _bl(src): raise PermissionError("Gesperrt")
    _ok(dst_dir)
    dst = _unique_dst(os.path.join(dst_dir, os.path.basename(src)))
    if os.path.isdir(src): shutil.copytree(src, dst)
    else: shutil.copy2(src, dst)
    return {"ok": True, "dst": dst}

def move(src, dst_dir):
    src = _r(src); dst_dir = _r(dst_dir); _ok(src); _ok(dst_dir)
    dst = _unique_dst(os.path.join(dst_dir, os.path.basename(src)))
    shutil.move(src, dst)
    return {"ok": True, "dst": dst}

def mkdir(path, name):
    path = _r(path); _ok(path)
    new = os.path.join(path, name)
    os.makedirs(new, exist_ok=False)
    return {"ok": True, "path": new}

def file_info(path):
    path = _r(path); st = os.stat(path)
    mime, _ = mimetypes.guess_type(path)
    return {"name": os.path.basename(path), "path": path, "size": st.st_size,
            "modified": st.st_mtime, "mode": stat.filemode(st.st_mode),
            "octal": oct(stat.S_IMODE(st.st_mode))[2:], "uid": st.st_uid,
            "gid": st.st_gid, "mime": mime, "is_dir": os.path.isdir(path)}

def search(base, query, max_results=200):
    base = _r(base); results = []; q = query.lower()
    for root, dirs, files in os.walk(base):
        if _bl(root): dirs.clear(); continue
        dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))]
        for name in dirs + files:
            if q in name.lower():
                full = os.path.join(root, name)
                try:
                    entry = _entry(full, os.lstat(full))
                except OSError:
                    continue
                if entry["blocked"]:
                    continue
                results.append(entry)
                if len(results) >= max_results: return results
    return results

# ── ZIP ──
def make_zip(paths, output_path):
    output_path = _r(output_path); _ok(os.path.dirname(output_path))
    paths = list(dict.fromkeys(_r(p) for p in paths))
    for p in paths:
        if _bl(p): raise PermissionError("Gesperrt")
    selected = set(paths)
    if output_path in selected:
        raise ValueError(output_path)
    sources = [p for p in paths if not any(str(parent) in selected for parent in Path(p).parents)]
    archive_root = os.path.commonpath([os.path.dirname(p) for p in sources])
    if os.path.lexists(output_path):
        raise FileExistsError(output_path)
    fd, staged = tempfile.mkstemp(dir=os.path.dirname(output_path))
    os.close(fd)
    try:
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sources:
                if os.path.isdir(p):
                    for root, dirs, files in os.walk(p):
                        if _bl(root):
                            dirs.clear()
                            continue
                        dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))]
                        files = [f for f in files if _r(os.path.join(root, f)) not in (output_path, staged)]
                        if not dirs and not files:
                            zf.write(root, os.path.relpath(root, archive_root))
                        for f in files:
                            full = os.path.join(root, f)
                            zf.write(full, os.path.relpath(full, archive_root))
                else:
                    zf.write(p, os.path.relpath(p, archive_root))
        _rename_without_overwrite(staged, output_path, False)
    finally:
        Path(staged).unlink(missing_ok=True)
    return {"ok": True, "path": output_path}

def extract_zip(path, dst_dir):
    path = _r(path); dst_dir = _r(dst_dir); _ok(dst_dir)
    dst_real = _r(dst_dir)
    with zipfile.ZipFile(path, "r") as archive, tempfile.TemporaryDirectory(dir=dst_dir) as staging:
        # Stage and verify the entire archive before publishing any member.
        for member in archive.infolist():
            name = Path(member.filename)
            if name.is_absolute() or ".." in name.parts or not name.parts:
                raise PermissionError("Unsicherer ZIP-Pfad")
            target = os.path.join(dst_real, *name.parts)
            if _r(target) != target:
                raise PermissionError("Unsicherer ZIP-Pfad")
            if os.path.lexists(target) and not (member.is_dir() and os.path.isdir(target)):
                raise FileExistsError(target)
            local = Path(staging, *name.parts)
            if member.is_dir():
                local.mkdir(parents=True, exist_ok=True)
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, local.open("xb") as output:
                    shutil.copyfileobj(source, output)
        created_files, created_dirs = [], []
        with _directory_lock(dst_dir):
            try:
                for root, dirs, names in os.walk(staging):
                    out = Path(dst_dir) / Path(root).relative_to(staging)
                    for directory in dirs:
                        target = out / directory
                        if not target.exists():
                            target.mkdir()
                            created_dirs.append(target)
                        elif target.is_symlink() or not target.is_dir():
                            raise FileExistsError(target)
                    for name in names:
                        target = out / name
                        source = Path(root, name)
                        try:
                            _rename_without_overwrite(source, target, False)
                        except OSError as exc:
                            if exc.errno != errno.EXDEV:
                                raise
                            # A subdirectory may be a separate mounted filesystem.
                            with source.open("rb") as src, target.open("xb") as dst:
                                created_files.append(target)
                                shutil.copyfileobj(src, dst)
                        else:
                            created_files.append(target)
            except OSError:
                for target in reversed(created_files):
                    target.unlink()
                for target in reversed(created_dirs):
                    target.rmdir()
                raise
    return {"ok": True, "dst": dst_dir}

# ── Papierkorb ──
@contextmanager
def _trash_transaction():
    os.makedirs(TRASH, mode=0o700, exist_ok=True)
    with open(os.path.join(TRASH, ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _load_meta():
    try:
        with open(TRASHMETA) as f: return json.load(f)
    except FileNotFoundError: return []

def _save_meta(data):
    with tempfile.NamedTemporaryFile("w", dir=TRASH, delete=False) as tmp:
        try:
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(tmp.name, TRASHMETA)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

def delete(path):
    """Datei/Ordner in den Papierkorb verschieben."""
    path = _r(path); _ok(path)
    item_id = str(uuid.uuid4()).replace("-", "")
    dst = os.path.join(TRASH, item_id)
    with _trash_transaction():
        meta = _load_meta()
        shutil.move(path, dst)
        meta.append({"id": item_id, "original": path, "name": os.path.basename(path),
                     "deleted": time.time(), "is_dir": os.path.isdir(dst)})
        _save_meta(meta)
    return {"ok": True}

move_to_trash = delete  # Alias

def list_trash():
    return _load_meta()

def _rename_without_overwrite(src, dst, directory):
    if directory:
        os.mkdir(dst, 0o700)
    else:
        os.close(os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    try:
        os.replace(src, dst)
    except OSError:
        if directory:
            os.rmdir(dst)
        else:
            os.unlink(dst)
        raise
    return dst


def _rename_to_free_path(src, original, directory):
    while True:
        dst = _unique_dst(original)
        try:
            return _rename_without_overwrite(src, dst, directory)
        except FileExistsError:
            continue


def _restore_to_free_path(src, original):
    parent = os.path.dirname(original)
    _ok(parent)
    directory = stat.S_ISDIR(os.lstat(src).st_mode)
    try:
        return _rename_to_free_path(src, original, directory)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    # Finish the cross-filesystem copy privately before exposing the final name.
    with tempfile.TemporaryDirectory(dir=parent) as staging:
        copy = os.path.join(staging, "item")
        if directory:
            shutil.copytree(src, copy, symlinks=True)
        else:
            shutil.copy2(src, copy, follow_symlinks=False)
        dst = _rename_to_free_path(copy, original, directory)
    if directory:
        shutil.rmtree(src)
    else:
        os.unlink(src)
    return dst


def restore_trash(item_id):
    with _trash_transaction():
        meta = _load_meta()
        item = next((m for m in meta if m["id"] == item_id), None)
        if not item: raise FileNotFoundError("Nicht im Papierkorb")
        dst = _restore_to_free_path(os.path.join(TRASH, item_id), item["original"])
        _save_meta([m for m in meta if m["id"] != item_id])
    return {"ok": True, "dst": dst}

def empty_trash():
    with _trash_transaction():
        for item in _load_meta():
            p = os.path.join(TRASH, item["id"])
            if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p): os.remove(p)
        _save_meta([])
    return {"ok": True}

# ── Hintergrund-Jobs für große Dateioperationen ──
def _load_jobs():
    try:
        with open(JOBDB) as f:
            return json.load(f)
    except:
        return []

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
        p = _r(p)
        if _bl(p):
            raise PermissionError("Gesperrt")
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
            shutil.copy2(os.path.join(root, f), os.path.join(target_root, f))
            tick(os.path.join(root, f))

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
            dst_dir_r = _r(dst_dir); _ok(dst_dir_r)
            for src in paths:
                src = _r(src)
                dst = _unique_dst(os.path.join(dst_dir_r, os.path.basename(src)))
                if os.path.isdir(src):
                    _copy_tree_progress(src, dst, tick)
                else:
                    shutil.copy2(src, dst)
                    tick(src)
        elif action == "move":
            dst_dir_r = _r(dst_dir); _ok(dst_dir_r)
            for src in paths:
                src = _r(src); _ok(src)
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
    paths = [_r(p) for p in paths if p]
    if not paths:
        raise ValueError("Keine Dateien ausgewählt")
    if action not in ("copy", "move", "delete", "zip"):
        raise ValueError("Unbekannter Job")
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
    try:
        with open(SHAREDB) as f: return json.load(f)
    except: return {}

def _save_shares(data):
    os.makedirs(os.path.dirname(SHAREDB), exist_ok=True)
    with open(SHAREDB, "w") as f: json.dump(data, f, indent=2)

def directory_download(path):
    """Build the folder download from regular files, without following links."""
    stream = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for root, dirs, names, dirfd in os.fwalk(path, follow_symlinks=False):
                dirs[:] = [d for d in dirs if not _bl(os.path.join(root, d))
                           and not os.path.islink(os.path.join(root, d))]
                relative = Path(root).relative_to(path)
                if relative.parts:
                    archive.writestr(relative.as_posix() + "/", b"")
                for name in names:
                    if not stat.S_ISREG(os.stat(name, dir_fd=dirfd, follow_symlinks=False).st_mode):
                        continue
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
                    with os.fdopen(fd, "rb") as source:
                        if stat.S_ISREG(os.fstat(fd).st_mode):
                            with archive.open((relative / name).as_posix(), "w", force_zip64=True) as out:
                                shutil.copyfileobj(source, out)
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def create_share_link(path):
    path = _r(path)
    if _bl(path) or not (os.path.isfile(path) or os.path.isdir(path)):
        raise FileNotFoundError(path)
    token = str(uuid.uuid4()).replace("-", "")[:16]
    s = _load_shares()
    s[token] = {"path": path, "name": os.path.basename(path), "created": time.time()}
    _save_shares(s)
    return {"token": token, "name": os.path.basename(path)}

def resolve_share(token):
    return _load_shares().get(token)

def list_shares():
    return _load_shares()

def delete_share(token):
    s = _load_shares(); s.pop(token, None); _save_shares(s)
    return {"ok": True}

def _mount_field(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def _disk_labels():
    labels = {}
    for link in Path("/dev/disk/by-label").glob("*"):
        name = re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m[1].decode()),
                      os.fsencode(link.name)).decode("utf-8", "replace")
        labels[_r(link)] = name
    return labels


def _device_source(number, fallback):
    # Mount sources may retain a temporary alias from the mounting process.
    try:
        fields = Path("/sys/dev/block", number, "uevent").read_text().splitlines()
    except OSError:
        return fallback
    return next(("/dev/" + f[8:] for f in fields if f.startswith("DEVNAME=")), fallback)


def _disk_model(source):
    device = Path("/sys/class/block", os.path.basename(_r(source))).resolve()
    if (device / "partition").is_file():
        device = device.parent
    try:
        return (device / "device/model").read_text().strip()
    except OSError:
        return ""


def _parse_mounts(lines, labels):
    mounts = {}
    remote_types = {"cifs": "SMB", "smb3": "SMB", "nfs": "NFS", "nfs4": "NFS"}
    for line in lines:
        left, sep, right = line.partition(" - ")
        fields, fs = left.split(), right.split()
        if not sep or len(fields) < 6 or len(fs) < 2:
            continue
        root, target = map(_mount_field, fields[3:5])
        fstype, source = fs[0], _mount_field(fs[1])
        # Multiple mounts can cover the same directory. Only the top one is visible.
        mounts.pop(target, None)
        kind = remote_types.get(fstype)
        if not kind:
            if not source.startswith("/dev/") or fstype in {"squashfs", "devtmpfs"}:
                continue
            if target == "/" or target.split("/")[1] in {
                "boot", "proc", "sys", "dev", "run", "etc", "usr", "lib", "lib64", "snap"
            }:
                continue
            if target.startswith(("/var/lib/docker/", "/var/lib/containers/", "/var/lib/kubelet/")):
                continue
            kind = "local"
        elif target.split("/")[1] in {"proc", "sys", "dev", "run"}:
            continue
        if kind == "local":
            source = _device_source(fields[2], source)
            label = labels.get(_r(source)) or _disk_model(source) or os.path.basename(target)
        else:
            label = source.removeprefix("//")
        mounts[target] = {"mountpoint": target, "source": source, "type": kind,
                          "label": label, "root": root, "device": fields[2], "fstype": fstype,
                          "mount_id": fields[0]}
    return sorted(mounts.values(), key=lambda m: m["mountpoint"])


def list_mounts():
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as stream:
            return _parse_mounts(stream, _disk_labels())
    except FileNotFoundError:
        return []


# ── Externes SMB mounten ──
def mount_smb(server, share_name, mountpoint, username="guest", password=""):
    os.makedirs(mountpoint, exist_ok=True)
    opts = f"username={username},password={password},uid=0,gid=0"
    r = subprocess.run(["mount", "-t", "cifs", f"//{server}/{share_name}", mountpoint, "-o", opts],
                       capture_output=True, text=True, timeout=30)
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "mountpoint": os.path.realpath(mountpoint),
    }

# ── Externes NFS mounten ──
def mount_nfs(server, export, mountpoint, options=""):
    os.makedirs(mountpoint, exist_ok=True)
    cmd = ["mount", "-t", "nfs", f"{server}:{export}", mountpoint]
    if options:
        cmd += ["-o", options]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "mountpoint": os.path.realpath(mountpoint),
    }

# ── Helpers ──
def is_image(p): return Path(p).suffix.lower() in {".jpg",".jpeg",".png",".gif",".webp",".svg",".bmp"}
def is_video(p): return Path(p).suffix.lower() in {".mp4",".webm",".ogg",".mkv"}
def is_zip(p):   return Path(p).suffix.lower() in {".zip"}
