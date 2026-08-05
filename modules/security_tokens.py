"""
Manage short-lived tokens for dangerous actions and terminal sessions.
"""

from __future__ import annotations

import secrets
import time

CONFIRM_TTL_SECONDS = 300
TERMINAL_TTL_SECONDS = 120

_confirm_tokens: dict[str, dict[str, object]] = {}
_terminal_tokens: dict[str, dict[str, object]] = {}


def _prune(store: dict[str, dict[str, object]]) -> None:
    now = time.time()
    expired = [token for token, meta in store.items() if float(meta["expires"]) < now]
    for token in expired:
        store.pop(token, None)


def issue_confirm_token(user: str, action: str, target: str) -> dict[str, object]:
    """
    Create a short-lived token for a dangerous action.

    Args:
    -----
        user (str):
            Authenticated user.
        action (str):
            Action name.
        target (str):
            Target resource.

    Returns:
    --------
        dict[str, object]:
            Token response with expiry.
    """
    _prune(_confirm_tokens)
    token = secrets.token_urlsafe(32)
    expires = time.time() + CONFIRM_TTL_SECONDS
    _confirm_tokens[token] = {
        "user": user,
        "action": action,
        "target": target,
        "expires": expires,
    }
    return {"token": token, "expires_at": expires}


def require_confirm_token(user: str, action: str, target: str, token: str) -> None:
    """
    Validate and consume a dangerous-action token.

    Args:
    -----
        user (str):
            Authenticated user.
        action (str):
            Expected action.
        target (str):
            Expected target.
        token (str):
            Token supplied by the caller.

    Raises:
    -------
        PermissionError:
            Raised when the token is missing, expired, or mismatched.
    """
    _prune(_confirm_tokens)
    meta = _confirm_tokens.pop(token or "", None)
    if not meta:
        raise PermissionError("Missing or invalid confirmation token")
    if meta["user"] != user or meta["action"] != action or meta["target"] != target:
        raise PermissionError("Confirmation token does not match action")


def issue_terminal_token(user: str) -> dict[str, object]:
    """
    Create a short-lived token for one terminal WebSocket session.

    Args:
    -----
        user (str):
            Authenticated user.

    Returns:
    --------
        dict[str, object]:
            Token response with expiry.
    """
    _prune(_terminal_tokens)
    token = secrets.token_urlsafe(32)
    expires = time.time() + TERMINAL_TTL_SECONDS
    _terminal_tokens[token] = {"user": user, "expires": expires}
    return {"token": token, "expires_at": expires}


def consume_terminal_token(token: str, user: str | None = None) -> str:
    """
    Validate and consume a terminal token.

    Args:
    -----
        token (str):
            Token supplied by the WebSocket client.

    Returns:
    --------
        str:
            Authenticated user that owns the token.

    Raises:
    -------
        PermissionError:
            Raised when the token is invalid.
    """
    _prune(_terminal_tokens)
    meta = _terminal_tokens.pop(token or "", None)
    if not meta:
        raise PermissionError("Missing or invalid terminal token")
    if user is not None and meta["user"] != user:
        raise PermissionError("Terminal token does not match user")
    return str(meta["user"])


def clear_terminal_tokens(user: str | None = None) -> None:
    """Revoke all terminal grants, or only grants owned by one user."""
    if user is None:
        _terminal_tokens.clear()
        return
    for token, meta in list(_terminal_tokens.items()):
        if meta.get("user") == user:
            _terminal_tokens.pop(token, None)
