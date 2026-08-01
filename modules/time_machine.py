"""Managed SMB Time Machine destinations for runvard.

macOS owns backup scheduling, encryption, verification, and restoration. This
module owns the server-side target registry, storage boundary, and generated
Samba include. All commands use argument lists; no request value reaches a
shell interpreter.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any
import ipaddress


STATE_FILE = os.path.join(
    os.environ.get("RUNVARD_DATA_DIR", "/opt/runvard/data"), "time_machine.json",
)
SMB_CONF = "/etc/samba/smb.conf"
MANAGED_SMB_CONF = "/etc/samba/runvard-timemachine.conf"
AVAHI_SERVICE = "/etc/avahi/services/runvard-time-machine.service"
REPLICATION_KEY = os.path.join(
    os.environ.get("RUNVARD_DATA_DIR", "/opt/runvard/data"),
    "time-machine-keys", "id_ed25519",
)
KNOWN_HOSTS_FILE = os.path.join(
    os.environ.get("RUNVARD_DATA_DIR", "/opt/runvard/data"),
    "time-machine-keys", "known_hosts",
)
RECEIVER_AUTHORIZED_KEYS = "/var/lib/runvard-replica/.ssh/authorized_keys"

GIB = 1024**3
STATE_VERSION = 1
_LOCK = threading.RLock()
_ACCOUNT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}\$?$")
_SHARE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_SECRET_KEYS = {
    "password", "passphrase", "secret", "private_key", "confirm_token",
}
_REPLICATION_LOCK = threading.Lock()
_ACTIVE_REPLICATION_KEYS: set[tuple[str, str]] = set()


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "targets": [],
        "protection_points": [],
        "replications": [],
        "jobs": [],
        "events": [],
        "alert_cooldowns": {},
    }


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                return True
            if _contains_secret(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _atomic_write(path: str, content: str, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_state() -> dict[str, Any]:
    with _LOCK:
        try:
            with open(STATE_FILE, encoding="utf-8") as handle:
                state = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return _default_state()
        if not isinstance(state, dict) or not isinstance(state.get("targets"), list):
            return _default_state()
        result = _default_state()
        result.update(state)
        return copy.deepcopy(result)


def save_state(state: dict[str, Any]) -> None:
    if _contains_secret(state):
        raise ValueError("Time Machine state must not contain secrets")
    payload = copy.deepcopy(state)
    payload["version"] = STATE_VERSION
    for key in ("targets", "protection_points", "replications", "jobs", "events"):
        payload.setdefault(key, [])
    payload.setdefault("alert_cooldowns", {})
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with _LOCK:
        _atomic_write(STATE_FILE, serialized, 0o600)


def _run(command: list[str], *, input_text: str | None = None,
         timeout: int = 60) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(exc)}


def _run_pipe(source: list[str], destination: list[str], *,
              timeout: int = 86_400) -> dict[str, Any]:
    """Stream one fixed argv command into another without a shell."""
    producer = None
    consumer = None
    with tempfile.TemporaryFile() as producer_errors:
        try:
            producer = subprocess.Popen(
                source, stdout=subprocess.PIPE, stderr=producer_errors,
            )
            consumer = subprocess.Popen(
                destination, stdin=producer.stdout, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if producer.stdout:
                producer.stdout.close()
            stdout, consumer_stderr = consumer.communicate(timeout=timeout)
            producer_code = producer.wait(timeout=30)
            producer_errors.seek(0)
            producer_stderr = producer_errors.read()
            ok = producer_code == 0 and consumer.returncode == 0
            return {
                "ok": ok,
                "returncode": consumer.returncode if consumer.returncode else producer_code,
                "stdout": stdout.decode("utf-8", "replace"),
                "stderr": (
                    producer_stderr + consumer_stderr
                ).decode("utf-8", "replace"),
            }
        except (OSError, subprocess.SubprocessError) as exc:
            for process in (consumer, producer):
                if process and process.poll() is None:
                    process.kill()
            return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(exc)}


def _mount_info(path: str) -> dict[str, str] | None:
    result = _run(["findmnt", "--json", "--target", path,
                   "--output", "TARGET,SOURCE,FSTYPE,OPTIONS"])
    if not result["ok"]:
        return None
    try:
        filesystems = json.loads(result["stdout"]).get("filesystems", [])
        row = filesystems[0]
        return {
            "mountpoint": os.path.realpath(row.get("target", "")),
            "source": str(row.get("source", "")),
            "fstype": str(row.get("fstype", "")),
            "options": str(row.get("options", "")),
        }
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _user_info(name: str) -> dict[str, Any] | None:
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        return None
    return {"name": user.pw_name, "uid": user.pw_uid, "gid": user.pw_gid}


def _set_owner(path: str, user: dict[str, Any]) -> None:
    os.chmod(path, 0o700)
    os.chown(path, int(user["uid"]), int(user["gid"]))


def _clean_text(value: str, *, label: str, maximum: int = 80) -> str:
    value = str(value or "").strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid {label}")
    return value


def _validate_account(value: str) -> str:
    value = _clean_text(value, label="account", maximum=32)
    if not _ACCOUNT_RE.fullmatch(value):
        raise ValueError("Invalid account")
    return value


def _validate_share(value: str) -> str:
    if not _SHARE_RE.fullmatch(str(value or "")):
        raise ValueError("Invalid share name")
    return value


def _validate_target_path(value: str) -> str:
    value = str(value or "")
    if "\n" in value or "\r" in value or not os.path.isabs(value):
        raise ValueError("Invalid target path")
    normalized = os.path.normpath(value)
    if normalized in ("/", "."):
        raise ValueError("Invalid target path")
    return normalized


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return (slug or "mac")[:40]


def _size_for_samba(value: int) -> str:
    if value <= 0 or value % GIB:
        raise ValueError("Advertised Time Machine size must use whole GiB")
    return f"{value // GIB}G"


def render_samba_config(targets: list[dict[str, Any]]) -> str:
    blocks = [
        "# Managed by runvard. Manual changes are reported as configuration drift.",
        "# Time Machine targets are dedicated shares; do not store other files here.",
    ]
    for target in sorted(targets, key=lambda item: item.get("share_name", "")):
        if not target.get("enabled", True):
            continue
        share = _validate_share(str(target.get("share_name", "")))
        owner = _validate_account(str(target.get("owner", "")))
        path = _validate_target_path(str(target.get("path", "")))
        target_id = str(target.get("id", ""))
        if not _ID_RE.fullmatch(target_id):
            raise ValueError("Invalid target id")
        size = _size_for_samba(int(target.get("advertised_bytes", 0)))
        blocks.extend([
            "",
            f"[{share}]",
            f"   comment = runvard Time Machine target {target_id}",
            f"   path = {path}",
            "   browseable = yes",
            "   read only = no",
            "   guest ok = no",
            f"   valid users = {owner}",
            "   create mask = 0600",
            "   directory mask = 0700",
            "   vfs objects = catia fruit streams_xattr",
            "   fruit:time machine = yes",
            f"   fruit:time machine max size = {size}",
            "   server smb encrypt = required",
            "   hosts allow = 127.0.0.1 ::1 10.0.0.0/8 172.16.0.0/12 "
            "192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 fc00::/7 fe80::/10",
        ])
    return "\n".join(blocks).rstrip() + "\n"


def render_avahi_service(targets: list[dict[str, Any]]) -> str:
    enabled = [target for target in targets if target.get("enabled", True)]
    records = []
    for index, target in enumerate(sorted(
        enabled, key=lambda item: item.get("share_name", ""),
    )):
        share = _validate_share(str(target.get("share_name", "")))
        target_id = str(target.get("id", ""))
        if not _ID_RE.fullmatch(target_id):
            raise ValueError("Invalid target id")
        records.append(f"    <txt-record>dk{index}=adVN={share},adVF=0x82</txt-record>")
    services = []
    if enabled:
        services.append((
            "  <service>\n"
            "    <type>_smb._tcp</type>\n"
            "    <port>445</port>\n"
            "  </service>"
        ))
        services.append(
            "  <service>\n"
            "    <type>_adisk._tcp</type>\n"
            "    <port>9</port>\n"
            + "\n".join(records) + "\n"
            "  </service>"
        )
    body = "\n".join(services)
    return (
        '<?xml version="1.0" standalone="no"?>\n'
        '<!DOCTYPE service-group SYSTEM "avahi-service.dtd">\n'
        '<service-group>\n'
        '  <name replace-wildcards="yes">runvard Time Machine on %h</name>\n'
        f'{body}\n'
        '</service-group>\n'
    )


def _restore_file(path: str, previous: bytes | None, mode: int) -> None:
    if previous is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.restore.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _include_line() -> str:
    return f"include = {MANAGED_SMB_CONF}"


def _main_with_include(content: str, include_path: str) -> str:
    canonical = _include_line()
    if canonical in content:
        return content.replace(canonical, f"include = {include_path}", 1)
    directive = (
        "\n# runvard managed Time Machine targets\n"
        f"include = {include_path}\n"
    )
    lines = content.splitlines(keepends=True)
    saw_global = False
    insert_at = len(lines)
    for index, line in enumerate(lines):
        section = line.strip().lower()
        if section == "[global]":
            saw_global = True
            continue
        if saw_global and section.startswith("[") and section.endswith("]"):
            insert_at = index
            break
    if not saw_global:
        prefix = "[global]\n" + directive
        return prefix + ("\n" if content and not content.startswith("\n") else "") + content
    lines.insert(insert_at, directive)
    return "".join(lines)


def _activate_samba_config(targets: list[dict[str, Any]]) -> None:
    if not os.path.isfile(SMB_CONF):
        raise RuntimeError("Samba configuration is missing")

    rendered = render_samba_config(targets)
    previous_main = Path(SMB_CONF).read_bytes()
    previous_include = Path(MANAGED_SMB_CONF).read_bytes() \
        if os.path.exists(MANAGED_SMB_CONF) else None
    previous_main_mode = stat.S_IMODE(os.stat(SMB_CONF).st_mode)
    previous_include_mode = stat.S_IMODE(os.stat(MANAGED_SMB_CONF).st_mode) \
        if os.path.exists(MANAGED_SMB_CONF) else 0o640

    include_dir = str(Path(MANAGED_SMB_CONF).parent)
    Path(include_dir).mkdir(parents=True, exist_ok=True)
    include_fd, include_temp = tempfile.mkstemp(
        prefix=".runvard-timemachine.validate.", dir=include_dir,
    )
    main_fd, main_temp = tempfile.mkstemp(
        prefix=".smb.validate.", dir=str(Path(SMB_CONF).parent),
    )
    try:
        with os.fdopen(include_fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        main_content = previous_main.decode("utf-8")
        with os.fdopen(main_fd, "w", encoding="utf-8") as handle:
            handle.write(_main_with_include(main_content, include_temp))

        validation = _run(["testparm", "-s", main_temp])
        if not validation["ok"]:
            raise RuntimeError(validation.get("stderr") or "Samba validation failed")

        _atomic_write(MANAGED_SMB_CONF, rendered, 0o640)
        if _include_line() not in main_content:
            _atomic_write(SMB_CONF, _main_with_include(main_content, MANAGED_SMB_CONF),
                          previous_main_mode)

        reload_result = _run(["systemctl", "reload", "smbd"])
        if not reload_result["ok"]:
            raise RuntimeError(reload_result.get("stderr") or "Samba reload failed")
        active_result = _run(["systemctl", "is-active", "--quiet", "smbd"])
        if not active_result["ok"]:
            raise RuntimeError("Samba is not active after reload")
    except Exception:
        _restore_file(SMB_CONF, previous_main, previous_main_mode)
        _restore_file(MANAGED_SMB_CONF, previous_include, previous_include_mode)
        raise
    finally:
        for temp_name in (include_temp, main_temp):
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _activate_avahi_config(targets: list[dict[str, Any]]) -> None:
    enabled = [target for target in targets if target.get("enabled", True)]
    previous = Path(AVAHI_SERVICE).read_bytes() if os.path.exists(AVAHI_SERVICE) else None
    previous_mode = stat.S_IMODE(os.stat(AVAHI_SERVICE).st_mode) \
        if os.path.exists(AVAHI_SERVICE) else 0o644
    try:
        if enabled:
            rendered = render_avahi_service(targets)
            ET.fromstring(rendered.split("\n", 2)[2])
            _atomic_write(AVAHI_SERVICE, rendered, 0o644)
        else:
            try:
                os.unlink(AVAHI_SERVICE)
            except FileNotFoundError:
                pass
        reload_result = _run(["systemctl", "reload", "avahi-daemon"])
        if not reload_result["ok"]:
            raise RuntimeError(reload_result.get("stderr") or "Avahi reload failed")
        active_result = _run([
            "systemctl", "is-active", "--quiet", "avahi-daemon",
        ])
        if not active_result["ok"]:
            raise RuntimeError("Avahi is not active after reload")
    except Exception:
        _restore_file(AVAHI_SERVICE, previous, previous_mode)
        raise


def _managed_config_has_drift(targets: list[dict[str, Any]]) -> bool:
    expected = render_samba_config(targets)
    try:
        actual = Path(MANAGED_SMB_CONF).read_text(encoding="utf-8")
    except OSError:
        return bool(targets)
    return actual != expected


def _activate_managed_config(targets: list[dict[str, Any]],
                             previous_targets: list[dict[str, Any]],
                             allow_drift: bool = False) -> None:
    if not allow_drift and _managed_config_has_drift(previous_targets):
        raise RuntimeError(
            "Managed Time Machine configuration has drift; reconcile it explicitly"
        )
    _activate_samba_config(targets)
    try:
        _activate_avahi_config(targets)
    except Exception:
        _activate_samba_config(previous_targets)
        raise


def _set_smb_password(owner: str, password: str) -> None:
    if len(password or "") < 12 or len(password) > 1024:
        raise ValueError("Samba password must contain at least 12 characters")
    result = _run(
        ["smbpasswd", "-a", "-s", owner],
        input_text=f"{password}\n{password}\n",
        timeout=30,
    )
    if not result["ok"]:
        raise RuntimeError(result.get("stderr") or "Could not set Samba password")


def _backend_for_mount(info: dict[str, str]) -> str:
    fstype = info.get("fstype", "").lower()
    if fstype == "zfs":
        return "zfs"
    if fstype == "btrfs":
        return "btrfs"
    return "directory"


def _provision_storage(target: dict[str, Any]) -> None:
    path = target["path"]
    backend = target["backend"]
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if backend == "zfs":
        dataset = target["dataset"]
        created = _run(["zfs", "create", "-p", "-o", f"mountpoint={path}", dataset])
        if not created["ok"]:
            raise RuntimeError(created.get("stderr") or "Could not create ZFS dataset")
        quota = _run(["zfs", "set", f"refquota={target['hard_limit_bytes']}", dataset])
        if not quota["ok"]:
            raise RuntimeError(quota.get("stderr") or "Could not set ZFS quota")
    elif backend == "btrfs":
        enabled = _run(["btrfs", "quota", "enable", target["storage_root"]])
        if not enabled["ok"] and "already enabled" not in (
            enabled.get("stderr", "") + enabled.get("stdout", "")
        ).lower():
            raise RuntimeError(enabled.get("stderr") or "Could not enable Btrfs quotas")
        created = _run(["btrfs", "subvolume", "create", path])
        if not created["ok"]:
            raise RuntimeError(created.get("stderr") or "Could not create Btrfs subvolume")
        quota = _run(["btrfs", "qgroup", "limit", str(target["hard_limit_bytes"]), path])
        if not quota["ok"]:
            raise RuntimeError(quota.get("stderr") or "Could not set Btrfs quota")
    else:
        os.makedirs(path, mode=0o700, exist_ok=False)


def _cleanup_empty_storage(target: dict[str, Any]) -> None:
    path = target.get("path", "")
    if not path or not os.path.isdir(path):
        return
    try:
        if os.listdir(path):
            return
        backend = target.get("backend")
        if backend == "zfs" and target.get("dataset"):
            _run(["zfs", "destroy", target["dataset"]])
        elif backend == "btrfs":
            _run(["btrfs", "subvolume", "delete", path])
        else:
            os.rmdir(path)
        parent = os.path.dirname(path)
        try:
            os.rmdir(parent)
        except OSError:
            pass
    except OSError:
        return


def _validate_storage_root(storage_root: str) -> tuple[str, dict[str, str]]:
    root = os.path.realpath(_validate_target_path(storage_root))
    if not os.path.isdir(root):
        raise ValueError("Storage root does not exist")
    if os.path.islink(storage_root):
        raise ValueError("Storage root must not be a symbolic link")
    info = _mount_info(root)
    if not info or os.path.realpath(info.get("mountpoint", "")) != root:
        raise ValueError("Storage root must be an active mount point")
    options = set(info.get("options", "").split(","))
    if "ro" in options:
        raise ValueError("Storage root is read-only")
    return root, info


def _assert_creation_preflight() -> None:
    status = system_status()
    aapl = status.get("aapl_audit", {})
    if not aapl.get("ok", False):
        risky = ", ".join(aapl.get("risky_shares", [])) or "global configuration"
        raise RuntimeError(f"Samba AAPL compatibility check failed: {risky}")
    if status.get("managed_config", {}).get("drift"):
        raise RuntimeError("Managed Time Machine configuration must be reconciled")
    if not status.get("samba", {}).get("installed") \
            or not status.get("samba", {}).get("active") \
            or not status.get("samba", {}).get("configuration_valid"):
        raise RuntimeError("Samba is not ready for Time Machine target creation")
    if not status.get("avahi", {}).get("active"):
        raise RuntimeError("Avahi is not ready for Time Machine target creation")
    if not status.get("worker", {}).get("timer_active"):
        raise RuntimeError("Time Machine maintenance worker is not active")


def create_target(*, display_name: str, owner: str, storage_root: str,
                  capacity_gb: int, source_capacity_gb: int = 0,
                  password: str = "", create_account: bool = False,
                  client_encryption_required: bool = False) -> dict[str, Any]:
    if client_encryption_required is not True:
        raise ValueError("client-side encryption policy must be explicitly confirmed")
    display_name = _clean_text(display_name, label="display name")
    owner = _validate_account(owner)
    try:
        capacity_gb = int(capacity_gb)
        source_capacity_gb = int(source_capacity_gb or 0)
    except (TypeError, ValueError):
        raise ValueError("Invalid capacity") from None
    if capacity_gb < 10 or capacity_gb > 10_000_000:
        raise ValueError("Capacity must be between 10 GB and 10 PB")
    if source_capacity_gb < 0:
        raise ValueError("Invalid source capacity")
    _assert_creation_preflight()

    with _LOCK:
        root, mount = _validate_storage_root(storage_root)
        state = load_state()
        if len(state["targets"]) >= 20:
            raise ValueError("A maximum of 20 Time Machine targets is supported")
        target_id = secrets.token_hex(8)
        slug = _slug(display_name)
        share_name = f"tm-{slug}-{target_id[:8]}"[:80]
        target_path = os.path.realpath(os.path.join(
            root, "runvard-time-machine", f"{slug}-{target_id[:8]}",
        ))
        base = os.path.realpath(os.path.join(root, "runvard-time-machine"))
        if os.path.commonpath([target_path, base]) != base:
            raise ValueError("Target path escapes storage root")
        if any(item.get("share_name") == share_name for item in state["targets"]):
            raise ValueError("Share name already exists")

        hard_limit = capacity_gb * GIB
        advertised_gib = max(1, int(capacity_gb * 0.95))
        backend = _backend_for_mount(mount)
        target = {
            "id": target_id,
            "display_name": display_name,
            "share_name": share_name,
            "owner": owner,
            "storage_root": root,
            "mount_source": mount.get("source", ""),
            "filesystem": mount.get("fstype", ""),
            "path": target_path,
            "backend": backend,
            "quota_mode": "hard" if backend in ("zfs", "btrfs") else "reported",
            "hard_limit_bytes": hard_limit,
            "advertised_bytes": advertised_gib * GIB,
            "source_capacity_bytes": source_capacity_gb * GIB,
            "enabled": True,
            "status": "waiting",
            "health_code": "waiting_for_first_backup",
            "created_at": time.time(),
            "last_activity": None,
            "client_encryption_required": True,
            "client_encryption_policy_confirmed_at": time.time(),
            "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
        }
        if backend == "zfs":
            source = mount.get("source", "").split("[")[0]
            if not source or source.startswith("/"):
                raise ValueError("Could not determine parent ZFS dataset")
            target["dataset"] = f"{source}/runvard-timemachine/{target_id}"

        user = _user_info(owner)
        account_created = False
        if not user and create_account:
            result = _run([
                "useradd", "--system", "--no-create-home", "--shell",
                "/usr/sbin/nologin", "--user-group", owner,
            ])
            if not result["ok"]:
                raise RuntimeError(result.get("stderr") or "Could not create backup account")
            account_created = True
            user = _user_info(owner)
        if not user:
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise ValueError("Backup account does not exist")
        if int(user.get("uid", -1)) == 0:
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise ValueError("The root account cannot own a Time Machine target")

        created = False
        config_activated = False
        try:
            _provision_storage(target)
            created = True
            _set_owner(target_path, user)
            candidate = copy.deepcopy(state)
            candidate["targets"].append(target)
            _activate_managed_config(candidate["targets"], state["targets"])
            config_activated = True
            if password:
                _set_smb_password(owner, password)
            save_state(candidate)
        except Exception:
            if config_activated:
                try:
                    _activate_managed_config(state["targets"], candidate["targets"])
                except Exception:
                    pass
            if created:
                _cleanup_empty_storage(target)
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise
        return {"ok": True, "target": copy.deepcopy(target)}


def list_targets() -> list[dict[str, Any]]:
    return copy.deepcopy(load_state()["targets"])


def _replication_index(state: dict[str, Any], replication_id: str) -> int:
    replication_id = str(replication_id or "")
    if not _ID_RE.fullmatch(replication_id):
        raise ValueError("Invalid replication id")
    for index, replication in enumerate(state["replications"]):
        if replication.get("id") == replication_id:
            return index
    raise KeyError("Time Machine replication not found")


def list_replications(target_id: str | None = None) -> list[dict[str, Any]]:
    replications = load_state()["replications"]
    if target_id is not None:
        if not _ID_RE.fullmatch(str(target_id)):
            raise ValueError("Invalid target id")
        replications = [
            replication for replication in replications
            if replication.get("target_id") == target_id
        ]
    return copy.deepcopy(replications)


def list_jobs(*, limit: int = 50) -> list[dict[str, Any]]:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("Invalid job limit") from None
    if limit < 1 or limit > 100:
        raise ValueError("Job limit must be between 1 and 100")
    jobs = [
        job for job in load_state()["jobs"]
        if job.get("type") in {"replication", "maintenance"}
    ]
    jobs.sort(
        key=lambda job: float(
            job.get("finished_at") or job.get("started_at")
            or job.get("queued_at") or 0
        ),
        reverse=True,
    )
    return copy.deepcopy(jobs[:limit])


def list_events(*, limit: int = 50) -> list[dict[str, Any]]:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("Invalid event limit") from None
    if limit < 1 or limit > 100:
        raise ValueError("Event limit must be between 1 and 100")
    events = load_state().get("events", [])
    events.sort(
        key=lambda event: float(event.get("created_at", 0)), reverse=True,
    )
    return copy.deepcopy(events[:limit])


def _append_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    item = copy.deepcopy(event)
    item.setdefault("id", secrets.token_hex(8))
    state["events"] = (state.get("events", []) + [item])[-200:]


def _cleanup_local_replica_staging(replication: dict[str, Any]) -> None:
    if replication.get("kind") != "local" or replication.get("transport") != "local-rsync":
        return
    destination = os.path.realpath(str(replication.get("destination_path") or ""))
    versions = os.path.realpath(os.path.join(destination, "versions"))
    if not destination or os.path.commonpath([versions, destination]) != destination:
        raise RuntimeError("Invalid local replica staging namespace")
    try:
        entries = list(Path(versions).iterdir())
    except FileNotFoundError:
        return
    for entry in entries:
        if not re.fullmatch(r"\.incomplete-[0-9]{1,20}-[a-f0-9]{8}", entry.name):
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        path = os.path.realpath(entry)
        if os.path.commonpath([path, versions]) != versions:
            raise RuntimeError("Replica staging path escapes managed namespace")
        shutil.rmtree(path)


def recover_interrupted_jobs(*, now: float | None = None) -> dict[str, Any]:
    """Requeue replication jobs left running by a stopped worker process."""
    timestamp = time.time() if now is None else float(now)
    with _LOCK:
        state = load_state()
        recovered: list[str] = []
        failed: list[str] = []
        changed = False
        for job in state["jobs"]:
            if job.get("type") != "replication" or job.get("status") != "running":
                continue
            replication_id = str(job.get("replication_id") or "")
            try:
                index = _replication_index(state, replication_id)
            except (KeyError, ValueError):
                job.update(status="failed", finished_at=timestamp,
                           error="Replication no longer exists")
                failed.append(str(job.get("id") or ""))
                changed = True
                continue
            replication = state["replications"][index]
            _cleanup_local_replica_staging(replication)
            recovery_count = int(job.get("recovery_count") or 0) + 1
            if recovery_count >= 3:
                error = "Interrupted replication reached the recovery limit"
                job.update(
                    status="failed", finished_at=timestamp, error=error,
                    recovery_count=recovery_count, recovered_at=timestamp,
                )
                replication.update(status="warning", last_error=error)
                failed.append(str(job.get("id") or ""))
                changed = True
                continue
            job.update(
                status="queued", queued_at=timestamp, recovered_at=timestamp,
                recovery_count=recovery_count,
            )
            job.pop("finished_at", None)
            job.pop("error", None)
            if replication.get("status") == "replicating":
                replication["status"] = "passive"
            recovered.append(str(job.get("id") or ""))
            changed = True
        if changed:
            save_state(state)
        result = {"ok": not failed, "recovered": recovered}
        if failed:
            result["failed"] = failed
        return result


def _resolved_addresses(host: str) -> list[str]:
    try:
        return sorted({
            row[4][0] for row in socket.getaddrinfo(
                host, None, type=socket.SOCK_STREAM,
            )
        })
    except socket.gaierror as exc:
        raise ValueError("Remote host could not be resolved") from exc


def _is_private_lan_or_vpn(address: str) -> bool:
    value = ipaddress.ip_address(address)
    if value.version == 4:
        networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("100.64.0.0/10"),
        )
    else:
        networks = (ipaddress.ip_network("fc00::/7"),)
    return any(value in network for network in networks)


def _validate_remote_host(host: str) -> str:
    host = str(host or "").strip().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOST_RE.fullmatch(host) or ".." in host:
            raise ValueError("Invalid remote host") from None
    addresses = _resolved_addresses(host)
    if not addresses or any(not _is_private_lan_or_vpn(value) for value in addresses):
        raise ValueError("Remote host must resolve only to a private LAN or VPN address")
    return host


def _validate_remote_root(path: str) -> str:
    path = _validate_target_path(path)
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in Path(path).parts:
        raise ValueError("Invalid remote replica root")
    return path.rstrip("/")


def _known_host_token(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _has_pinned_host(host: str, port: int) -> bool:
    token = _known_host_token(host, port)
    try:
        lines = Path(KNOWN_HOSTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(
        token in line.split(maxsplit=1)[0].split(",")
        for line in lines if line.strip() and not line.lstrip().startswith("#")
    )


def _validate_managed_identity() -> None:
    key = Path(REPLICATION_KEY)
    if not key.is_file() or key.is_symlink():
        raise RuntimeError("Managed replication identity is missing")
    if stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise RuntimeError("Managed replication identity permissions are too broad")


def ensure_replication_identity() -> dict[str, Any]:
    key = Path(REPLICATION_KEY)
    public_key = Path(f"{REPLICATION_KEY}.pub")
    key.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(key.parent, 0o700)
    if not key.exists():
        result = _run([
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
            "runvard-time-machine-replication", "-f", REPLICATION_KEY,
        ])
        if not result["ok"]:
            raise RuntimeError(result.get("stderr") or "Could not create replication identity")
    _validate_managed_identity()
    try:
        public = public_key.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Replication public key is missing") from exc
    if not public.startswith("ssh-ed25519 ") or "\n" in public:
        raise RuntimeError("Invalid replication public key")
    forced = (
        'restrict,command="/opt/runvard/venv/bin/python '
        '/opt/runvard/scripts/time-machine-receiver.py" '
        + public
    )
    return {"public_key": public, "authorized_keys_line": forced}


def register_remote_host_key(*, host: str, port: int, key_line: str,
                             expected_fingerprint: str) -> dict[str, Any]:
    host = _validate_remote_host(host)
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("Invalid SSH port") from None
    if port < 1 or port > 65535:
        raise ValueError("Invalid SSH port")
    key_line = str(key_line or "").strip()
    fields = key_line.split()
    if len(fields) < 3 or fields[1] not in {
        "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    }:
        raise ValueError("Invalid SSH host key")
    expected_token = _known_host_token(host, port)
    if expected_token not in fields[0].split(","):
        raise ValueError("SSH host key does not match the requested host")
    fingerprint = str(expected_fingerprint or "").strip()
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{20,60}", fingerprint):
        raise ValueError("Invalid SSH host-key fingerprint")
    checked = _run(["ssh-keygen", "-lf", "-"], input_text=key_line + "\n")
    if not checked["ok"] or fingerprint not in checked.get("stdout", ""):
        raise ValueError("SSH host-key fingerprint does not match")
    path = Path(KNOWN_HOSTS_FILE)
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    remaining = [
        line for line in existing
        if expected_token not in line.split(maxsplit=1)[0].split(",")
    ]
    remaining.append(key_line)
    _atomic_write(KNOWN_HOSTS_FILE, "\n".join(remaining) + "\n", 0o600)
    return {"ok": True, "host": host, "port": port, "fingerprint": fingerprint}


def register_replication_client_key(public_key: str) -> dict[str, Any]:
    public_key = str(public_key or "").strip()
    if any(char in public_key for char in ("\n", "\r", "\x00", '"', "'")):
        raise ValueError("Invalid replication public key")
    fields = public_key.split()
    if len(fields) not in {2, 3} or fields[0] != "ssh-ed25519" \
            or not re.fullmatch(r"[A-Za-z0-9+/=]{32,500}", fields[1]):
        raise ValueError("Only a valid Ed25519 replication key is accepted")
    if len(fields) == 3 and not re.fullmatch(r"[A-Za-z0-9@._-]{1,80}", fields[2]):
        raise ValueError("Invalid replication key comment")
    forced = (
        'restrict,command="/opt/runvard/venv/bin/python '
        '/opt/runvard/scripts/time-machine-receiver.py" '
        + public_key
    )
    path = Path(RECEIVER_AUTHORIZED_KEYS)
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    existing = [line for line in existing if fields[1] not in line]
    existing.append(forced)
    _atomic_write(RECEIVER_AUTHORIZED_KEYS, "\n".join(existing) + "\n", 0o600)
    return {"ok": True, "key_type": "ssh-ed25519"}


def create_replication(*, target_id: str, destination_root: str,
                       schedule_hour: int = 2, bandwidth_mbps: int = 0,
                       remote_host: str = "", remote_user: str = "",
                       remote_port: int = 22) -> dict[str, Any]:
    try:
        schedule_hour = int(schedule_hour)
        bandwidth_mbps = int(bandwidth_mbps or 0)
        remote_port = int(remote_port)
    except (TypeError, ValueError):
        raise ValueError("Invalid replication settings") from None
    if schedule_hour < 0 or schedule_hour > 23:
        raise ValueError("Schedule hour must be between 0 and 23")
    if bandwidth_mbps < 0 or bandwidth_mbps > 100_000:
        raise ValueError("Invalid bandwidth limit")
    with _LOCK:
        state = load_state()
        target = state["targets"][_target_index(state, target_id)]
        replication_id = secrets.token_hex(8)
        if remote_host:
            host = _validate_remote_host(remote_host)
            user = _validate_account(remote_user)
            if remote_port < 1 or remote_port > 65535:
                raise ValueError("Invalid SSH port")
            root = _validate_remote_root(destination_root)
            if not _has_pinned_host(host, remote_port):
                raise ValueError("Remote SSH host key is not pinned")
            _validate_managed_identity()
            destination_path = (
                f"{root}/runvard-time-machine-replicas/{replication_id}"
            )
            replication = {
                "id": replication_id,
                "target_id": target_id,
                "kind": "remote",
                "transport": "remote-rsync",
                "remote_host": host,
                "remote_user": user,
                "remote_port": remote_port,
                "destination_root": root,
                "destination_path": destination_path,
            }
        else:
            root, mount = _validate_storage_root(destination_root)
            if root == target.get("storage_root") or mount.get("source") == target.get("mount_source"):
                raise ValueError("Replication destination must be a different storage pool")
            destination_path = os.path.realpath(os.path.join(
                root, "runvard-time-machine-replicas", replication_id,
            ))
            base = os.path.realpath(os.path.join(root, "runvard-time-machine-replicas"))
            if os.path.commonpath([destination_path, base]) != base:
                raise ValueError("Replication destination escapes storage root")
            source_backend = target.get("backend")
            destination_backend = _backend_for_mount(mount)
            transport = "local-rsync"
            if source_backend == destination_backend == "zfs":
                transport = "local-zfs"
            elif source_backend == destination_backend == "btrfs":
                transport = "local-btrfs"
            replication = {
                "id": replication_id,
                "target_id": target_id,
                "kind": "local",
                "transport": transport,
                "destination_root": root,
                "destination_mount_source": mount.get("source", ""),
                "destination_filesystem": mount.get("fstype", ""),
                "destination_path": destination_path,
            }
        replication.update({
            "enabled": True,
            "status": "passive",
            "schedule_hour": schedule_hour,
            "bandwidth_mbps": bandwidth_mbps,
            "retention_versions": 2,
            "created_at": time.time(),
            "last_attempt_at": None,
            "last_complete_at": None,
            "last_complete_path": None,
            "last_error": None,
        })
        candidate = copy.deepcopy(state)
        candidate["replications"].append(replication)
        save_state(candidate)
        return {"ok": True, "replication": copy.deepcopy(replication)}


def update_replication_policy(replication_id: str, *, schedule_hour: int,
                              bandwidth_mbps: int, enabled: bool,
                              actor: str = "system",
                              now: float | None = None) -> dict[str, Any]:
    try:
        schedule_hour = int(schedule_hour)
        bandwidth_mbps = int(bandwidth_mbps)
    except (TypeError, ValueError):
        raise ValueError("Invalid replication settings") from None
    if schedule_hour < 0 or schedule_hour > 23:
        raise ValueError("Schedule hour must be between 0 and 23")
    if bandwidth_mbps < 0 or bandwidth_mbps > 100_000:
        raise ValueError("Invalid bandwidth limit")
    enabled = bool(enabled)
    timestamp = time.time() if now is None else float(now)

    with _LOCK:
        state = load_state()
        index = _replication_index(state, replication_id)
        current = state["replications"][index]
        if enabled and current.get("status") == "promoted":
            raise ValueError("A promoted replication cannot be re-enabled")
        candidate = copy.deepcopy(state)
        replication = candidate["replications"][index]
        replication.update(
            schedule_hour=schedule_hour,
            bandwidth_mbps=bandwidth_mbps,
            enabled=enabled,
            updated_at=timestamp,
        )
        if not enabled:
            replication["status"] = "paused"
            for job in candidate["jobs"]:
                if job.get("type") == "replication" \
                        and job.get("replication_id") == replication_id \
                        and job.get("status") == "queued":
                    job["status"] = "cancelled"
                    job["finished_at"] = timestamp
        elif current.get("status") == "paused":
            replication["status"] = "passive"
        _append_event(candidate, {
            "type": "replication_policy_updated",
            "replication_id": replication_id,
            "target_id": replication.get("target_id"),
            "actor": str(actor or "system")[:80],
            "created_at": timestamp,
            "changes": {
                "schedule_hour": {
                    "from": current.get("schedule_hour"), "to": schedule_hour,
                },
                "bandwidth_mbps": {
                    "from": current.get("bandwidth_mbps", 0), "to": bandwidth_mbps,
                },
                "enabled": {
                    "from": bool(current.get("enabled", True)), "to": enabled,
                },
            },
        })
        save_state(candidate)
        return {"ok": True, "replication": copy.deepcopy(replication)}


def queue_replication(replication_id: str) -> dict[str, Any]:
    with _LOCK:
        state = load_state()
        replication = state["replications"][_replication_index(state, replication_id)]
        if not replication.get("enabled", True) or replication.get("status") == "promoted":
            raise ValueError("Replication is not active")
        existing = next((
            job for job in state["jobs"]
            if job.get("type") == "replication"
            and job.get("replication_id") == replication_id
            and job.get("status") in {"queued", "running"}
        ), None)
        if existing:
            return {"ok": True, "job": copy.deepcopy(existing), "already_queued": True}
        job = {
            "id": secrets.token_hex(8), "type": "replication",
            "replication_id": replication_id, "status": "queued",
            "queued_at": time.time(),
        }
        candidate = copy.deepcopy(state)
        candidate["jobs"] = (candidate["jobs"] + [job])[-100:]
        save_state(candidate)
        return {"ok": True, "job": copy.deepcopy(job), "already_queued": False}


def schedule_due_replications(*, now: float | None = None) -> dict[str, Any]:
    timestamp = time.time() if now is None else float(now)
    local = time.localtime(timestamp)
    day_key = local.tm_year * 10_000 + local.tm_mon * 100 + local.tm_mday
    queued = []
    for replication in list_replications():
        if not replication.get("enabled", True) or replication.get("status") == "promoted":
            continue
        if int(replication.get("schedule_hour", 2)) != local.tm_hour:
            continue
        if int(replication.get("last_scheduled_day") or 0) == day_key:
            continue
        result = queue_replication(replication["id"])
        if not result.get("already_queued"):
            queued.append(replication["id"])
        with _LOCK:
            state = load_state()
            index = _replication_index(state, replication["id"])
            state["replications"][index]["last_scheduled_day"] = day_key
            save_state(state)
    return {"ok": True, "queued": queued, "day": day_key}


def _alert_sink(message: str) -> dict[str, Any]:
    from modules import monitoring

    config = monitoring.list_alert_rules()
    channels = config.get("channels", {})
    channel = "webhook" if channels.get("webhook") else (
        "email" if channels.get("email") else "in-app"
    )
    return monitoring.trigger_alert(message, channel=channel)


def _emit_deduplicated_alert(key: str, message: str, *, now: float,
                             cooldown: int = 21_600) -> bool:
    with _LOCK:
        state = load_state()
        last = float(state.get("alert_cooldowns", {}).get(key, 0))
        if last and now - last < cooldown:
            return False
    result = _alert_sink(message)
    if not result.get("ok", False):
        return False
    with _LOCK:
        state = load_state()
        state.setdefault("alert_cooldowns", {})[key] = now
        save_state(state)
    return True


def emit_health_alerts(*, now: float | None = None) -> dict[str, Any]:
    timestamp = time.time() if now is None else float(now)
    health = health_check()
    emitted = []
    for target in health["targets"]:
        if target.get("status") == "critical":
            code = str(target.get("health_code") or "critical")
            key = f"target:{target.get('id')}:{code}"
            message = (
                f"Time Machine target {target.get('display_name') or target.get('id')} "
                f"is critical: {code}"
            )
            if _emit_deduplicated_alert(key, message, now=timestamp):
                emitted.append(key)
        total = int(target.get("filesystem_total_bytes") or 0)
        free = int(target.get("filesystem_free_bytes") or 0)
        if total:
            used_percent = (total - free) / total * 100
            if used_percent >= 85:
                level = "critical" if used_percent >= 95 else "warning"
                key = f"pool:{target.get('storage_root')}:{level}"
                message = (
                    f"Time Machine storage pool for {target.get('display_name')} is "
                    f"{used_percent:.1f}% full ({level})"
                )
                if _emit_deduplicated_alert(key, message, now=timestamp):
                    emitted.append(key)
    for replication in list_replications():
        if replication.get("status") == "warning":
            key = f"replication:{replication['id']}:warning"
            message = (
                f"Time Machine replication {replication['id']} failed: "
                f"{replication.get('last_error') or 'unknown error'}"
            )
            if _emit_deduplicated_alert(key, message, now=timestamp):
                emitted.append(key)
        last_complete = float(replication.get("last_complete_at") or 0)
        created = float(replication.get("created_at") or timestamp)
        if replication.get("enabled", True) and timestamp - (last_complete or created) > 129_600:
            key = f"replication:{replication['id']}:overdue"
            message = f"Time Machine replication {replication['id']} is overdue"
            if _emit_deduplicated_alert(key, message, now=timestamp):
                emitted.append(key)
    return {"ok": True, "emitted": emitted}


def _claim_replication(replication: dict[str, Any], target: dict[str, Any]) -> tuple[str, str]:
    source_key = str(target.get("mount_source") or target.get("storage_root"))
    destination_key = str(
        replication.get("remote_host") or replication.get("destination_mount_source")
        or replication.get("destination_root")
    )
    key = (source_key, destination_key)
    with _REPLICATION_LOCK:
        if any(source_key in active or destination_key in active for active in _ACTIVE_REPLICATION_KEYS):
            raise RuntimeError("Another replication is active for this source or destination")
        _ACTIVE_REPLICATION_KEYS.add(key)
    return key


def _release_replication(key: tuple[str, str]) -> None:
    with _REPLICATION_LOCK:
        _ACTIVE_REPLICATION_KEYS.discard(key)


def _update_replication(replication_id: str, values: dict[str, Any],
                        job: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        state = load_state()
        index = _replication_index(state, replication_id)
        state["replications"][index].update(values)
        job_index = next(
            (i for i, item in enumerate(state["jobs"]) if item.get("id") == job["id"]),
            None,
        )
        if job_index is None:
            state["jobs"].append(job)
        else:
            state["jobs"][job_index] = job
        state["jobs"] = state["jobs"][-100:]
        save_state(state)
        return copy.deepcopy(state["replications"][index])


def _remote_shell(replication: dict[str, Any]) -> str:
    return " ".join([
        "ssh", "-T", "-o", "BatchMode=yes", "-o",
        f"IdentityFile={REPLICATION_KEY}", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_FILE}", "-p",
        str(replication["remote_port"]),
    ])


def _remote_ssh_argv(replication: dict[str, Any]) -> list[str]:
    host = _validate_remote_host(replication["remote_host"])
    remote_host = f"[{host}]" if ":" in host else host
    return [
        "ssh", "-T", "-o", "BatchMode=yes", "-o",
        f"IdentityFile={REPLICATION_KEY}", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_FILE}", "-p",
        str(replication["remote_port"]),
        f"{replication['remote_user']}@{remote_host}",
    ]


def _remote_capacity(replication: dict[str, Any]) -> dict[str, int]:
    host = _validate_remote_host(replication["remote_host"])
    _validate_managed_identity()
    if not _has_pinned_host(host, int(replication["remote_port"])):
        raise RuntimeError("Remote SSH host key is not pinned")
    result = _run([
        *_remote_ssh_argv(replication),
        "runvard-tm-capacity", replication["destination_root"],
    ], timeout=30)
    if not result.get("ok"):
        raise RuntimeError(result.get("stderr") or "Remote capacity check failed")
    try:
        payload = json.loads(result.get("stdout", ""))
        capacity = {
            key: int(payload[key]) for key in ("total", "used", "free")
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Remote capacity response is invalid") from exc
    if capacity["total"] <= 0 or min(capacity.values()) < 0:
        raise RuntimeError("Remote capacity response is invalid")
    return capacity


def run_replication(replication_id: str, *, now: float | None = None,
                    job_id: str | None = None) -> dict[str, Any]:
    timestamp = int(time.time() if now is None else now)
    state = load_state()
    replication = state["replications"][_replication_index(state, replication_id)]
    target = state["targets"][_target_index(state, replication["target_id"])]
    if not replication.get("enabled", True) or replication.get("status") == "promoted":
        raise ValueError("Replication is not active")
    _assert_target_mount(target)
    source_usage = shutil.disk_usage(target["path"])
    if not source_usage.total or source_usage.used / source_usage.total >= 0.95:
        return {"ok": False, "deferred": True, "reason": "pool_full"}
    if _target_has_open_handles(target):
        return {"ok": False, "deferred": True, "reason": "target_busy"}
    if replication["kind"] == "local":
        destination_usage = shutil.disk_usage(replication["destination_root"])
        if (
            not destination_usage.total
            or destination_usage.used / destination_usage.total >= 0.95
        ):
            return {"ok": False, "deferred": True, "reason": "pool_full"}
    else:
        remote_usage = _remote_capacity(replication)
        if remote_usage["used"] / remote_usage["total"] >= 0.95:
            return {"ok": False, "deferred": True, "reason": "remote_pool_full"}
    claim = _claim_replication(replication, target)
    if job_id is not None:
        if not _ID_RE.fullmatch(str(job_id)):
            raise ValueError("Invalid job id")
        queued_job = next((
            item for item in state["jobs"] if item.get("id") == job_id
            and item.get("type") == "replication"
            and item.get("replication_id") == replication_id
        ), None)
        if not queued_job:
            raise KeyError("Queued replication job not found")
        job = copy.deepcopy(queued_job)
        job.update(started_at=timestamp, status="running")
    else:
        job = {
            "id": secrets.token_hex(8), "type": "replication",
            "replication_id": replication_id, "started_at": timestamp,
            "status": "running",
        }
    _update_replication(
        replication_id,
        {"status": "replicating", "last_attempt_at": timestamp, "last_error": None},
        job,
    )
    staging = ""
    completion_values: dict[str, Any] = {}
    try:
        if replication["kind"] == "local":
            info = _mount_info(replication["destination_root"])
            if not info or os.path.realpath(info.get("mountpoint", "")) != os.path.realpath(
                replication["destination_root"]
            ):
                raise RuntimeError("Replication destination mount is missing")
            if info.get("source") != replication.get("destination_mount_source"):
                raise RuntimeError("Replication destination mount identity changed")
            points = sorted(
                (
                    point for point in state["protection_points"]
                    if point.get("target_id") == target["id"]
                    and point.get("backend") == target.get("backend")
                ),
                key=lambda point: point.get("created_at", 0),
            )
            latest = points[-1] if points else None
            if replication["transport"] == "local-zfs":
                if not latest:
                    raise RuntimeError("No ZFS storage protection point is available")
                source_snapshot = str(latest["native_name"])
                destination_parent = (
                    f"{info['source'].split('[', 1)[0]}/runvard-replicas"
                )
                destination_dataset = f"{destination_parent}/{replication_id}"
                listed = _run([
                    "zfs", "list", "-H", "-o", "name", destination_parent,
                ])
                if not listed["ok"]:
                    created = _run(["zfs", "create", "-p", destination_parent])
                    if not created["ok"]:
                        raise RuntimeError(
                            created.get("stderr") or "Could not create replica dataset parent"
                        )
                send = ["zfs", "send"]
                if replication.get("last_source_point"):
                    send.extend(["-i", str(replication["last_source_point"])])
                send.append(source_snapshot)
                result = _run_pipe(
                    send, ["zfs", "receive", "-u", destination_dataset],
                )
                if not result["ok"]:
                    raise RuntimeError(result.get("stderr") or "ZFS replication failed")
                for property_value in ("readonly=on", "mountpoint=none"):
                    changed = _run([
                        "zfs", "set", property_value, destination_dataset,
                    ])
                    if not changed["ok"]:
                        raise RuntimeError(
                            changed.get("stderr") or "Could not make ZFS replica passive"
                        )
                snapshot_suffix = source_snapshot.split("@", 1)[1]
                final_path = f"{destination_dataset}@{snapshot_suffix}"
                completion_values.update({
                    "last_source_point": source_snapshot,
                    "replica_dataset": destination_dataset,
                    "replica_native_name": final_path,
                })
            elif replication["transport"] == "local-btrfs":
                if not latest:
                    raise RuntimeError("No Btrfs storage protection point is available")
                source_snapshot = os.path.realpath(str(latest["native_name"]))
                if not os.path.isdir(source_snapshot):
                    raise RuntimeError("Btrfs storage protection point is missing")
                receive_root = os.path.join(replication["destination_path"], "snapshots")
                os.makedirs(receive_root, mode=0o700, exist_ok=True)
                send = ["btrfs", "send"]
                if replication.get("last_source_point"):
                    send.extend(["-p", str(replication["last_source_point"])])
                send.append(source_snapshot)
                result = _run_pipe(send, ["btrfs", "receive", receive_root])
                if not result["ok"]:
                    raise RuntimeError(result.get("stderr") or "Btrfs replication failed")
                final_path = os.path.join(receive_root, os.path.basename(source_snapshot))
                completion_values.update({
                    "last_source_point": source_snapshot,
                    "replica_native_name": final_path,
                })
            else:
                versions = os.path.join(replication["destination_path"], "versions")
                os.makedirs(versions, mode=0o700, exist_ok=True)
                final_path = os.path.join(versions, str(timestamp))
                staging = final_path + f".incomplete-{secrets.token_hex(4)}"
                os.makedirs(staging, mode=0o700, exist_ok=False)
                command = [
                    "rsync", "-aH", "--numeric-ids", "--delete-delay", "--partial",
                ]
                if replication.get("bandwidth_mbps"):
                    command.append(
                        f"--bwlimit={int(replication['bandwidth_mbps']) * 125}"
                    )
                command.extend([target["path"].rstrip("/") + "/", staging + "/"])
                result = _run(command, timeout=86_400)
                if not result["ok"]:
                    raise RuntimeError(result.get("stderr") or "Replication transfer failed")
                os.replace(staging, final_path)
                _prune_local_replica_versions(replication, keep_current=final_path)
        else:
            host = _validate_remote_host(replication["remote_host"])
            _validate_managed_identity()
            if not _has_pinned_host(host, int(replication["remote_port"])):
                raise RuntimeError("Remote SSH host key is not pinned")
            cleaned = _run([
                *_remote_ssh_argv(replication),
                "runvard-tm-clean-staging", replication_id,
                str(max(0, timestamp - 86_400)),
            ], timeout=30)
            if not cleaned.get("ok"):
                raise RuntimeError(
                    cleaned.get("stderr") or "Could not clean remote replica staging"
                )
            final_path = f"{replication['destination_path']}/versions/{timestamp}"
            remote_staging = (
                f"{replication['destination_path']}/versions/"
                f".incomplete-{timestamp}-{secrets.token_hex(4)}"
            )
            remote_host = f"[{host}]" if ":" in host else host
            command = [
                "rsync", "-aH", "--numeric-ids", "--delete-delay", "--partial",
                "--protect-args", "-e", _remote_shell(replication),
            ]
            if replication.get("bandwidth_mbps"):
                command.append(
                    f"--bwlimit={int(replication['bandwidth_mbps']) * 125}"
                )
            command.extend([
                target["path"].rstrip("/") + "/",
                f"{replication['remote_user']}@{remote_host}:{remote_staging}/",
            ])
            result = _run(command, timeout=86_400)
            if not result["ok"]:
                raise RuntimeError(result.get("stderr") or "Remote replication failed")
            committed = _run([
                *_remote_ssh_argv(replication),
                "runvard-tm-commit", remote_staging, final_path,
            ], timeout=30)
            if not committed.get("ok"):
                raise RuntimeError(
                    committed.get("stderr") or "Could not commit remote replica"
                )
        job.update(status="completed", finished_at=time.time())
        completed = _update_replication(
            replication_id,
            {
                "status": "passive", "last_complete_at": timestamp,
                "last_complete_path": final_path, "last_error": None,
                **completion_values,
            },
            job,
        )
        return {"ok": True, "replication": completed, "job": job}
    except Exception as exc:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging)
        job.update(status="failed", finished_at=time.time(), error=str(exc))
        _update_replication(
            replication_id,
            {"status": "warning", "last_error": str(exc)},
            job,
        )
        raise
    finally:
        _release_replication(claim)


def _prune_local_replica_versions(replication: dict[str, Any], *, keep_current: str) -> None:
    """Keep a small rollback window, confined to the managed replica namespace."""
    if replication.get("kind") != "local" or replication.get("transport") != "local-rsync":
        return
    versions = os.path.realpath(os.path.join(replication["destination_path"], "versions"))
    destination = os.path.realpath(replication["destination_path"])
    current = os.path.realpath(keep_current)
    if os.path.commonpath([versions, destination]) != destination:
        raise RuntimeError("Invalid replica versions path")
    if os.path.commonpath([current, versions]) != versions:
        raise RuntimeError("Current replica version escapes managed namespace")
    try:
        entries = [
            item for item in Path(versions).iterdir()
            if item.is_dir() and item.name.isdigit()
        ]
    except FileNotFoundError:
        return
    keep = max(2, min(int(replication.get("retention_versions", 2)), 30))
    entries.sort(key=lambda item: int(item.name))
    for item in entries[:-keep]:
        path = os.path.realpath(item)
        if path == versions or os.path.commonpath([path, versions]) != versions:
            raise RuntimeError("Replica version escapes managed namespace")
        shutil.rmtree(path)


def process_replication_queue(*, limit: int = 1,
                              now: float | None = None) -> dict[str, Any]:
    try:
        limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        raise ValueError("Invalid queue limit") from None
    queued = sorted(
        (
            job for job in load_state()["jobs"]
            if job.get("type") == "replication" and job.get("status") == "queued"
        ),
        key=lambda job: job.get("queued_at", 0),
    )[:limit]
    processed = 0
    deferred = 0
    errors = []
    for job in queued:
        try:
            result = run_replication(
                job["replication_id"], now=now, job_id=job["id"],
            )
            if result.get("deferred"):
                deferred += 1
            else:
                processed += 1
        except Exception as exc:
            processed += 1
            errors.append({"job_id": job.get("id"), "error": str(exc)})
    return {
        "ok": not errors, "processed": processed,
        "deferred": deferred, "errors": errors,
    }


def promote_replica(replication_id: str, *, source_unavailable_confirmed: bool,
                    now: float | None = None) -> dict[str, Any]:
    if not source_unavailable_confirmed:
        raise ValueError("Source unavailability must be explicitly confirmed")
    with _LOCK:
        state = load_state()
        replication_index = _replication_index(state, replication_id)
        replication = state["replications"][replication_index]
        if replication.get("kind") != "local":
            raise ValueError("Remote replicas must be promoted on the destination runvard")
        target_index = _target_index(state, replication["target_id"])
        target = state["targets"][target_index]
        if target.get("enabled", True) or target.get("status") not in {"paused", "removed"}:
            raise ValueError("Source target must be paused or removed before promotion")
        info = _mount_info(replication["destination_root"])
        if not info or info.get("source") != replication.get("destination_mount_source"):
            raise RuntimeError("Replication destination mount identity changed")
        promoted_path = os.path.realpath(os.path.join(
            replication["destination_root"], "runvard-time-machine",
            f"promoted-{target['id']}",
        ))
        promoted_base = os.path.realpath(os.path.join(
            replication["destination_root"], "runvard-time-machine",
        ))
        if os.path.commonpath([promoted_path, promoted_base]) != promoted_base:
            raise RuntimeError("Promotion path escapes destination storage")
        if os.path.exists(promoted_path):
            raise RuntimeError("Promotion path already exists")
        os.makedirs(promoted_base, mode=0o700, exist_ok=True)
        transport = replication.get("transport")
        source_real = ""
        moved_replica = False
        if transport == "local-zfs":
            dataset = str(replication.get("replica_dataset") or "")
            expected_parent = f"{info['source'].split('[', 1)[0]}/runvard-replicas/"
            if not dataset.startswith(expected_parent) or dataset != \
                    f"{expected_parent}{replication_id}":
                raise RuntimeError("Passive ZFS replica dataset is invalid")
            for property_value in (
                "readonly=off", f"mountpoint={promoted_path}",
                f"refquota={int(target['hard_limit_bytes'])}",
            ):
                changed = _run(["zfs", "set", property_value, dataset])
                if not changed["ok"]:
                    _run(["zfs", "set", "readonly=on", dataset])
                    _run(["zfs", "set", "mountpoint=none", dataset])
                    raise RuntimeError(
                        changed.get("stderr") or "Could not promote ZFS replica"
                    )
        else:
            source_path = str(replication.get("last_complete_path") or "")
            namespace = "snapshots" if transport == "local-btrfs" else "versions"
            source_root = os.path.realpath(os.path.join(
                replication["destination_path"], namespace,
            ))
            if not source_path or os.path.islink(source_path):
                raise RuntimeError("No complete passive replica is available")
            source_real = os.path.realpath(source_path)
            if os.path.commonpath([source_real, source_root]) != source_root \
                    or not os.path.isdir(source_real):
                raise RuntimeError("Passive replica path is invalid or missing")
            os.replace(source_real, promoted_path)
            moved_replica = True
            if transport == "local-btrfs":
                writable = _run([
                    "btrfs", "property", "set", "-ts", promoted_path, "ro", "false",
                ])
                quota = _run([
                    "btrfs", "qgroup", "limit",
                    str(int(target["hard_limit_bytes"])), promoted_path,
                ])
                if not writable["ok"] or not quota["ok"]:
                    os.replace(promoted_path, source_real)
                    restored = _run([
                        "btrfs", "property", "set", "-ts",
                        source_real, "ro", "true",
                    ])
                    if not restored["ok"]:
                        raise RuntimeError(
                            (writable.get("stderr") or quota.get("stderr")
                             or "Could not promote Btrfs replica")
                            + "; read-only rollback failed: "
                            + (restored.get("stderr") or "unknown error")
                        )
                    raise RuntimeError(
                        writable.get("stderr") or quota.get("stderr")
                        or "Could not promote Btrfs replica"
                    )
        candidate = copy.deepcopy(state)
        promoted_target = candidate["targets"][target_index]
        promoted_target.update({
            "storage_root": replication["destination_root"],
            "mount_source": replication["destination_mount_source"],
            "filesystem": replication.get("destination_filesystem", ""),
            "path": promoted_path,
            "backend": "zfs" if transport == "local-zfs" else (
                "btrfs" if transport == "local-btrfs" else "directory"
            ),
            "quota_mode": "hard" if transport in {"local-zfs", "local-btrfs"}
            else "reported",
            "enabled": True,
            "status": "waiting",
            "health_code": "verification_required",
            "verification_required": True,
            "promoted_from_replication": replication_id,
            "promoted_at": time.time() if now is None else float(now),
            "previous_source": {
                "storage_root": target.get("storage_root"),
                "mount_source": target.get("mount_source"),
                "path": target.get("path"),
            },
        })
        if transport == "local-zfs":
            promoted_target["dataset"] = replication["replica_dataset"]
        else:
            promoted_target.pop("dataset", None)
        candidate["replications"][replication_index].update({
            "status": "promoted", "enabled": False,
            "promoted_at": promoted_target["promoted_at"],
            "last_complete_path": promoted_path,
        })
        try:
            owner = _user_info(target["owner"])
            if not owner:
                raise RuntimeError("Backup account is missing on promotion host")
            _set_owner(promoted_path, owner)
            _activate_managed_config(candidate["targets"], state["targets"])
            save_state(candidate)
        except Exception:
            try:
                _activate_managed_config(state["targets"], candidate["targets"])
            except Exception:
                pass
            if transport == "local-zfs":
                _run(["zfs", "set", "readonly=on", replication["replica_dataset"]])
                _run(["zfs", "set", "mountpoint=none", replication["replica_dataset"]])
            elif moved_replica and os.path.exists(promoted_path):
                os.replace(promoted_path, source_real)
            raise
        return {
            "ok": True,
            "target": copy.deepcopy(promoted_target),
            "replication": copy.deepcopy(candidate["replications"][replication_index]),
        }


def promote_received_replica(*, source_replication_id: str, version: str,
                             display_name: str, owner: str, storage_root: str,
                             capacity_gb: int, password: str,
                             create_account: bool = False,
                             source_unavailable_confirmed: bool,
                             client_encryption_required: bool = False,
                             now: float | None = None) -> dict[str, Any]:
    if client_encryption_required is not True:
        raise ValueError("client-side encryption policy must be explicitly confirmed")
    if not source_unavailable_confirmed:
        raise ValueError("Source unavailability must be explicitly confirmed")
    if not _ID_RE.fullmatch(str(source_replication_id or "")):
        raise ValueError("Invalid source replication id")
    version = str(version or "")
    if not re.fullmatch(r"[0-9]{1,20}", version):
        raise ValueError("Invalid replica version")
    display_name = _clean_text(display_name, label="display name")
    owner = _validate_account(owner)
    try:
        capacity_gb = int(capacity_gb)
    except (TypeError, ValueError):
        raise ValueError("Invalid capacity") from None
    if capacity_gb < 10 or capacity_gb > 10_000_000:
        raise ValueError("Capacity must be between 10 GB and 10 PB")
    with _LOCK:
        root, mount = _validate_storage_root(storage_root)
        state = load_state()
        if len(state["targets"]) >= 20:
            raise ValueError("A maximum of 20 Time Machine targets is supported")
        source_base = os.path.realpath(os.path.join(
            root, "runvard-time-machine-replicas", source_replication_id,
            "versions",
        ))
        source_path = os.path.realpath(os.path.join(source_base, version))
        if os.path.commonpath([source_path, source_base]) != source_base \
                or not os.path.isdir(source_path) or os.path.islink(source_path):
            raise RuntimeError("Complete received replica is missing")
        if not os.path.isfile(os.path.join(source_path, ".runvard-complete")):
            raise RuntimeError("Received replica has no completion marker")
        account_created = False
        user = _user_info(owner)
        if not user and create_account:
            created_user = _run([
                "useradd", "--system", "--no-create-home", "--shell",
                "/usr/sbin/nologin", "--user-group", owner,
            ])
            if not created_user["ok"]:
                raise RuntimeError(
                    created_user.get("stderr") or "Could not create backup account"
                )
            account_created = True
            user = _user_info(owner)
        if not user:
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise ValueError("Backup account does not exist")
        if int(user.get("uid", -1)) == 0:
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise ValueError("The root account cannot own a Time Machine target")
        target_id = secrets.token_hex(8)
        slug = _slug(display_name)
        share_name = f"tm-{slug}-{target_id[:8]}"[:80]
        target_base = os.path.realpath(os.path.join(root, "runvard-time-machine"))
        target_path = os.path.realpath(os.path.join(
            target_base, f"promoted-{slug}-{target_id[:8]}",
        ))
        if os.path.commonpath([target_path, target_base]) != target_base \
                or os.path.exists(target_path):
            raise RuntimeError("Invalid or existing promotion path")
        try:
            os.makedirs(target_base, mode=0o700, exist_ok=True)
            os.replace(source_path, target_path)
        except Exception:
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise
        hard_limit = capacity_gb * GIB
        target = {
            "id": target_id,
            "display_name": display_name,
            "share_name": share_name,
            "owner": owner,
            "storage_root": root,
            "mount_source": mount.get("source", ""),
            "filesystem": mount.get("fstype", ""),
            "path": target_path,
            "backend": "directory",
            "quota_mode": "reported",
            "hard_limit_bytes": hard_limit,
            "advertised_bytes": max(1, int(capacity_gb * 0.95)) * GIB,
            "source_capacity_bytes": 0,
            "enabled": True,
            "status": "waiting",
            "health_code": "verification_required",
            "verification_required": True,
            "created_at": time.time() if now is None else float(now),
            "promoted_at": time.time() if now is None else float(now),
            "last_activity": None,
            "client_encryption_required": True,
            "client_encryption_policy_confirmed_at": (
                time.time() if now is None else float(now)
            ),
            "protection_policy": {"daily": 7, "weekly": 4, "monthly": 3},
            "remote_replica_source": {
                "replication_id": source_replication_id, "version": version,
            },
        }
        candidate = copy.deepcopy(state)
        candidate["targets"].append(target)
        config_activated = False
        try:
            _set_owner(target_path, user)
            _set_smb_password(owner, password)
            _activate_managed_config(candidate["targets"], state["targets"])
            config_activated = True
            save_state(candidate)
        except Exception:
            if config_activated:
                try:
                    _activate_managed_config(state["targets"], candidate["targets"])
                except Exception:
                    pass
            if os.path.exists(target_path) and not os.path.exists(source_path):
                os.replace(target_path, source_path)
            if account_created:
                _run(["smbpasswd", "-x", owner])
                _run(["userdel", owner])
            raise
        return {"ok": True, "target": copy.deepcopy(target)}


def _target_index(state: dict[str, Any], target_id: str) -> int:
    target_id = str(target_id or "")
    if not _ID_RE.fullmatch(target_id):
        raise ValueError("Invalid target id")
    for index, target in enumerate(state["targets"]):
        if target.get("id") == target_id:
            return index
    raise KeyError("Time Machine target not found")


def _assert_target_mount(target: dict[str, Any]) -> None:
    root = target.get("storage_root", "")
    info = _mount_info(root) if root else None
    if not info or os.path.realpath(info.get("mountpoint", "")) != os.path.realpath(root):
        raise RuntimeError("Storage mount is missing")
    expected_source = target.get("mount_source", "")
    if expected_source and info.get("source") != expected_source:
        raise RuntimeError("Storage mount identity changed")


def set_target_enabled(target_id: str, enabled: bool) -> dict[str, Any]:
    with _LOCK:
        state = load_state()
        index = _target_index(state, target_id)
        current = state["targets"][index]
        if current.get("status") == "removed":
            raise ValueError("Removed target cannot be enabled or paused")
        if enabled:
            _assert_target_mount(current)
            if not os.path.isdir(current.get("path", "")):
                raise RuntimeError("Target path is missing")
        candidate = copy.deepcopy(state)
        target = candidate["targets"][index]
        target["enabled"] = bool(enabled)
        target["status"] = "waiting" if enabled else "paused"
        target["health_code"] = "waiting_for_first_backup" if enabled else "paused"
        _activate_managed_config(candidate["targets"], state["targets"])
        try:
            save_state(candidate)
        except Exception:
            _activate_managed_config(state["targets"], candidate["targets"])
            raise
        return {"ok": True, "target": copy.deepcopy(target)}


def _set_target_quota(target: dict[str, Any], limit_bytes: int) -> None:
    backend = target.get("backend")
    if backend == "zfs":
        dataset = str(target.get("dataset", ""))
        if not dataset:
            raise RuntimeError("ZFS dataset is missing")
        result = _run(["zfs", "set", f"refquota={limit_bytes}", dataset])
    elif backend == "btrfs":
        path = str(target.get("path", ""))
        if not path:
            raise RuntimeError("Btrfs target path is missing")
        result = _run(["btrfs", "qgroup", "limit", str(limit_bytes), path])
    else:
        return
    if not result["ok"]:
        raise RuntimeError(result.get("stderr") or "Could not update target quota")


def _target_allocated_bytes(target: dict[str, Any]) -> int:
    result = _run([
        "du", "-s", "-B1", "--one-file-system", "--", str(target.get("path", "")),
    ], timeout=300)
    if not result["ok"]:
        detail = str(result.get("stderr") or "").strip()
        raise RuntimeError(
            "Could not measure target usage" + (f": {detail}" if detail else "")
        )
    try:
        value = int(str(result.get("stdout", "")).split(None, 1)[0])
    except (IndexError, TypeError, ValueError):
        raise RuntimeError("Could not measure target usage") from None
    if value < 0:
        raise RuntimeError("Could not measure target usage")
    return value


def quarantine_unavailable_targets(*, now: float | None = None) -> dict[str, Any]:
    """Fail closed by removing unavailable targets from Samba and Bonjour."""
    timestamp = time.time() if now is None else float(now)
    with _LOCK:
        state = load_state()
        candidate = copy.deepcopy(state)
        quarantined = []
        for index, current in enumerate(state["targets"]):
            if not current.get("enabled", True) or current.get("status") == "removed":
                continue
            root = str(current.get("storage_root", ""))
            info = _mount_info(root) if root else None
            if not info or os.path.realpath(info.get("mountpoint", "")) != os.path.realpath(root):
                code = "mount_missing"
            elif current.get("mount_source") and info.get("source") != current.get("mount_source"):
                code = "mount_identity_changed"
            elif not os.path.isdir(str(current.get("path", ""))):
                code = "target_path_missing"
            else:
                continue
            target = candidate["targets"][index]
            target.update(
                enabled=False, status="critical", health_code=code,
                quarantined_at=timestamp,
            )
            item = {"target_id": target["id"], "health_code": code}
            quarantined.append(item)
            _append_event(candidate, {
                "type": "target_quarantined", "target_id": target["id"],
                "actor": "system", "created_at": timestamp, "health_code": code,
            })
        if not quarantined:
            return {"ok": True, "quarantined": []}
        _activate_managed_config(candidate["targets"], state["targets"])
        try:
            save_state(candidate)
        except Exception:
            _activate_managed_config(state["targets"], candidate["targets"])
            raise
        return {"ok": True, "quarantined": quarantined}


def refresh_target_usage(*, now: float | None = None) -> dict[str, Any]:
    timestamp = time.time() if now is None else float(now)
    with _LOCK:
        state = load_state()
        candidate = copy.deepcopy(state)
        updated = []
        errors = []
        for index, current in enumerate(state["targets"]):
            if current.get("status") == "removed":
                continue
            try:
                _assert_target_mount(current)
                if not os.path.isdir(str(current.get("path", ""))):
                    raise RuntimeError("Target path is missing")
                allocated = _target_allocated_bytes(current)
            except Exception as exc:
                errors.append({"target_id": current.get("id"), "error": str(exc)})
                continue
            candidate["targets"][index]["allocated_bytes"] = allocated
            candidate["targets"][index]["usage_measured_at"] = timestamp
            updated.append(current["id"])
        if updated:
            save_state(candidate)
        return {"ok": not errors, "updated": updated, "errors": errors}


def update_target_policy(target_id: str, *, capacity_gb: int,
                         daily: int, weekly: int, monthly: int,
                         actor: str = "system",
                         now: float | None = None) -> dict[str, Any]:
    try:
        capacity_gb = int(capacity_gb)
        retention = {
            "daily": int(daily), "weekly": int(weekly), "monthly": int(monthly),
        }
    except (TypeError, ValueError):
        raise ValueError("Invalid target policy") from None
    if capacity_gb < 10 or capacity_gb > 10_000_000:
        raise ValueError("Capacity must be between 10 GB and 10 PB")
    if any(value < 0 or value > 365 for value in retention.values()):
        raise ValueError("Retention values must be between 0 and 365")

    with _LOCK:
        state = load_state()
        index = _target_index(state, target_id)
        current = state["targets"][index]
        if current.get("status") == "removed":
            raise ValueError("Removed target policy cannot be changed")
        _assert_target_mount(current)
        candidate = copy.deepcopy(state)
        target = candidate["targets"][index]
        old_limit = int(current.get("hard_limit_bytes", 0))
        new_limit = capacity_gb * GIB
        new_advertised = max(1, int(capacity_gb * 0.95)) * GIB
        if new_advertised < int(current.get("advertised_bytes", old_limit)):
            allocated = _target_allocated_bytes(current)
            if allocated > new_advertised:
                raise ValueError(
                    "Target currently uses more space than the new advertised capacity"
                )
        timestamp = time.time() if now is None else float(now)
        target["hard_limit_bytes"] = new_limit
        target["advertised_bytes"] = new_advertised
        target["protection_policy"] = retention
        target["policy_updated_at"] = timestamp
        _append_event(candidate, {
            "type": "target_policy_updated",
            "target_id": target_id,
            "actor": str(actor or "system")[:80],
            "created_at": timestamp,
            "changes": {
                "capacity_gb": {"from": old_limit // GIB, "to": capacity_gb},
                "protection_policy": {
                    "from": copy.deepcopy(current.get("protection_policy", {})),
                    "to": copy.deepcopy(retention),
                },
            },
        })

        quota_changed = new_limit != old_limit and current.get("backend") in {"zfs", "btrfs"}
        if quota_changed:
            _set_target_quota(current, new_limit)
        config_activated = False
        try:
            _activate_managed_config(candidate["targets"], state["targets"])
            config_activated = True
            save_state(candidate)
        except Exception as exc:
            rollback_errors = []
            if config_activated:
                try:
                    _activate_managed_config(state["targets"], candidate["targets"])
                except Exception as rollback_exc:
                    rollback_errors.append(f"managed config: {rollback_exc}")
            if quota_changed:
                try:
                    _set_target_quota(current, old_limit)
                except Exception as rollback_exc:
                    rollback_errors.append(f"native quota: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    f"{exc}; rollback failed ({'; '.join(rollback_errors)})"
                ) from exc
            raise
        return {"ok": True, "target": copy.deepcopy(target)}


def remove_target(target_id: str, *, actor: str = "system",
                  now: float | None = None) -> dict[str, Any]:
    """Remove a target from Samba while preserving its backup data."""
    with _LOCK:
        state = load_state()
        index = _target_index(state, target_id)
        replication_ids = {
            replication.get("id") for replication in state["replications"]
            if replication.get("target_id") == target_id
        }
        if any(
            job.get("type") == "replication"
            and job.get("replication_id") in replication_ids
            and job.get("status") == "running"
            for job in state["jobs"]
        ):
            raise ValueError("Replication is running; wait for it to finish")
        candidate = copy.deepcopy(state)
        target = candidate["targets"][index]
        timestamp = time.time() if now is None else float(now)
        target.update(
            enabled=False,
            status="removed",
            health_code="removed_data_preserved",
            removed_at=timestamp,
        )
        disabled_replication_ids = []
        for replication in candidate["replications"]:
            if replication.get("target_id") != target_id:
                continue
            disabled_replication_ids.append(replication.get("id"))
            replication["enabled"] = False
            if replication.get("status") != "promoted":
                replication["status"] = "paused"
            replication["updated_at"] = timestamp
        cancelled_job_ids = []
        for job in candidate["jobs"]:
            if job.get("type") == "replication" \
                    and job.get("replication_id") in replication_ids \
                    and job.get("status") == "queued":
                job["status"] = "cancelled"
                job["finished_at"] = timestamp
                cancelled_job_ids.append(job.get("id"))
        _append_event(candidate, {
            "type": "target_removed",
            "target_id": target_id,
            "actor": str(actor or "system")[:80],
            "created_at": timestamp,
            "disabled_replication_ids": disabled_replication_ids,
            "cancelled_job_ids": cancelled_job_ids,
        })
        _activate_managed_config(candidate["targets"], state["targets"])
        try:
            save_state(candidate)
        except Exception:
            _activate_managed_config(state["targets"], candidate["targets"])
            raise
        return {"ok": True, "target": copy.deepcopy(target), "data_preserved": True}


def _safe_target_path(target: dict[str, Any]) -> str:
    root = os.path.realpath(str(target.get("storage_root", "")))
    base = os.path.realpath(os.path.join(root, "runvard-time-machine"))
    raw_path = str(target.get("path", ""))
    if os.path.islink(raw_path):
        raise RuntimeError("Refusing to delete a symbolic link")
    path = os.path.realpath(_validate_target_path(raw_path))
    if path == base or os.path.commonpath([path, base]) != base:
        raise RuntimeError("Target path is outside the managed storage boundary")
    return path


def delete_target_data(target_id: str) -> dict[str, Any]:
    """Permanently delete data only after a target was explicitly removed."""
    with _LOCK:
        state = load_state()
        index = _target_index(state, target_id)
        target = state["targets"][index]
        if target.get("status") != "removed" or target.get("enabled", True):
            raise ValueError("Target must be removed before deleting its data")
        _assert_target_mount(target)
        if _target_has_open_handles(target):
            raise ValueError("Target still has open backup handles")
        path = _safe_target_path(target)
        if target.get("backend") == "zfs":
            result = _run(["zfs", "destroy", "-r", str(target.get("dataset", ""))])
            if not result["ok"]:
                raise RuntimeError(result.get("stderr") or "Could not delete ZFS target")
        elif target.get("backend") == "btrfs":
            protection_root = os.path.realpath(os.path.join(
                target["storage_root"], ".runvard-tm-protection", target_id,
            ))
            points = [
                point for point in state["protection_points"]
                if point.get("target_id") == target_id
            ]
            for point in points:
                snapshot_path = os.path.realpath(str(point.get("native_name", "")))
                if snapshot_path == protection_root or os.path.commonpath(
                    [snapshot_path, protection_root]
                ) != protection_root:
                    raise RuntimeError("Invalid Btrfs protection point")
                if os.path.exists(snapshot_path):
                    snapshot_result = _run([
                        "btrfs", "subvolume", "delete", snapshot_path,
                    ])
                    if not snapshot_result["ok"]:
                        raise RuntimeError(
                            snapshot_result.get("stderr")
                            or "Could not delete Btrfs protection point"
                        )
            result = _run(["btrfs", "subvolume", "delete", path])
            if not result["ok"]:
                raise RuntimeError(result.get("stderr") or "Could not delete Btrfs target")
        elif os.path.exists(path):
            shutil.rmtree(path)
        candidate = copy.deepcopy(state)
        del candidate["targets"][index]
        candidate["protection_points"] = [
            point for point in candidate["protection_points"]
            if point.get("target_id") != target_id
        ]
        save_state(candidate)
        return {"ok": True, "target_id": target_id, "data_deleted": True}


def _target_has_open_handles(target: dict[str, Any]) -> bool:
    result = _run(["smbstatus", "--json"])
    if not result["ok"]:
        return False
    try:
        payload = json.loads(result.get("stdout", ""))
    except (TypeError, json.JSONDecodeError):
        return False
    target_path = os.path.realpath(target["path"])

    def contains_path(value: Any) -> bool:
        if isinstance(value, dict):
            return any(contains_path(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_path(item) for item in value)
        if isinstance(value, str) and value.startswith("/"):
            candidate = os.path.realpath(value)
            return candidate == target_path or candidate.startswith(target_path + os.sep)
        return False

    return contains_path(payload)


def observe_target_activity(*, now: float | None = None) -> dict[str, Any]:
    """Persist recent SMB activity so health reflects actual Mac connections."""
    timestamp = time.time() if now is None else float(now)
    with _LOCK:
        state = load_state()
        active: list[str] = []
        changed = False
        for target in state["targets"]:
            if (
                target.get("enabled", True)
                and target.get("status") != "removed"
                and _target_has_open_handles(target)
            ):
                target["last_activity"] = timestamp
                active.append(str(target["id"]))
                changed = True
        if changed:
            save_state(state)
        return {"ok": True, "active": active}


def create_protection_point(target_id: str, *, kind: str = "manual",
                            now: float | None = None) -> dict[str, Any]:
    if kind not in {"manual", "daily", "weekly", "monthly"}:
        raise ValueError("Invalid protection point kind")
    with _LOCK:
        state = load_state()
        target = state["targets"][_target_index(state, target_id)]
        if not target.get("enabled", True) or target.get("status") == "removed":
            raise ValueError("Target is not active")
        if target.get("backend") not in {"zfs", "btrfs"}:
            raise ValueError("Storage protection points require ZFS or Btrfs")
        _assert_target_mount(target)
        if _target_has_open_handles(target):
            return {"ok": False, "deferred": True, "reason": "target_busy"}
        timestamp = int(time.time() if now is None else now)
        suffix = f"runvard-{kind}-{timestamp}"
        if target["backend"] == "zfs":
            native_name = f"{target['dataset']}@{suffix}"
            result = _run(["zfs", "snapshot", native_name])
        else:
            snapshot_root = os.path.join(
                target["storage_root"], ".runvard-tm-protection", target_id,
            )
            os.makedirs(snapshot_root, mode=0o700, exist_ok=True)
            native_name = os.path.join(snapshot_root, suffix)
            result = _run([
                "btrfs", "subvolume", "snapshot", "-r", target["path"], native_name,
            ])
        if not result["ok"]:
            raise RuntimeError(result.get("stderr") or "Could not create protection point")
        point = {
            "id": secrets.token_hex(8),
            "target_id": target_id,
            "kind": kind,
            "backend": target["backend"],
            "native_name": native_name,
            "created_at": timestamp,
        }
        candidate = copy.deepcopy(state)
        candidate["protection_points"].append(point)
        save_state(candidate)
        return {"ok": True, "protection_point": copy.deepcopy(point)}


def list_protection_points(target_id: str | None = None) -> list[dict[str, Any]]:
    points = load_state()["protection_points"]
    if target_id is not None:
        if not _ID_RE.fullmatch(str(target_id)):
            raise ValueError("Invalid target id")
        points = [point for point in points if point.get("target_id") == target_id]
    return copy.deepcopy(points)


def prune_protection_points(target_id: str) -> dict[str, Any]:
    """Apply per-kind retention without ever touching the live target."""
    with _LOCK:
        state = load_state()
        target = state["targets"][_target_index(state, target_id)]
        policy = target.get("protection_policy", {})
        delete_ids: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for kind in ("daily", "weekly", "monthly"):
            keep = max(0, int(policy.get(kind, 0)))
            points = sorted(
                (
                    point for point in state["protection_points"]
                    if point.get("target_id") == target_id and point.get("kind") == kind
                ),
                key=lambda point: point.get("created_at", 0),
            )
            candidates.extend(points[:-keep] if keep else points)
        for point in candidates:
            native_name = str(point.get("native_name", ""))
            if point.get("backend") == "zfs":
                dataset = str(target.get("dataset", ""))
                if not dataset or not native_name.startswith(dataset + "@"):
                    raise RuntimeError("Invalid ZFS protection point")
                result = _run(["zfs", "destroy", native_name])
            elif point.get("backend") == "btrfs":
                root = os.path.realpath(os.path.join(
                    target["storage_root"], ".runvard-tm-protection", target_id,
                ))
                path = os.path.realpath(native_name)
                if path == root or os.path.commonpath([path, root]) != root:
                    raise RuntimeError("Invalid Btrfs protection point")
                result = _run(["btrfs", "subvolume", "delete", path])
            else:
                continue
            if not result["ok"]:
                raise RuntimeError(result.get("stderr") or "Could not prune protection point")
            delete_ids.add(str(point.get("id", "")))
        if delete_ids:
            candidate = copy.deepcopy(state)
            candidate["protection_points"] = [
                point for point in candidate["protection_points"]
                if point.get("id") not in delete_ids
            ]
            save_state(candidate)
        return {"ok": True, "deleted": len(delete_ids)}


def run_scheduled_maintenance(*, now: float | None = None) -> dict[str, Any]:
    """Run one durable health/protection pass from a systemd oneshot service."""
    timestamp = time.time() if now is None else float(now)
    recovery = recover_interrupted_jobs(now=timestamp)
    job = {
        "id": secrets.token_hex(8),
        "type": "maintenance",
        "started_at": timestamp,
        "status": "running",
        "actions": [],
        "errors": [],
    }
    job["actions"].append({"action": "job_recovery", **recovery})
    if not recovery.get("ok", False):
        job["errors"].append({
            "component": "job_recovery",
            "failed_jobs": recovery.get("failed", []),
        })
    state = load_state()
    state["jobs"] = (state.get("jobs", []) + [job])[-100:]
    save_state(state)
    try:
        quarantined = quarantine_unavailable_targets(now=timestamp)
        job["actions"].append({"action": "target_quarantine", **quarantined})
    except Exception as exc:
        job["errors"].append({"component": "target_quarantine", "error": str(exc)})
    try:
        usage = refresh_target_usage(now=timestamp)
        job["actions"].append({"action": "target_usage", **usage})
        for error in usage.get("errors", []):
            job["errors"].append({"component": "target_usage", **error})
    except Exception as exc:
        job["errors"].append({"component": "target_usage", "error": str(exc)})
    try:
        activity = observe_target_activity(now=timestamp)
        job["actions"].append({"action": "observe_activity", **activity})
    except Exception as exc:
        job["errors"].append({"component": "activity", "error": str(exc)})
    for target in list_targets():
        if not target.get("enabled", True) or target.get("backend") not in {"zfs", "btrfs"}:
            continue
        try:
            _assert_target_mount(target)
            usage = shutil.disk_usage(target["path"])
            pool_percent = (usage.used / usage.total * 100) if usage.total else 100
            if pool_percent >= 95:
                job["actions"].append({
                    "target_id": target["id"], "action": "protection_paused_pool_full",
                })
                continue
            local = time.localtime(timestamp)
            kinds = ["daily"]
            if local.tm_wday == 0:
                kinds.append("weekly")
            if local.tm_mday == 1:
                kinds.append("monthly")
            existing = list_protection_points(target["id"])
            day_start = timestamp - (
                local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec
            )
            for kind in kinds:
                if any(
                    point.get("kind") == kind
                    and float(point.get("created_at", 0)) >= day_start
                    for point in existing
                ):
                    continue
                result = create_protection_point(
                    target["id"], kind=kind, now=timestamp,
                )
                job["actions"].append({
                    "target_id": target["id"], "action": kind,
                    "deferred": bool(result.get("deferred")),
                })
            prune_protection_points(target["id"])
        except Exception as exc:
            job["errors"].append({"target_id": target.get("id"), "error": str(exc)})
    try:
        scheduled = schedule_due_replications(now=timestamp)
        job["actions"].append({"action": "replication_schedule", **scheduled})
    except Exception as exc:
        job["errors"].append({"component": "replication_schedule", "error": str(exc)})
    try:
        processed = process_replication_queue(limit=1, now=timestamp)
        job["actions"].append({"action": "replication_queue", **processed})
        for error in processed.get("errors", []):
            job["errors"].append({"component": "replication", **error})
    except Exception as exc:
        job["errors"].append({"component": "replication_queue", "error": str(exc)})
    try:
        alerts = emit_health_alerts(now=timestamp)
        job["actions"].append({"action": "alerts", **alerts})
    except Exception as exc:
        job["errors"].append({"component": "alerts", "error": str(exc)})
    job["status"] = "failed" if job["errors"] else "completed"
    job["finished_at"] = time.time()
    final_state = load_state()
    final_state["jobs"] = [
        job if item.get("id") == job["id"] else item
        for item in final_state.get("jobs", [])
    ][-100:]
    save_state(final_state)
    return copy.deepcopy(job)


def health_check() -> dict[str, Any]:
    state = load_state()
    checked = []
    for source_target in state["targets"]:
        target = copy.deepcopy(source_target)
        root = target.get("storage_root", "")
        info = _mount_info(root) if root else None
        expected_source = target.get("mount_source", "")
        if target.get("status") == "removed":
            target.update(status="removed", health_code="removed_data_preserved")
        elif not info or os.path.realpath(info.get("mountpoint", "")) != os.path.realpath(root):
            target.update(status="critical", health_code="mount_missing")
        elif expected_source and info.get("source") != expected_source:
            target.update(status="critical", health_code="mount_identity_changed")
        elif not os.path.isdir(target.get("path", "")):
            target.update(status="critical", health_code="target_path_missing")
        elif not target.get("enabled", True):
            target.update(status="paused", health_code="paused")
        else:
            try:
                usage = shutil.disk_usage(target["path"])
                target["filesystem_free_bytes"] = usage.free
                target["filesystem_total_bytes"] = usage.total
            except OSError:
                pass
            if target.get("last_activity"):
                target.update(status="active", health_code="healthy")
            else:
                target.update(status="waiting", health_code="waiting_for_first_backup")
        checked.append(target)
    return {"targets": checked, "checked_at": time.time()}


def managed_config_hash() -> str | None:
    try:
        content = Path(MANAGED_SMB_CONF).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest()


def reconcile_managed_config() -> dict[str, Any]:
    """Explicitly replace a drifted include with the registered desired state."""
    with _LOCK:
        state = load_state()
        targets = copy.deepcopy(state["targets"])
        _activate_managed_config(targets, targets, allow_drift=True)
        return {
            "ok": True, "drift": _managed_config_has_drift(targets),
            "managed_config_hash": managed_config_hash(),
        }


def _service_active(name: str) -> bool:
    result = _run(["systemctl", "is-active", name])
    return bool(result["ok"] and result.get("stdout", "").strip() == "active")


def _audit_aapl_configuration(text: str) -> dict[str, Any]:
    current = "global"
    risky: list[str] = []
    global_disabled = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if "=" not in line:
            continue
        key, value = (part.strip().lower() for part in line.split("=", 1))
        if key == "fruit:aapl" and value in {"no", "false", "0"}:
            if current == "global":
                global_disabled = True
            elif current not in risky:
                risky.append(current)
        if key == "vfs objects" and current != "global":
            modules = set(value.split())
            if modules and "fruit" not in modules and current not in risky:
                risky.append(current)
    return {
        "ok": not global_disabled and not risky,
        "global_aapl_disabled": global_disabled,
        "risky_shares": risky,
    }


def system_status() -> dict[str, Any]:
    """Return a read-only preflight without modifying host configuration."""
    version_result = _run(["smbd", "--version"])
    version = version_result.get("stdout", "").strip()
    if version.lower().startswith("version "):
        version = version[8:]
    testparm = _run(["testparm", "-s", "--suppress-prompt", SMB_CONF])
    aapl = _audit_aapl_configuration(testparm.get("stdout", "")) \
        if testparm["ok"] else {
            "ok": False, "global_aapl_disabled": False, "risky_shares": [],
        }
    state = load_state()
    expected = render_samba_config(state["targets"])
    try:
        actual = Path(MANAGED_SMB_CONF).read_text(encoding="utf-8")
    except OSError:
        actual = ""
    drift = _managed_config_has_drift(state["targets"])
    smbd_active = _service_active("smbd")
    avahi_active = _service_active("avahi-daemon")
    worker_active = _service_active("runvard-time-machine-maintenance.timer")
    ready = bool(
        version_result["ok"] and testparm["ok"] and smbd_active
        and avahi_active and worker_active and aapl["ok"] and not drift
    )
    return {
        "ready": ready,
        "limits": {"registered_macs": 20, "concurrent_backups": 10},
        "samba": {
            "installed": bool(version_result["ok"]),
            "active": smbd_active,
            "configuration_valid": bool(testparm["ok"]),
            "version": version,
        },
        "avahi": {"installed": avahi_active, "active": avahi_active},
        "worker": {"timer_active": worker_active},
        "aapl_audit": aapl,
        "managed_config": {
            "drift": drift,
            "actual_hash": managed_config_hash(),
            "expected_hash": hashlib.sha256(expected.encode()).hexdigest(),
        },
    }


def setup_guide(target_id: str, *, hostname: str) -> dict[str, Any]:
    state = load_state()
    target = state["targets"][_target_index(state, target_id)]
    hostname = str(hostname or "").strip().rstrip(".")
    if not hostname or len(hostname) > 253 or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.:-]*", hostname,
    ):
        raise ValueError("Invalid hostname")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    url = f"smb://{hostname}/{target['share_name']}"
    return {
        "target_id": target_id,
        "display_name": target.get("display_name", ""),
        "owner": target["owner"],
        "share_name": target["share_name"],
        "lan": {"bonjour": True, "url": url},
        "vpn": {"bonjour": False, "url": url},
        "client_encryption_required": True,
        "server_can_verify_client_encryption": False,
        "restore_with_apple_tools": True,
        "steps": [
            "Connect to the SMB destination with the assigned backup account.",
            "Open System Settings, General, Time Machine, then Add Backup Disk.",
            "Select the runvard share and enable Encrypt Backup.",
            "Store the separate Time Machine encryption password safely.",
        ],
    }
