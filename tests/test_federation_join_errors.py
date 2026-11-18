import requests

import pytest

from modules.federation.service import FederationManager


class RejectingSession:
    def post(self, url, **kwargs):
        response = requests.Response()
        response.status_code = 401
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"detail":"invalid pairing code"}'
        return response


def test_join_preserves_the_peer_rejection_reason(tmp_path):
    manager = FederationManager(tmp_path, session=RejectingSession())

    with pytest.raises(ValueError, match="invalid pairing code"):
        manager.join(
            "http://10.0.0.1:8080", "wrong", "B",
            "http://10.0.0.2:8080", "https://b.example.test",
            peer_fingerprint="SHA256:expected",
        )
