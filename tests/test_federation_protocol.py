import copy

import pytest

from modules.federation.crypto import create_or_load_identity
from modules.federation.protocol import (
    NonceCache,
    sign_request,
    validate_internal_url,
    verify_request,
)


def test_signed_request_is_accepted_once_and_binds_request_parts(tmp_path):
    sender = create_or_load_identity(tmp_path / "sender")
    target = create_or_load_identity(tmp_path / "target")
    body = {"events": [{"id": "one"}]}
    headers = sign_request(
        sender, target.node_id, "POST", "/api/federation/v1/peer/sync",
        body, now=1000, nonce="unique-nonce",
    )
    cache = NonceCache()

    assert verify_request(
        headers, sender.public_key, target.node_id, "POST",
        "/api/federation/v1/peer/sync", body, cache, now=1000,
    ) == sender.node_id

    with pytest.raises(ValueError, match="replay"):
        verify_request(
            headers, sender.public_key, target.node_id, "POST",
            "/api/federation/v1/peer/sync", body, cache, now=1000,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("method", "GET", "signature"),
        ("path", "/wrong", "signature"),
        ("body", {"events": []}, "signature"),
        ("target", "other-node", "recipient"),
    ],
)
def test_request_tampering_is_rejected(tmp_path, field, value, message):
    sender = create_or_load_identity(tmp_path / "sender")
    target = create_or_load_identity(tmp_path / "target")
    body = {"events": [{"id": "one"}]}
    method = "POST"
    path = "/api/federation/v1/peer/sync"
    headers = sign_request(
        sender, target.node_id, method, path, body, now=1000, nonce="n-1",
    )
    if field == "method":
        method = value
    elif field == "path":
        path = value
    elif field == "body":
        body = value
    else:
        headers = copy.deepcopy(headers)
        headers["X-Runvard-Target"] = value

    with pytest.raises(ValueError, match=message):
        verify_request(
            headers, sender.public_key, target.node_id, method, path, body,
            NonceCache(), now=1000,
        )


def test_clock_skew_and_unknown_sender_are_rejected(tmp_path):
    sender = create_or_load_identity(tmp_path / "sender")
    target = create_or_load_identity(tmp_path / "target")
    headers = sign_request(
        sender, target.node_id, "GET", "/status", None, now=900, nonce="n-2",
    )
    with pytest.raises(ValueError, match="clock"):
        verify_request(
            headers, sender.public_key, target.node_id, "GET", "/status",
            None, NonceCache(), now=1000,
        )

    altered = dict(headers)
    altered["X-Runvard-Node"] = "unknown"
    with pytest.raises(ValueError, match="sender"):
        verify_request(
            altered, sender.public_key, target.node_id, "GET", "/status",
            None, NonceCache(), now=900,
        )


def test_internal_urls_must_be_literal_addresses_in_allowed_networks():
    allowed = ["10.0.0.0/8", "fd00::/8", "127.0.0.0/8"]
    assert validate_internal_url("http://10.0.0.4:8765", allowed) == \
        "http://10.0.0.4:8765"
    assert validate_internal_url("https://[fd00::2]:9443", allowed) == \
        "https://[fd00::2]:9443"

    for value in (
        "http://server.local:8765",
        "http://8.8.8.8:8765",
        "ftp://10.0.0.4",
        "http://user:pass@10.0.0.4",
        "http://10.0.0.4/path",
    ):
        with pytest.raises(ValueError):
            validate_internal_url(value, allowed)
