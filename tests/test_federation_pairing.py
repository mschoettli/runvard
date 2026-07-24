import pytest

from modules.federation.pairing import PairingCodes


def test_pairing_code_is_hashed_single_use_and_expires():
    values = {}
    codes = PairingCodes(values)
    code = codes.issue(now=100)

    assert len(code) >= 22
    assert code not in str(values)
    assert codes.consume(code, "10.0.0.2", now=699) is True
    with pytest.raises(ValueError, match="invalid|used"):
        codes.consume(code, "10.0.0.2", now=699)

    expired = codes.issue(now=100)
    with pytest.raises(ValueError, match="expired"):
        codes.consume(expired, "10.0.0.2", now=701)


def test_wrong_pairing_codes_are_rate_limited():
    codes = PairingCodes({}, max_failures=3)
    for _ in range(3):
        with pytest.raises(ValueError, match="invalid"):
            codes.consume("wrong", "10.0.0.8", now=100)
    with pytest.raises(ValueError, match="rate"):
        codes.consume("wrong", "10.0.0.8", now=100)
