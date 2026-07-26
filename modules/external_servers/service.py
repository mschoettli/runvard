"""External server registry, polling, normalized status, and lifecycle."""

from __future__ import annotations

import copy
import ipaddress
import secrets as random_secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

import requests

from .connectors import connector_for, normalize_snapshot
from .storage import ExternalServerStore, SecretStore


KINDS = {"generic", "proxmox", "linux", "windows"}
DEFAULT_ALLOWED_CIDRS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "fd00::/8",
)
UPDATE_CACHE_SECONDS = 15 * 60
MAX_SERVERS = 20


def _private_address(value):
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("host must be a literal private IP address") from exc
    networks = [
        ipaddress.ip_network(cidr, strict=False)
        for cidr in DEFAULT_ALLOWED_CIDRS
    ]
    if not any(
        address.version == network.version and address in network
        for network in networks
    ):
        raise ValueError("host is outside the allowed private networks")
    return str(address)


def validate_admin_url(value):
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("administration URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("administration URL must not contain credentials")
    return str(value).strip()


def validate_status_url(value, *, windows=False):
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("status URL must use HTTP or HTTPS")
    if windows and parsed.scheme != "https":
        raise ValueError("Windows status URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("status URL must not contain credentials or query data")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("status URL must include a literal private IP and port")
    _private_address(parsed.hostname)
    if windows and parsed.path.rstrip("/") != "/wsman":
        raise ValueError("Windows status URL must end with /wsman")
    return str(value).strip().rstrip("/")


def _bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _clean_config(config):
    source = copy.deepcopy(config or {})
    kind = str(source.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("unsupported server type")
    result = {
        "name": str(source.get("name") or "").strip()[:80],
        "kind": kind,
        "admin_url": validate_admin_url(source.get("admin_url")),
        "enabled": _bool(source.get("enabled"), True),
        "verify_tls": _bool(source.get("verify_tls"), True),
    }
    if not result["name"]:
        raise ValueError("server name is required")
    if kind == "linux":
        result.update({
            "host": _private_address(source.get("host")),
            "port": int(source.get("port") or 22),
            "username": str(source.get("username") or "").strip()[:128],
            "host_key": str(source.get("host_key") or "").strip()[:256],
        })
        if not 1 <= result["port"] <= 65535:
            raise ValueError("SSH port is invalid")
        if not result["username"]:
            raise ValueError("SSH username is required")
        if not result["host_key"].startswith("SHA256:"):
            raise ValueError("SSH host key fingerprint is required")
    else:
        result["status_url"] = validate_status_url(
            source.get("status_url"), windows=kind == "windows",
        )
        if kind == "proxmox":
            result["node"] = str(source.get("node") or "").strip()[:80]
        elif kind == "windows":
            result["username"] = str(
                source.get("username") or "",
            ).strip()[:128]
            if not result["username"]:
                raise ValueError("WinRM username is required")
    return result


def _clean_credentials(kind, credentials, *, required=True):
    source = credentials or {}
    if kind == "linux":
        result = {
            "private_key": str(source.get("private_key") or ""),
            "passphrase": str(source.get("passphrase") or ""),
        }
        complete = bool(result["private_key"].strip())
    elif kind == "proxmox":
        result = {
            "token_id": str(source.get("token_id") or "").strip(),
            "token_secret": str(source.get("token_secret") or "").strip(),
        }
        complete = bool(result["token_id"] and result["token_secret"])
    elif kind == "windows":
        result = {"password": str(source.get("password") or "")}
        complete = bool(result["password"])
    else:
        result, complete = {}, True
    if required and not complete:
        raise ValueError("credentials are incomplete")
    return result if complete else {}


def _error_category(exc):
    if isinstance(exc, (TimeoutError, requests.Timeout)):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "certificate"
    if isinstance(exc, (PermissionError, paramiko_auth_error())):
        return "authentication"
    text = str(exc).lower()
    if any(word in text for word in ("auth", "credential", "password", "401")):
        return "authentication"
    if any(word in text for word in ("certificate", "ssl", "tls")):
        return "certificate"
    if any(word in text for word in ("timeout", "timed out")):
        return "timeout"
    return "unreachable"


def error_category(exc):
    """Return a bounded diagnostic category without leaking remote details."""
    return _error_category(exc)


def paramiko_auth_error():
    try:
        import paramiko
        return paramiko.AuthenticationException
    except ImportError:
        return PermissionError


class ExternalServerManager:
    def __init__(
        self, data_dir, connector_factory=connector_for, now=time.time,
    ):
        self.store = ExternalServerStore(data_dir)
        self.secrets = SecretStore(data_dir)
        self.state = self.store.load()
        self.connector_factory = connector_factory
        self.now = now
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None

    def _save(self):
        self.store.save(self.state)

    def _admin_row(self, server_id, config):
        return {
            **copy.deepcopy(config),
            "server_id": server_id,
            "has_credentials": bool(self.secrets.get(server_id))
            or config["kind"] == "generic",
            "status": copy.deepcopy(
                self.state["statuses"].get(server_id, {}),
            ),
        }

    def admin_list(self):
        with self.lock:
            return [
                self._admin_row(server_id, config)
                for server_id, config in self.state["servers"].items()
            ]

    def create(self, config, credentials=None):
        clean = _clean_config(config)
        secret = _clean_credentials(clean["kind"], credentials)
        with self.lock:
            if len(self.state["servers"]) >= MAX_SERVERS:
                raise ValueError("external server limit reached")
            server_id = random_secrets.token_hex(12)
            self.state["servers"][server_id] = clean
            self.state["statuses"][server_id] = {
                "health": "unknown",
                "failures": 0,
                "snapshot": normalize_snapshot({}, now=self.now()),
                "last_attempt": None,
                "last_success": None,
                "updates_checked_at": None,
                "error": None,
            }
            if secret:
                self.secrets.set(server_id, secret)
            self._save()
            return self._admin_row(server_id, clean)

    def update(self, server_id, config, credentials=None):
        clean = _clean_config(config)
        with self.lock:
            previous = self.state["servers"].get(server_id)
            if not previous:
                raise ValueError("unknown external server")
            existing_secret = self.secrets.get(server_id)
            replacement = _clean_credentials(
                clean["kind"], credentials, required=False,
            )
            if clean["kind"] == "generic":
                secret = {}
            elif replacement:
                secret = replacement
            elif previous["kind"] == clean["kind"]:
                secret = existing_secret
            else:
                secret = {}
            _clean_credentials(clean["kind"], secret)
            self.state["servers"][server_id] = clean
            if replacement:
                self.secrets.set(server_id, replacement)
            elif clean["kind"] == "generic":
                self.secrets.delete(server_id)
            self._save()
            return self._admin_row(server_id, clean)

    def delete(self, server_id):
        with self.lock:
            if self.state["servers"].pop(server_id, None) is None:
                raise ValueError("unknown external server")
            self.state["statuses"].pop(server_id, None)
            self.secrets.delete(server_id)
            self._save()
            return {"ok": True}

    def set_enabled(self, server_id, enabled):
        with self.lock:
            config = self.state["servers"].get(server_id)
            if not config:
                raise ValueError("unknown external server")
            config["enabled"] = _bool(enabled)
            self._save()
            return self._admin_row(server_id, config)

    def test_connection(self, config, credentials=None, server_id=None):
        clean = _clean_config(config)
        secret = _clean_credentials(
            clean["kind"], credentials, required=False,
        )
        if not secret and server_id:
            with self.lock:
                saved = self.state["servers"].get(server_id)
                if not saved or saved["kind"] != clean["kind"]:
                    raise ValueError("unknown external server")
                secret = self.secrets.get(server_id)
        secret = _clean_credentials(clean["kind"], secret)
        connector = self.connector_factory(clean["kind"])
        snapshot = normalize_snapshot(
            connector.collect(clean, secret), now=self.now(),
        )
        try:
            snapshot["updates"] = connector.collect_updates(clean, secret)
        except Exception:
            snapshot["updates"] = None
        return {"ok": True, "snapshot": snapshot}

    def _poll_one(self, server_id, force_updates=False):
        with self.lock:
            config = copy.deepcopy(self.state["servers"].get(server_id))
            if not config or not config.get("enabled"):
                return
            credentials = self.secrets.get(server_id)
            status = copy.deepcopy(self.state["statuses"].get(server_id, {}))
        attempted_at = int(self.now())
        try:
            connector = self.connector_factory(config["kind"])
            values = connector.collect(config, credentials)
            snapshot = normalize_snapshot(values, now=attempted_at)
            updates_at = status.get("updates_checked_at")
            should_update = force_updates or not updates_at or (
                attempted_at - int(updates_at) >= UPDATE_CACHE_SECONDS
            )
            if should_update:
                try:
                    snapshot["updates"] = connector.collect_updates(
                        config, credentials,
                    )
                    updates_at = attempted_at
                except Exception:
                    snapshot["updates"] = (
                        status.get("snapshot") or {}
                    ).get("updates")
            else:
                snapshot["updates"] = (
                    status.get("snapshot") or {}
                ).get("updates")
            updated = {
                "health": "online",
                "failures": 0,
                "snapshot": snapshot,
                "last_attempt": attempted_at,
                "last_success": attempted_at,
                "updates_checked_at": updates_at,
                "error": None,
            }
        except Exception as exc:
            failures = int(status.get("failures") or 0) + 1
            updated = {
                **status,
                "health": "offline" if failures >= 3 else "degraded",
                "failures": failures,
                "last_attempt": attempted_at,
                "error": _error_category(exc),
            }
        with self.lock:
            if server_id in self.state["servers"]:
                self.state["statuses"][server_id] = updated
                self._save()

    def refresh(self, server_ids=None, force_updates=False):
        with self.lock:
            selected = [
                server_id for server_id, config in self.state["servers"].items()
                if config.get("enabled")
                and (server_ids is None or server_id in set(server_ids))
            ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [
                pool.submit(self._poll_one, server_id, force_updates)
                for server_id in selected
            ]
            for job in as_completed(jobs):
                job.result()
        return self.overview()

    def overview(self):
        with self.lock:
            nodes = []
            for server_id, config in self.state["servers"].items():
                if not config.get("enabled"):
                    continue
                status = self.state["statuses"].get(server_id, {})
                nodes.append({
                    "node_id": f"external:{server_id}",
                    "external_id": server_id,
                    "external": True,
                    "kind": config["kind"],
                    "name": config["name"],
                    "browser_url": config["admin_url"],
                    "health": status.get("health", "unknown"),
                    "last_contact": status.get("last_success"),
                    "snapshot": copy.deepcopy(
                        status.get("snapshot") or normalize_snapshot({}),
                    ),
                })
            nodes.sort(key=lambda row: row["name"].lower())
            return {
                "nodes": nodes,
                "online": sum(
                    1 for row in nodes if row["health"] == "online"
                ),
                "total": len(nodes),
            }

    def start(self, interval=15):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()

        def run():
            while not self.stop_event.wait(1):
                self.refresh()
                if self.stop_event.wait(max(1, interval - 1)):
                    break

        self.worker = threading.Thread(
            target=run, name="runvard-external-servers", daemon=True,
        )
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=2)
