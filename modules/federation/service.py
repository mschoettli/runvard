"""Federation orchestration: membership, pairing, polling and SSO."""

from __future__ import annotations

import copy
import secrets
import socket
import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

import requests

from .client import PeerClient
from .crypto import (
    create_or_load_identity, node_id_from_public_key, sign_payload,
    verify_payload,
)
from .membership import apply_event, create_event
from .pairing import PairingCodes
from .protocol import NonceCache, validate_internal_url, verify_request
from .sso import (
    issue_ticket, redeem_ticket, ticket_claim, validate_ticket,
)
from .status import default_snapshot
from .storage import FederationStore


SYNC_PATH = "/api/federation/v1/peer/sync"
STATUS_PATH = "/api/federation/v1/peer/status"
REDEEM_PATH = "/api/federation/v1/peer/sso/redeem"
PAIR_PATH = "/api/federation/v1/peer/pair"
MAX_NODES = 20


def validate_browser_url(value):
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("browser URL must be an HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("browser URL must not include credentials, query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("browser URL must not include a path")
    hostname = parsed.hostname or ""
    if ":" not in hostname and not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname,
    ):
        raise ValueError("browser URL contains an invalid hostname")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("browser URL contains an invalid port") from exc
    return str(value).strip().rstrip("/")


class FederationManager:
    def __init__(self, data_dir, snapshot_provider=default_snapshot, session=None):
        self.store = FederationStore(data_dir)
        self.state = self.store.load()
        self.identity = None
        self.snapshot_provider = snapshot_provider
        self.session = session or requests.Session()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.local_snapshot = {}
        self._snapshot_at = 0
        if self.state.get("enabled"):
            self.identity = create_or_load_identity(self.store.data_dir)

    def _save(self):
        self.store.save(self.state)

    def _ensure_identity(self):
        if self.identity is None:
            self.identity = create_or_load_identity(self.store.data_dir)
        return self.identity

    def _metadata(self, name=None, internal_url=None, browser_url=None):
        identity = self._ensure_identity()
        settings = self.state["settings"]
        return {
            "node_id": identity.node_id,
            "public_key": identity.public_key,
            "name": str(name if name is not None else settings.get("name") or socket.gethostname())[:80],
            "hostname": socket.gethostname()[:255],
            "internal_url": internal_url if internal_url is not None else settings.get("internal_url", ""),
            "browser_url": browser_url if browser_url is not None else settings.get("browser_url", ""),
            "api_version": 1,
            "runvard_version": (self.local_snapshot.get("version") or "unknown")[:32],
        }

    def enable(self, name, internal_url, browser_url, allowed_cidrs=None):
        with self.lock:
            if self.state.get("enabled"):
                return self.overview()
            if allowed_cidrs:
                self.state["settings"]["allowed_cidrs"] = list(allowed_cidrs)
            internal_url = validate_internal_url(
                internal_url, self.state["settings"]["allowed_cidrs"],
            )
            browser_url = validate_browser_url(browser_url)
            self.state["settings"].update({
                "name": str(name)[:80],
                "internal_url": internal_url,
                "browser_url": browser_url,
            })
            self.state["enabled"] = True
            self.state["federation_id"] = secrets.token_hex(16)
            identity = self._ensure_identity()
            event = create_event(
                identity, self.state["federation_id"], "node_joined",
                self._metadata(),
            )
            apply_event(self.state, event)
            self._save()
            return self.overview()

    def update_settings(self, name, internal_url, browser_url, allowed_cidrs=None):
        with self.lock:
            self._require_enabled()
            if allowed_cidrs:
                self.state["settings"]["allowed_cidrs"] = list(allowed_cidrs)
            internal_url = validate_internal_url(
                internal_url, self.state["settings"]["allowed_cidrs"],
            )
            browser_url = validate_browser_url(browser_url)
            self.state["settings"].update({
                "name": str(name)[:80],
                "internal_url": internal_url,
                "browser_url": browser_url,
            })
            event = create_event(
                self.identity, self.state["federation_id"], "node_updated",
                self._metadata(),
            )
            apply_event(self.state, event)
            self._save()
            return self.overview()

    def issue_pairing_code(self):
        with self.lock:
            self._require_enabled()
            code = PairingCodes(self.state["pairing_codes"]).issue()
            self._save()
            return code

    def pairing_payload(self, code, name, internal_url, browser_url):
        identity = self._ensure_identity()
        node = self._metadata(name, internal_url, browser_url)
        proof = {"code": code, "node": node}
        return {**proof, "proof": sign_payload(identity, proof)}

    def accept_pair(self, payload, remote):
        with self.lock:
            self._require_enabled()
            node = copy.deepcopy(payload.get("node") or {})
            proof_payload = {"code": payload.get("code"), "node": node}
            verify_payload(
                str(node.get("public_key") or ""), proof_payload,
                str(payload.get("proof") or ""),
            )
            if len([n for n in self.state["nodes"].values() if not n.get("revoked")]) >= MAX_NODES:
                raise ValueError("federation node limit reached")
            if node.get("node_id") != node_id_from_public_key(
                str(node.get("public_key") or ""),
            ):
                raise ValueError("joining node ID does not match its public key")
            validate_internal_url(
                node.get("internal_url"), self.state["settings"]["allowed_cidrs"],
            )
            validate_browser_url(node.get("browser_url"))
            PairingCodes(
                self.state["pairing_codes"],
                self.state["pairing_failures"],
            ).consume(
                str(payload.get("code") or ""), remote,
            )
            event = create_event(
                self.identity, self.state["federation_id"], "node_joined", node,
            )
            apply_event(self.state, event)
            response = {
                "federation_id": self.state["federation_id"],
                "events": list(self.state["events"].values()),
                "responder": self.identity.node_id,
                "responder_public_key": self.identity.public_key,
            }
            response["signature"] = sign_payload(self.identity, response)
            self._save()
            return response

    def join(
        self, peer_url, code, name, internal_url, browser_url,
        allowed_cidrs=None,
    ):
        with self.lock:
            if self.state.get("enabled") and self.state.get("federation_id"):
                raise ValueError("this node already belongs to a federation")
            if allowed_cidrs:
                self.state["settings"]["allowed_cidrs"] = list(allowed_cidrs)
            peer_url = validate_internal_url(
                peer_url, self.state["settings"]["allowed_cidrs"],
            )
            internal_url = validate_internal_url(
                internal_url, self.state["settings"]["allowed_cidrs"],
            )
            browser_url = validate_browser_url(browser_url)
            payload = self.pairing_payload(
                code, name, internal_url, browser_url,
            )
        response = self.session.post(
            peer_url + PAIR_PATH, json=payload, timeout=(3, 10),
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise ValueError("peer redirects are not allowed")
        response.raise_for_status()
        result = response.json()
        signature = result.pop("signature", "")
        verify_payload(result.get("responder_public_key", ""), result, signature)
        with self.lock:
            self.state["enabled"] = True
            self.state["federation_id"] = result["federation_id"]
            self.state["settings"].update({
                "name": str(name)[:80],
                "internal_url": internal_url,
                "browser_url": browser_url,
            })
            for event in sorted(result.get("events", []), key=lambda row: row.get("created_at", 0)):
                apply_event(self.state, event)
            if self.identity.node_id not in self.state["nodes"]:
                raise ValueError("pairing response omitted joining node")
            self._save()
            return self.overview()

    def authenticate_peer(self, headers, method, path, body):
        with self.lock:
            self._require_enabled()
            sender = str(headers.get("X-Runvard-Node") or "")
            node = self.state["nodes"].get(sender)
            if not node or node.get("revoked"):
                raise ValueError("unknown or revoked peer")
            cache = NonceCache(self.state["nonces"])
            verified = verify_request(
                headers, node["public_key"], self.identity.node_id,
                method, path, body, cache,
            )
            self._save()
            return verified

    def accept_sync(self, sender, body):
        with self.lock:
            for event in sorted(body.get("events", []), key=lambda row: row.get("created_at", 0)):
                try:
                    apply_event(self.state, event)
                except ValueError:
                    continue
            self._save()
            return {
                "events": list(self.state["events"].values()),
                "status": self.snapshot(),
            }

    def signed_response(self, payload):
        return {
            "payload": payload,
            "signature": sign_payload(self.identity, payload),
        }

    def snapshot(self, force=False):
        now = time.time()
        if self.local_snapshot and not force and now - self._snapshot_at < 12:
            return copy.deepcopy(self.local_snapshot)
        try:
            self.local_snapshot = self.snapshot_provider()
            self._snapshot_at = now
        except Exception:
            if not self.local_snapshot:
                self.local_snapshot = {"api_version": 1, "version": "unknown"}
        return copy.deepcopy(self.local_snapshot)

    def _peer_client(self):
        return PeerClient(self.identity, session=self.session)

    def refresh(self):
        with self.lock:
            if not self.state.get("enabled"):
                return self.overview()
            nodes = [
                copy.deepcopy(node) for node in self.state["nodes"].values()
                if node["node_id"] != self.identity.node_id and not node.get("revoked")
            ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(self._poll_one, node) for node in nodes]
            for job in as_completed(jobs):
                try:
                    job.result()
                except Exception:
                    pass
        return self.overview()

    def _poll_one(self, node):
        now = int(time.time())
        try:
            body = {"events": list(self.state["events"].values())}
            result = self._peer_client().request(node, "POST", SYNC_PATH, body)
            with self.lock:
                for event in sorted(result.get("events", []), key=lambda row: row.get("created_at", 0)):
                    try:
                        apply_event(self.state, event)
                    except ValueError:
                        continue
                status = result.get("status") or {}
                health = "online" if int(status.get("api_version") or 0) == 1 else "incompatible"
                self.state["peer_status"][node["node_id"]] = {
                    "health": health, "last_success": now, "last_attempt": now,
                    "failures": 0, "snapshot": status,
                }
                self._save()
        except Exception:
            with self.lock:
                entry = self.state["peer_status"].setdefault(node["node_id"], {})
                failures = int(entry.get("failures") or 0) + 1
                last_success = int(entry.get("last_success") or 0)
                entry.update({
                    "failures": failures, "last_attempt": now,
                    "health": "offline" if failures >= 3 or now - last_success >= 45 else "degraded",
                })
                self._save()
            raise

    def revoke(self, node_id):
        with self.lock:
            self._require_enabled()
            if node_id == self.identity.node_id:
                raise ValueError("cannot revoke the current node")
            event = create_event(
                self.identity, self.state["federation_id"], "node_revoked",
                {"node_id": node_id},
            )
            apply_event(self.state, event)
            self._save()
            return self.overview()

    def start_sso(self, node_id, username, role, expert):
        with self.lock:
            self._require_enabled()
            node = self.state["nodes"].get(node_id)
            if not node or node.get("revoked"):
                raise ValueError("unknown target node")
            health = self.state["peer_status"].get(node_id, {}).get("health")
            if health in {"offline", "incompatible"}:
                raise ValueError("target node is not available")
            ticket = issue_ticket(
                self.identity, node_id, self.state["federation_id"],
                username, role, expert, self.state["tickets"],
            )
            self._save()
            return node["browser_url"], ticket

    def accept_sso(self, ticket):
        untrusted = ticket_claim(ticket)
        with self.lock:
            self._require_enabled()
            issuer = self.state["nodes"].get(untrusted.get("issuer"))
            if not issuer or issuer.get("revoked"):
                raise ValueError("unknown SSO issuer")
            claim = validate_ticket(
                ticket, issuer["public_key"], self.identity.node_id,
                self.state["federation_id"],
            )
        self._peer_client().request(
            issuer, "POST", REDEEM_PATH, {"ticket": ticket},
        )
        return claim

    def redeem_sso(self, body):
        with self.lock:
            redeem_ticket(str(body.get("ticket") or ""), self.state["tickets"])
            self._save()
            return {"ok": True}

    def overview(self):
        with self.lock:
            if not self.state.get("enabled"):
                return {"enabled": False, "nodes": [], "online": 0, "total": 0}
            rows = []
            for node in self.state["nodes"].values():
                if node.get("revoked"):
                    continue
                current = node["node_id"] == self.identity.node_id
                peer = self.state["peer_status"].get(node["node_id"], {})
                snapshot = self.snapshot() if current else peer.get("snapshot", {})
                health = "online" if current else peer.get("health", "unknown")
                rows.append({
                    **{k: v for k, v in node.items() if not k.startswith("_") and k != "public_key"},
                    "current": current,
                    "health": health,
                    "last_contact": int(time.time()) if current else peer.get("last_success"),
                    "snapshot": snapshot,
                })
            rows.sort(key=lambda row: (not row["current"], row.get("name", "").lower()))
            return {
                "enabled": True,
                "federation_id": self.state["federation_id"],
                "node_id": self.identity.node_id,
                "settings": copy.deepcopy(self.state["settings"]),
                "nodes": rows,
                "online": sum(1 for row in rows if row["health"] == "online"),
                "total": len(rows),
            }

    def _require_enabled(self):
        if not self.state.get("enabled") or not self.state.get("federation_id"):
            raise ValueError("federation is disabled")

    def start(self, interval=15):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()

        def run():
            while not self.stop_event.wait(1):
                self.snapshot(force=True)
                self.refresh()
                if self.stop_event.wait(max(1, interval - 1)):
                    break

        self.worker = threading.Thread(
            target=run, name="runvard-federation", daemon=True,
        )
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=2)
