"""Signed federation membership events."""

from __future__ import annotations

import copy
import hashlib
import secrets
import time

from .crypto import canonical_json, sign_payload, verify_payload


EVENT_KINDS = {"node_joined", "node_updated", "node_revoked"}


def empty_membership(federation_id):
    return {
        "federation_id": federation_id,
        "nodes": {},
        "events": {},
    }


def _unsigned(event):
    return {
        key: copy.deepcopy(value)
        for key, value in event.items()
        if key not in {"event_id", "signature"}
    }


def create_event(identity, federation_id, kind, subject, now=None):
    if kind not in EVENT_KINDS:
        raise ValueError("unsupported event kind")
    unsigned = {
        "federation_id": federation_id,
        "kind": kind,
        "issuer": identity.node_id,
        "subject": copy.deepcopy(subject),
        "created_at": int(time.time() if now is None else now),
        "nonce": secrets.token_hex(16),
    }
    signature = sign_payload(identity, unsigned)
    event_id = hashlib.sha256(
        canonical_json({"event": unsigned, "signature": signature}).encode("utf-8")
    ).hexdigest()
    return {**unsigned, "signature": signature, "event_id": event_id}


def _issuer_key(state, event):
    issuer = event.get("issuer", "")
    node = state.get("nodes", {}).get(issuer)
    if node:
        if node.get("revoked"):
            raise ValueError("event issuer is revoked")
        return node.get("public_key", "")
    subject = event.get("subject") or {}
    if (
        event.get("kind") == "node_joined"
        and issuer == subject.get("node_id")
        and not state.get("nodes")
    ):
        return subject.get("public_key", "")
    raise ValueError("unknown event issuer")


def apply_event(state, event):
    if event.get("federation_id") != state.get("federation_id"):
        raise ValueError("event belongs to another federation")
    if event.get("kind") not in EVENT_KINDS:
        raise ValueError("unsupported event kind")
    event_id = str(event.get("event_id") or "")
    if event_id in state.setdefault("events", {}):
        return False
    public_key = _issuer_key(state, event)
    verify_payload(public_key, _unsigned(event), str(event.get("signature") or ""))
    expected_id = hashlib.sha256(
        canonical_json({
            "event": _unsigned(event),
            "signature": event.get("signature"),
        }).encode("utf-8")
    ).hexdigest()
    if not secrets.compare_digest(event_id, expected_id):
        raise ValueError("invalid event id")

    kind = event["kind"]
    subject = copy.deepcopy(event.get("subject") or {})
    node_id = str(subject.get("node_id") or "")
    if not node_id:
        raise ValueError("missing subject node id")
    nodes = state.setdefault("nodes", {})
    current = nodes.get(node_id)
    created_at = int(event.get("created_at") or 0)

    if kind == "node_joined":
        if current and current.get("revoked"):
            raise ValueError("node is revoked")
        if not subject.get("public_key"):
            raise ValueError("missing subject public key")
        if current and current.get("public_key") != subject.get("public_key"):
            raise ValueError("node key mismatch")
        subject["revoked"] = False
        subject["_updated_at"] = created_at
        nodes[node_id] = subject
    elif kind == "node_updated":
        if event.get("issuer") != node_id:
            raise ValueError("nodes may only update their own metadata")
        if not current:
            raise ValueError("unknown node")
        if current.get("revoked"):
            raise ValueError("node is revoked")
        if subject.get("public_key") != current.get("public_key"):
            raise ValueError("node key mismatch")
        if created_at < int(current.get("_updated_at") or 0):
            return False
        subject["revoked"] = False
        subject["_updated_at"] = created_at
        nodes[node_id] = subject
    else:
        if not current:
            raise ValueError("unknown node")
        current["revoked"] = True
        current["_revoked_at"] = max(
            created_at,
            int(current.get("_revoked_at") or 0),
        )

    state["events"][event_id] = copy.deepcopy(event)
    return True
