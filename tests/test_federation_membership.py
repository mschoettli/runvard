import copy

import pytest

from modules.federation.crypto import create_or_load_identity
from modules.federation.membership import (
    apply_event,
    create_event,
    empty_membership,
)
from modules.federation.storage import FederationStore


def _node(identity, name, port):
    return {
        "node_id": identity.node_id,
        "public_key": identity.public_key,
        "name": name,
        "hostname": name.lower(),
        "internal_url": f"http://127.0.0.1:{port}",
        "browser_url": f"http://127.0.0.1:{port}",
        "api_version": 1,
        "runvard_version": "test",
    }


def test_store_round_trips_state_and_keeps_private_permissions(tmp_path):
    store = FederationStore(tmp_path)
    state = store.load()
    state["enabled"] = True
    state["federation_id"] = "fed-test"

    store.save(state)

    assert FederationStore(tmp_path).load()["federation_id"] == "fed-test"
    assert oct((tmp_path / "state.json").stat().st_mode & 0o777) == "0o600"


def test_join_update_and_revoke_events_converge(tmp_path):
    a = create_or_load_identity(tmp_path / "a")
    b = create_or_load_identity(tmp_path / "b")
    state = empty_membership("fed-test")

    self_join = create_event(a, "fed-test", "node_joined", _node(a, "A", 8101), now=10)
    assert apply_event(state, self_join) is True

    join_b = create_event(a, "fed-test", "node_joined", _node(b, "B", 8102), now=20)
    assert apply_event(state, join_b) is True
    assert state["nodes"][b.node_id]["name"] == "B"

    assert apply_event(state, join_b) is False

    updated_b = _node(b, "B new", 8202)
    update = create_event(b, "fed-test", "node_updated", updated_b, now=30)
    assert apply_event(state, update) is True
    assert state["nodes"][b.node_id]["browser_url"].endswith(":8202")

    revoke = create_event(
        a,
        "fed-test",
        "node_revoked",
        {"node_id": b.node_id},
        now=40,
    )
    assert apply_event(state, revoke) is True
    assert state["nodes"][b.node_id]["revoked"] is True

    later_update = create_event(b, "fed-test", "node_updated", _node(b, "B bad", 9999), now=50)
    with pytest.raises(ValueError, match="revoked"):
        apply_event(state, later_update)


def test_event_signature_and_federation_are_validated(tmp_path):
    identity = create_or_load_identity(tmp_path)
    state = empty_membership("fed-one")
    event = create_event(identity, "fed-one", "node_joined", _node(identity, "A", 8101))

    tampered = copy.deepcopy(event)
    tampered["subject"]["name"] = "Mallory"
    with pytest.raises(ValueError, match="signature"):
        apply_event(state, tampered)

    wrong_federation = create_event(
        identity,
        "fed-two",
        "node_joined",
        _node(identity, "A", 8101),
    )
    with pytest.raises(ValueError, match="federation"):
        apply_event(state, wrong_federation)
