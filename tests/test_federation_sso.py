import pytest

from modules.federation.crypto import create_or_load_identity
from modules.federation.sso import issue_ticket, redeem_ticket, validate_ticket


def test_ticket_preserves_role_and_expert_and_is_single_use(tmp_path):
    source = create_or_load_identity(tmp_path / "source")
    target = create_or_load_identity(tmp_path / "target")
    pending = {}
    ticket = issue_ticket(
        source, target.node_id, "fed-one", "alice", "admin", True,
        pending, now=100,
    )
    claim = validate_ticket(
        ticket, source.public_key, target.node_id, "fed-one", now=120,
    )
    assert claim["username"] == "alice"
    assert claim["role"] == "admin"
    assert claim["expert"] is True
    assert redeem_ticket(ticket, pending, now=120) is True
    with pytest.raises(ValueError, match="redeemed"):
        redeem_ticket(ticket, pending, now=120)


def test_readonly_never_receives_expert_mode(tmp_path):
    source = create_or_load_identity(tmp_path / "source")
    target = create_or_load_identity(tmp_path / "target")
    ticket = issue_ticket(
        source, target.node_id, "fed-one", "reader", "readonly", True,
        {}, now=100,
    )
    claim = validate_ticket(
        ticket, source.public_key, target.node_id, "fed-one", now=101,
    )
    assert claim["role"] == "readonly"
    assert claim["expert"] is False


def test_wrong_audience_federation_and_expiry_fail_closed(tmp_path):
    source = create_or_load_identity(tmp_path / "source")
    target = create_or_load_identity(tmp_path / "target")
    pending = {}
    ticket = issue_ticket(
        source, target.node_id, "fed-one", "alice", "admin", False,
        pending, now=100,
    )
    with pytest.raises(ValueError, match="audience"):
        validate_ticket(ticket, source.public_key, "wrong", "fed-one", now=101)
    with pytest.raises(ValueError, match="federation"):
        validate_ticket(ticket, source.public_key, target.node_id, "fed-two", now=101)
    with pytest.raises(ValueError, match="expired"):
        validate_ticket(ticket, source.public_key, target.node_id, "fed-one", now=161)
    with pytest.raises(ValueError, match="expired"):
        redeem_ticket(ticket, pending, now=161)
