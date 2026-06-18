"""
Write security-relevant audit events.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

SECRET_KEYS = {"password", "token", "confirm_token", "secret", "key"}


def data_dir() -> str:
    """
    Return the configured runvard data directory.

    Returns:
    --------
        str:
            Data directory.
    """
    return os.environ.get("RUNVARD_DATA_DIR", "/opt/runvard/data")


def audit_log() -> str:
    """
    Return the audit log path.

    Returns:
    --------
        str:
            Audit log path.
    """
    return os.path.join(data_dir(), "audit.log")


def sanitize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """
    Remove secrets from an audit payload.

    Args:
    -----
        payload (dict[str, Any] | None):
            Event payload.

    Returns:
    --------
        dict[str, Any]:
            Sanitized payload.
    """
    if not payload:
        return {}
    clean = {}
    for key, value in payload.items():
        if any(secret in key.lower() for secret in SECRET_KEYS):
            clean[key] = "<redacted>"
        else:
            clean[key] = value
    return clean


def record_event(
    user: str,
    action: str,
    target: str = "",
    ok: bool = True,
    remote: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Append one audit event.

    Args:
    -----
        user (str):
            Authenticated user.
        action (str):
            Action name.
        target (str):
            Target resource.
        ok (bool):
            Whether the action succeeded.
        remote (str):
            Remote client address.
        payload (dict[str, Any] | None):
            Additional event context.
    """
    path = audit_log()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = {
        "time": time.time(),
        "user": user,
        "action": action,
        "target": target,
        "ok": ok,
        "remote": remote,
        "payload": sanitize_payload(payload),
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
