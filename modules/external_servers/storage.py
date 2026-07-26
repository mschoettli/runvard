"""Atomic registry and encrypted credential storage for external servers."""

from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.utils import random as random_bytes


def _atomic_json(path: Path, payload: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload, handle, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _atomic_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def default_state() -> dict:
    return {"servers": {}, "statuses": {}}


class ExternalServerStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "servers.json"
        self._lock = threading.RLock()

    def load(self) -> dict:
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    value = json.load(handle)
            except (FileNotFoundError, OSError, ValueError):
                return default_state()
            state = default_state()
            if isinstance(value, dict):
                for key in state:
                    if isinstance(value.get(key), dict):
                        state[key] = value[key]
            return state

    def save(self, state: dict) -> dict:
        with self._lock:
            _atomic_json(self.path, state)
            return state


class SecretStore:
    """Encrypt one JSON credential object per server with a local SecretBox key."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.key_path = self.data_dir / "secret.key"
        self.path = self.data_dir / "secrets.json"
        self._lock = threading.RLock()

    def _key(self) -> bytes:
        try:
            encoded = self.key_path.read_bytes().strip()
            key = base64.urlsafe_b64decode(encoded)
            if len(key) != SecretBox.KEY_SIZE:
                raise ValueError("invalid external server secret key")
        except FileNotFoundError:
            key = random_bytes(SecretBox.KEY_SIZE)
            _atomic_bytes(
                self.key_path, base64.urlsafe_b64encode(key) + b"\n",
            )
        os.chmod(self.key_path, 0o600)
        return key

    def _load(self) -> dict:
        try:
            with self.path.open(encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, ValueError):
            return {}

    def get(self, server_id: str) -> dict:
        with self._lock:
            encoded = self._load().get(str(server_id))
            if not encoded:
                return {}
            try:
                plaintext = SecretBox(self._key()).decrypt(
                    base64.urlsafe_b64decode(encoded),
                )
                value = json.loads(plaintext.decode("utf-8"))
            except (CryptoError, ValueError, TypeError, json.JSONDecodeError):
                raise ValueError("stored external server credentials are invalid")
            return value if isinstance(value, dict) else {}

    def set(self, server_id: str, credentials: dict) -> None:
        with self._lock:
            values = self._load()
            plaintext = json.dumps(
                credentials, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            encrypted = SecretBox(self._key()).encrypt(plaintext)
            values[str(server_id)] = base64.urlsafe_b64encode(
                encrypted,
            ).decode("ascii")
            _atomic_json(self.path, values)

    def delete(self, server_id: str) -> None:
        with self._lock:
            values = self._load()
            if values.pop(str(server_id), None) is not None:
                _atomic_json(self.path, values)
