"""HTTP client for signed federation peer calls."""

from __future__ import annotations

import requests

from .crypto import verify_payload
from .protocol import sign_request


class PeerClient:
    def __init__(self, identity, timeout=(3.0, 8.0), session=None):
        self.identity = identity
        self.timeout = timeout
        self.session = session or requests.Session()

    def request(self, node, method, path, body=None):
        headers = sign_request(
            self.identity, node["node_id"], method, path, body,
        )
        url = node["internal_url"].rstrip("/") + path
        response = self.session.request(
            method, url, json=body if body is not None else None,
            headers=headers, timeout=self.timeout, allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise ValueError("peer redirects are not allowed")
        response.raise_for_status()
        value = response.json()
        payload = value.get("payload")
        verify_payload(
            node["public_key"], payload, str(value.get("signature") or ""),
        )
        return payload
