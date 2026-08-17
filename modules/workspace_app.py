"""Fail-closed lifecycle for Runvard's locally catalogued Workspace app."""
from __future__ import annotations

import fcntl, hashlib, json, os, platform, re, shutil, stat, subprocess, tempfile, time, urllib.request, uuid
from pathlib import Path
from typing import Callable, Mapping
from cryptography.hazmat.primitives import serialization

APP_DIR = Path("/opt/runvard/data/apps/workspace")
COMPOSE_FILE = APP_DIR / "docker-compose.yml"
CANDIDATE_FILE = APP_DIR / "release-candidate.json"
CANDIDATE_SHA256_FILE = APP_DIR / "release-candidate.json.sha256"
SIGNATURE_FILE = APP_DIR / "release-candidate.sig"
PROMOTION_FILE = APP_DIR / "release-promotion.json"
PROMOTION_SHA256_FILE = APP_DIR / "release-promotion.json.sha256"
TRUST_ROOT_FILE = APP_DIR / "trust-root" / "trust-root.json"
COSIGN_BIN = Path("/opt/runvard/bin/cosign")
STATUS_FILE, AUDIT_FILE, LOCK_FILE = APP_DIR / "update-status.json", APP_DIR / "update-audit.jsonl", APP_DIR / "update.lock"
BACKUP_DIR, PROBE_DUMP = APP_DIR / "backups", APP_DIR / "probe" / "source.dump"
BOOTSTRAP_MARKER = APP_DIR / ".synthetic-bootstrap-complete"
LOCAL_IMAGES = {"web": "workspace-web:local", "migrator": "workspace-migrator:local"}
REPOSITORIES = {"web": "local.workspace/workspace-web", "migrator": "local.workspace/workspace-migrator"}
STATES = ("requested", "locked", "resolved", "verified", "preflight", "backed-up", "migration-validated", "migrating", "switching", "readiness", "succeeded", "failed", "manual-recovery-required")
HEX, DIGEST = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$"), re.compile(r"^sha256:[0-9a-f]{64}$")
INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

class WorkspaceUpdateError(RuntimeError):
    def __init__(self, code): super().__init__(code); self.code = code

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
def _platform(): return "linux/arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux/amd64"
def _sha(raw): return "sha256:" + hashlib.sha256(raw).hexdigest()
def _canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def _artifact_canonical(value): return (json.dumps(value, indent=2, ensure_ascii=False, separators=(",", ": ")) + "\n").encode()
def _image_ref(value, name): return f'{value["images"][name]["repository"]}@{value["images"][name]["digest"]}'

def validate_release(value):
    top = {"schema", "releaseId", "revision", "createdAt", "platform", "migrationInventorySha256", "images", "promotion"}
    if not isinstance(value, dict) or set(value) != top or value.get("schema") != "workspace.release-candidate@1": raise WorkspaceUpdateError("release-schema-invalid")
    rev = value.get("revision")
    if not isinstance(rev, str) or not HEX.fullmatch(rev): raise WorkspaceUpdateError("release-revision-invalid")
    if value.get("releaseId") != f"workspace-{rev}": raise WorkspaceUpdateError("release-id-invalid")
    if not isinstance(value.get("createdAt"), str) or not INSTANT.fullmatch(value["createdAt"]): raise WorkspaceUpdateError("release-created-at-invalid")
    if value.get("platform") != _platform(): raise WorkspaceUpdateError("release-platform-mismatch")
    if not isinstance(value.get("migrationInventorySha256"), str) or not DIGEST.fullmatch(value["migrationInventorySha256"]): raise WorkspaceUpdateError("migration-inventory-invalid")
    if not isinstance(value.get("images"), dict) or set(value["images"]) != {"web", "migrator"}: raise WorkspaceUpdateError("release-images-invalid")
    for name, repository in REPOSITORIES.items():
        image = value["images"].get(name)
        if not isinstance(image, dict) or set(image) != {"repository", "digest", "revision"}: raise WorkspaceUpdateError("release-image-schema-invalid")
        if image.get("repository") != repository: raise WorkspaceUpdateError("release-repository-not-allowed")
        if image.get("revision") != rev: raise WorkspaceUpdateError("release-revision-mismatch")
        if not isinstance(image.get("digest"), str) or not DIGEST.fullmatch(image["digest"]): raise WorkspaceUpdateError("release-digest-invalid")
    if value.get("promotion") != {"status": "blocked-until-trust-root", "signature": None, "trustRoot": None}: raise WorkspaceUpdateError("release-candidate-promotion-invalid")
    return value

def _controlled(path, maximum):
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum: raise WorkspaceUpdateError("release-input-unsafe")
        return path.read_bytes()
    except OSError as exc: raise WorkspaceUpdateError("release-input-unsafe") from exc

def _json_file(path, maximum, code):
    raw = _controlled(path, maximum)
    try: value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc: raise WorkspaceUpdateError(code) from exc
    if not isinstance(value, dict) or _artifact_canonical(value) != raw: raise WorkspaceUpdateError(code)
    return value, raw

def _hash_file(path):
    try: value = _controlled(path, 128).decode().strip()
    except UnicodeDecodeError as exc: raise WorkspaceUpdateError("release-hash-invalid") from exc
    if not DIGEST.fullmatch(value): raise WorkspaceUpdateError("release-hash-invalid")
    return value

def _verify_artifacts():
    candidate, candidate_raw = _json_file(CANDIDATE_FILE, 65536, "release-candidate-invalid")
    candidate = validate_release(candidate); candidate_hash = _hash_file(CANDIDATE_SHA256_FILE)
    if _sha(candidate_raw) != candidate_hash: raise WorkspaceUpdateError("release-candidate-hash-mismatch")
    promotion, promotion_raw = _json_file(PROMOTION_FILE, 32768, "release-promotion-invalid")
    promotion_hash = _hash_file(PROMOTION_SHA256_FILE)
    required = {"schema", "releaseId", "revision", "candidateSha256", "signatureSha256", "verification", "status"}
    verification = promotion.get("verification")
    if (_sha(promotion_raw) != promotion_hash or set(promotion) != required or promotion.get("schema") != "workspace.release-promotion@1" or promotion.get("status") != "promoted" or not isinstance(verification, dict) or set(verification) != {"scheme", "trustKeyFingerprint", "verifiedAt"} or verification.get("scheme") != "cosign-verify-blob@1" or not isinstance(verification.get("verifiedAt"), str) or not INSTANT.fullmatch(verification["verifiedAt"])): raise WorkspaceUpdateError("release-promotion-invalid")
    signature = _controlled(SIGNATURE_FILE, 16384)
    if (promotion.get("releaseId") != candidate["releaseId"] or promotion.get("revision") != candidate["revision"] or promotion.get("candidateSha256") != candidate_hash or promotion.get("signatureSha256") != _sha(signature)): raise WorkspaceUpdateError("release-promotion-binding-invalid")
    fingerprint = verification.get("trustKeyFingerprint")
    trust, _ = _json_file(TRUST_ROOT_FILE, 32768, "release-trust-root-invalid")
    if set(trust) != {"schema", "keys"} or trust.get("schema") != "workspace.cosign-trust-root@1" or not isinstance(trust.get("keys"), list): raise WorkspaceUpdateError("release-trust-root-invalid")
    selected, seen = None, set()
    for key in trust["keys"]:
        if not isinstance(key, dict) or set(key) != {"fingerprint", "publicKeyFile", "status", "addedAt", "revokedAt"} or key.get("fingerprint") in seen: raise WorkspaceUpdateError("release-trust-root-invalid")
        seen.add(key.get("fingerprint"))
        if key.get("fingerprint") == fingerprint: selected = key
    if not selected or selected.get("status") != "active" or selected.get("revokedAt") is not None or not isinstance(selected.get("addedAt"), str) or not INSTANT.fullmatch(selected["addedAt"]) or selected["addedAt"] > _now(): raise WorkspaceUpdateError("release-trust-key-inactive")
    name = selected.get("publicKeyFile")
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".pub"): raise WorkspaceUpdateError("release-trust-key-invalid")
    key_path = TRUST_ROOT_FILE.parent / "keys" / name; key_raw = _controlled(key_path, 16384)
    if key_path.stat().st_mode & 0o022: raise WorkspaceUpdateError("release-trust-key-unsafe")
    try:
        key = serialization.load_pem_public_key(key_raw)
        der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    except (TypeError, ValueError) as exc: raise WorkspaceUpdateError("release-trust-key-invalid") from exc
    if _sha(der) != fingerprint: raise WorkspaceUpdateError("release-trust-key-fingerprint-mismatch")
    if not COSIGN_BIN.is_file() or COSIGN_BIN.is_symlink() or not os.access(COSIGN_BIN, os.X_OK): raise WorkspaceUpdateError("cosign-unavailable")
    with tempfile.TemporaryDirectory(prefix="runvard-workspace-verify-") as folder:
        paths = {}
        for label, raw in {"candidate": candidate_raw, "signature": signature, "key": key_raw}.items():
            paths[label] = Path(folder) / label; paths[label].write_bytes(raw); paths[label].chmod(0o600)
        try: result = subprocess.run([str(COSIGN_BIN), "verify-blob", "--key", str(paths["key"]), "--signature", str(paths["signature"]), "--new-bundle-format=false", "--insecure-ignore-tlog", str(paths["candidate"])], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc: raise WorkspaceUpdateError("cosign-verification-failed") from exc
        if result.returncode: raise WorkspaceUpdateError("cosign-verification-failed")
    return candidate

def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700); fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle: handle.write(_canonical(payload) + b"\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise

def _append_audit(payload):
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700); fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try: os.write(fd, _canonical(payload) + b"\n"); os.fsync(fd)
    finally: os.close(fd)

def _persist(value, state, error=None):
    value.update(state=state, updatedAt=_now(), errorCode=error)
    if state in {"succeeded", "failed", "manual-recovery-required"}: value["finishedAt"] = value["updatedAt"]
    _atomic_json(STATUS_FILE, value); _append_audit({"runId": value["runId"], "state": state, "timestamp": value["updatedAt"], "sourceRelease": value.get("sourceRelease"), "targetRelease": value.get("targetRelease"), "errorCode": error})

def _public(value):
    allowed = ("runId", "state", "startedAt", "updatedAt", "finishedAt", "sourceRelease", "targetRelease", "targetDigests", "runningDigests", "errorCode")
    return {key: value[key] for key in allowed if key in value}

def status():
    try:
        value = json.loads(STATUS_FILE.read_text()); return _public(value) if isinstance(value, dict) else {"state": "unknown"}
    except (OSError, ValueError): return {"state": "idle"}

def _run(command, *, env=None, stdout_path=None):
    handle = None
    try:
        output = subprocess.PIPE
        if stdout_path: stdout_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700); handle = stdout_path.open("wb"); os.chmod(stdout_path, 0o600); output = handle
        result = subprocess.run(list(command), cwd=APP_DIR, env=dict(env) if env else None, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc: raise WorkspaceUpdateError("command-execution-failed") from exc
    finally:
        if handle: handle.close()
    if result.returncode: raise WorkspaceUpdateError("command-failed")

def _local_env():
    env = os.environ.copy(); env.update(WORKSPACE_WEB_IMAGE=LOCAL_IMAGES["web"], WORKSPACE_MIGRATOR_IMAGE=LOCAL_IMAGES["migrator"]); return env

def start(*, initiator_role, runner=_run):
    if initiator_role != "admin": raise WorkspaceUpdateError("administrator-required")
    if not COMPOSE_FILE.is_file(): raise WorkspaceUpdateError("compose-bundle-missing")
    env = _local_env()
    for name in ("web", "migrator"): runner(("docker", "image", "inspect", LOCAL_IMAGES[name]), env=env)
    runner(("docker", "compose", "-f", "docker-compose.yml", "up", "-d", "postgres"), env=env)
    runner(("docker", "compose", "-f", "docker-compose.yml", "--profile", "update", "run", "--rm", "migrator"), env=env)
    if not BOOTSTRAP_MARKER.exists():
        runner(("docker", "compose", "-f", "docker-compose.yml", "--profile", "bootstrap", "run", "--rm", "bootstrap-development"), env=env)
        fd = os.open(BOOTSTRAP_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try: os.write(fd, b"synthetic-only\n"); os.fsync(fd)
        finally: os.close(fd)
    runner(("docker", "compose", "-f", "docker-compose.yml", "up", "-d", "web", "gateway"), env=env)
    return {"state": "running", **health()}

def stop(*, initiator_role, runner=_run):
    if initiator_role != "admin": raise WorkspaceUpdateError("administrator-required")
    runner(("docker", "compose", "-f", "docker-compose.yml", "stop"), env=_local_env()); return {"state": "stopped"}

def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:3100/health", timeout=3) as response: return {"health": "healthy" if 200 <= response.status < 300 else "unhealthy"}
    except Exception: return {"health": "unhealthy"}

def run_update(*, initiator_role, verifier=_verify_artifacts, runner=_run):
    if initiator_role != "admin": raise WorkspaceUpdateError("administrator-required")
    APP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700); lock = LOCK_FILE.open("a+"); os.chmod(LOCK_FILE, 0o600)
    try:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise WorkspaceUpdateError("update-already-running") from exc
        current = status(); state = {"runId": uuid.uuid4().hex, "state": "requested", "startedAt": _now(), "updatedAt": _now(), "sourceRelease": current.get("targetRelease"), "targetRelease": None, "targetDigests": {}, "runningDigests": current.get("runningDigests", {}), "errorCode": None}; live = False; _persist(state, "requested")
        try:
            _persist(state, "locked"); _persist(state, "resolved"); release = verifier()
            state["targetRelease"] = release["releaseId"]; state["targetDigests"] = {name: release["images"][name]["digest"] for name in ("web", "migrator")}; _persist(state, "verified")
            if not COMPOSE_FILE.is_file(): raise WorkspaceUpdateError("compose-bundle-missing")
            env = os.environ.copy(); env.update(WORKSPACE_WEB_IMAGE=_image_ref(release, "web"), WORKSPACE_MIGRATOR_IMAGE=_image_ref(release, "migrator")); backup = BACKUP_DIR / f'{state["runId"]}.dump'; _persist(state, "preflight")
            # Runvard's local Workspace policy never resolves or pulls from a
            # registry. Both verified digest references must already exist.
            runner(("docker", "image", "inspect", _image_ref(release, "web"))); runner(("docker", "image", "inspect", _image_ref(release, "migrator")))
            runner(("docker", "compose", "-f", "docker-compose.yml", "exec", "-T", "postgres", "pg_dump", "-Fc", "-U", "nushira_migration", "workspace"), env=env, stdout_path=backup)
            PROBE_DUMP.parent.mkdir(parents=True, exist_ok=True, mode=0o700); shutil.copyfile(backup, PROBE_DUMP); os.chmod(PROBE_DUMP, 0o600); _persist(state, "backed-up")
            runner(("docker", "compose", "-f", "docker-compose.yml", "--profile", "update", "run", "--rm", "migration-probe"), env=env); _persist(state, "migration-validated"); live = True; _persist(state, "migrating")
            runner(("docker", "compose", "-f", "docker-compose.yml", "--profile", "update", "run", "--rm", "migrator"), env=env); _persist(state, "switching"); runner(("docker", "compose", "-f", "docker-compose.yml", "up", "-d", "--no-deps", "web", "gateway"), env=env); _persist(state, "readiness")
            runner(("docker", "compose", "-f", "docker-compose.yml", "exec", "-T", "gateway", "node", "-e", "fetch('http://127.0.0.1:3100/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"), env=env)
            state["runningDigests"] = dict(state["targetDigests"]); _persist(state, "succeeded"); return _public(state)
        except WorkspaceUpdateError as exc: _persist(state, "manual-recovery-required" if live else "failed", exc.code); return _public(state)
    finally: fcntl.flock(lock.fileno(), fcntl.LOCK_UN); lock.close()

def update(*, initiator_role): return run_update(initiator_role=initiator_role)
