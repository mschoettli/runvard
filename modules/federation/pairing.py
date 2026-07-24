"""Short-lived, single-use pairing codes."""

from __future__ import annotations

import hashlib
import secrets
import time


PAIRING_TTL = 600


def _digest(code):
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


class PairingCodes:
    def __init__(self, values, failures=None, ttl=PAIRING_TTL, max_failures=5):
        self.values = values
        self.failures = failures if failures is not None else {}
        self.ttl = ttl
        self.max_failures = max_failures

    def issue(self, now=None):
        created = int(time.time() if now is None else now)
        code = secrets.token_urlsafe(24)
        self.values.clear()
        self.values[_digest(code)] = {
            "created_at": created,
            "expires_at": created + self.ttl,
            "used": False,
        }
        return code

    def consume(self, code, remote, now=None):
        current = int(time.time() if now is None else now)
        recent = [
            item for item in self.failures.get(remote, [])
            if current - int(item) < 60
        ]
        self.failures[remote] = recent
        if len(recent) >= self.max_failures:
            raise ValueError("pairing rate limit exceeded")

        entry = self.values.get(_digest(code))
        if not entry:
            recent.append(current)
            raise ValueError("invalid pairing code")
        if entry.get("used"):
            raise ValueError("pairing code was already used")
        if current > int(entry.get("expires_at") or 0):
            raise ValueError("pairing code expired")
        entry["used"] = True
        return True
