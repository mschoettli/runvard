"""Short-lived, role-preserving federation SSO assertions."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

from .crypto import canonical_json, sign_payload, verify_payload


TICKET_TTL = 60


def _encode(payload):
    raw = canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(ticket):
    try:
        raw = base64.urlsafe_b64decode(ticket + "=" * (-len(ticket) % 4))
        value = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid SSO ticket") from exc
    if not isinstance(value, dict) or not isinstance(value.get("claim"), dict):
        raise ValueError("invalid SSO ticket")
    return value


def ticket_claim(ticket):
    """Read an untrusted claim so the caller can locate its trusted issuer."""
    return dict(_decode(ticket)["claim"])


def _hash(ticket):
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def issue_ticket(
    identity, target_node_id, federation_id, username, role, expert, pending,
    now=None,
):
    issued_at = int(time.time() if now is None else now)
    safe_role = role if role in {"admin", "readonly"} else "readonly"
    claim = {
        "audience": target_node_id,
        "expires_at": issued_at + TICKET_TTL,
        "expert": bool(expert and safe_role == "admin"),
        "federation_id": federation_id,
        "issued_at": issued_at,
        "issuer": identity.node_id,
        "role": safe_role,
        "ticket_id": secrets.token_urlsafe(18),
        "username": str(username)[:128],
    }
    ticket = _encode({"claim": claim, "signature": sign_payload(identity, claim)})
    pending[claim["ticket_id"]] = {
        "expires_at": claim["expires_at"],
        "hash": _hash(ticket),
        "redeemed": False,
    }
    return ticket


def validate_ticket(
    ticket, issuer_public_key, expected_target, expected_federation, now=None,
):
    current = int(time.time() if now is None else now)
    value = _decode(ticket)
    claim = value["claim"]
    verify_payload(issuer_public_key, claim, str(value.get("signature") or ""))
    if claim.get("audience") != expected_target:
        raise ValueError("wrong SSO audience")
    if claim.get("federation_id") != expected_federation:
        raise ValueError("wrong SSO federation")
    if current > int(claim.get("expires_at") or 0):
        raise ValueError("SSO ticket expired")
    if int(claim.get("issued_at") or 0) > current + 30:
        raise ValueError("SSO ticket clock is invalid")
    if claim.get("role") not in {"admin", "readonly"}:
        raise ValueError("invalid SSO role")
    if claim.get("role") != "admin":
        claim["expert"] = False
    return claim


def redeem_ticket(ticket, pending, now=None):
    current = int(time.time() if now is None else now)
    value = _decode(ticket)
    ticket_id = str(value["claim"].get("ticket_id") or "")
    entry = pending.get(ticket_id)
    if not entry or not secrets.compare_digest(
        str(entry.get("hash") or ""), _hash(ticket),
    ):
        raise ValueError("unknown SSO ticket")
    if entry.get("redeemed"):
        raise ValueError("SSO ticket already redeemed")
    if current > int(entry.get("expires_at") or 0):
        raise ValueError("SSO ticket expired")
    entry["redeemed"] = True
    return True
