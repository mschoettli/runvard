"""Persistent federation state."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


def default_state():
    return {
        "enabled": False,
        "federation_id": "",
        "settings": {
            "name": "",
            "internal_url": "",
            "browser_url": "",
            "allowed_cidrs": [
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "100.64.0.0/10",
                "fd00::/8",
            ],
        },
        "nodes": {},
        "events": {},
        "peer_status": {},
        "pairing_codes": {},
        "pairing_failures": {},
        "tickets": {},
        "nonces": {},
    }


class FederationStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "state.json"
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    data = json.load(handle)
            except (FileNotFoundError, OSError, ValueError):
                return default_state()
            base = default_state()
            for key, value in data.items():
                if key == "settings" and isinstance(value, dict):
                    base["settings"].update(value)
                else:
                    base[key] = value
            return base

    def save(self, state):
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
            return state
