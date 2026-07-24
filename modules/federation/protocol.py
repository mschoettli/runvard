"""Authentication and destination validation for federation peer traffic."""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import time
from urllib.parse import urlsplit

from .crypto import _b64decode, canonical_json, sign_payload, verify_payload


API_VERSION = "1"
MAX_CLOCK_SKEW = 30
NONCE_TTL = 120


def _node_id(public_key):
    return hashlib.sha256(_b64decode(public_key)).hexdigest()[:32]


def _body_hash(body):
    value = b"" if body is None else canonical_json(body).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _envelope(sender, target, method, path, body_hash, timestamp, nonce):
    return {
        "body_sha256": body_hash,
        "method": method.upper(),
        "nonce": nonce,
        "path": path,
        "sender": sender,
        "target": target,
        "timestamp": int(timestamp),
        "version": API_VERSION,
    }


def sign_request(identity, target, method, path, body, now=None, nonce=None):
    timestamp = int(time.time() if now is None else now)
    nonce = nonce or secrets.token_urlsafe(24)
    envelope = _envelope(
        identity.node_id, target, method, path, _body_hash(body), timestamp, nonce,
    )
    return {
        "X-Runvard-Node": identity.node_id,
        "X-Runvard-Target": target,
        "X-Runvard-Time": str(timestamp),
        "X-Runvard-Nonce": nonce,
        "X-Runvard-Version": API_VERSION,
        "X-Runvard-Signature": sign_payload(identity, envelope),
    }


class NonceCache:
    """Small replay cache; callers can persist its dictionary if required."""

    def __init__(self, values=None, ttl=NONCE_TTL, limit=4096):
        self.values = values if values is not None else {}
        self.ttl = ttl
        self.limit = limit

    def consume(self, sender, nonce, now):
        cutoff = int(now) - self.ttl
        stale = [key for key, seen in self.values.items() if int(seen) < cutoff]
        for key in stale:
            self.values.pop(key, None)
        key = f"{sender}:{nonce}"
        if key in self.values:
            raise ValueError("request replay detected")
        if len(self.values) >= self.limit:
            oldest = min(self.values, key=lambda item: self.values[item])
            self.values.pop(oldest, None)
        self.values[key] = int(now)


def verify_request(
    headers, public_key, expected_target, method, path, body, nonce_cache, now=None,
):
    timestamp_now = int(time.time() if now is None else now)
    sender = str(headers.get("X-Runvard-Node") or "")
    if not sender or sender != _node_id(public_key):
        raise ValueError("unknown request sender")
    target = str(headers.get("X-Runvard-Target") or "")
    if target != expected_target:
        raise ValueError("wrong request recipient")
    if str(headers.get("X-Runvard-Version") or "") != API_VERSION:
        raise ValueError("incompatible federation API")
    try:
        timestamp = int(headers.get("X-Runvard-Time"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid request clock") from exc
    if abs(timestamp_now - timestamp) > MAX_CLOCK_SKEW:
        raise ValueError("request clock skew is too large")
    nonce = str(headers.get("X-Runvard-Nonce") or "")
    if len(nonce) < 2:
        raise ValueError("missing request nonce")
    envelope = _envelope(
        sender, target, method, path, _body_hash(body), timestamp, nonce,
    )
    verify_payload(
        public_key, envelope, str(headers.get("X-Runvard-Signature") or ""),
    )
    nonce_cache.consume(sender, nonce, timestamp_now)
    return sender


def validate_internal_url(value, allowed_cidrs):
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("internal URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("internal URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("internal URL must be an origin without path or query")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("internal URL must include a literal IP address and port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        networks = [ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs]
    except ValueError as exc:
        raise ValueError("internal URL must contain a literal IP address") from exc
    if not any(
        address.version == network.version and address in network
        for network in networks
    ):
        raise ValueError("internal URL is outside the allowed networks")
    return str(value).strip().rstrip("/")
