from urllib.parse import urlsplit

from modules.federation.service import (
    PAIR_PATH, REDEEM_PATH, SYNC_PATH, FederationManager,
)


def _snapshot():
    return {
        "api_version": 1, "version": "mesh-test",
        "cpu_percent": 10, "ram_percent": 20, "disk_percent": 30,
        "docker": {"running": 1, "total": 2, "available": True},
        "vms": {"running": 1, "total": 1, "available": True},
        "updates": 0, "alerts": 0,
    }


class _Response:
    def __init__(self, value, status=200):
        self.value = value
        self.status_code = status

    def json(self):
        return self.value

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _MeshSession:
    def __init__(self):
        self.nodes = {}

    def add(self, origin, manager):
        self.nodes[origin] = manager

    def post(self, url, json, **kwargs):
        return self.request("POST", url, json=json, headers={}, **kwargs)

    def request(self, method, url, json=None, headers=None, **kwargs):
        parsed = urlsplit(url)
        manager = self.nodes[f"{parsed.scheme}://{parsed.netloc}"]
        if parsed.path == PAIR_PATH:
            return _Response(manager.accept_pair(json, "10.0.0.99"))
        sender = manager.authenticate_peer(
            headers, method, parsed.path, json,
        )
        if parsed.path == SYNC_PATH:
            value = manager.accept_sync(sender, json)
        elif parsed.path == REDEEM_PATH:
            value = manager.redeem_sso(json)
        else:
            raise AssertionError(parsed.path)
        return _Response(manager.signed_response(value))


def test_three_node_transitive_pairing_status_and_bidirectional_sso(tmp_path):
    mesh = _MeshSession()
    a = FederationManager(tmp_path / "a", _snapshot, mesh)
    b = FederationManager(tmp_path / "b", _snapshot, mesh)
    c = FederationManager(tmp_path / "c", _snapshot, mesh)
    mesh.add("http://10.0.0.1:8080", a)
    mesh.add("http://10.0.0.2:8080", b)
    mesh.add("http://10.0.0.3:8080", c)
    a.enable(
        "A", "http://10.0.0.1:8080", "https://a.example.test",
    )
    b.join(
        "http://10.0.0.1:8080", a.issue_pairing_code(), "B",
        "http://10.0.0.2:8080", "https://b.example.test",
    )
    c.join(
        "http://10.0.0.2:8080", b.issue_pairing_code(), "C",
        "http://10.0.0.3:8080", "https://c.example.test",
    )

    a.refresh()
    a.refresh()
    assert a.overview()["total"] == 3
    assert a.overview()["online"] == 3

    _, a_to_c = a.start_sso(c.identity.node_id, "alice", "admin", True)
    claim = c.accept_sso(a_to_c)
    assert (claim["role"], claim["expert"]) == ("admin", True)

    c.refresh()
    _, c_to_a = c.start_sso(a.identity.node_id, "reader", "readonly", True)
    claim = a.accept_sso(c_to_a)
    assert (claim["role"], claim["expert"]) == ("readonly", False)
