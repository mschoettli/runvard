import json
import os

import pytest

from modules.federation.crypto import (
    canonical_json,
    create_or_load_identity,
    sign_payload,
    verify_payload,
)


def test_identity_is_stable_and_private_key_is_restricted(tmp_path):
    first = create_or_load_identity(tmp_path)
    second = create_or_load_identity(tmp_path)

    assert first.node_id == second.node_id
    assert first.public_key == second.public_key
    assert first.signing_key == second.signing_key
    assert len(first.node_id) == 32
    assert oct(os.stat(tmp_path / "identity.key").st_mode & 0o777) == "0o600"


def test_canonical_json_is_order_independent():
    left = canonical_json({"b": 2, "a": {"z": 1, "x": "ok"}})
    right = canonical_json({"a": {"x": "ok", "z": 1}, "b": 2})

    assert left == right
    assert json.loads(left) == {"a": {"x": "ok", "z": 1}, "b": 2}


def test_signed_payload_rejects_tampering(tmp_path):
    identity = create_or_load_identity(tmp_path)
    payload = {"node_id": identity.node_id, "status": "online"}
    signature = sign_payload(identity, payload)

    assert verify_payload(identity.public_key, payload, signature) is True

    with pytest.raises(ValueError, match="signature"):
        verify_payload(
            identity.public_key,
            {"node_id": identity.node_id, "status": "offline"},
            signature,
        )
