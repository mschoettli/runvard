from fastapi.testclient import TestClient

import server


def _client(role="admin"):
    client = TestClient(server.app)
    client.cookies.set(
        server.COOKIE_NAME, server.make_token("tester", 3600, role),
    )
    return client


def _confirm(client, action, target):
    return client.post(
        "/api/confirm-token", data={"action": action, "target": target},
    ).json()["token"]


def test_time_machine_overview_is_available_to_readonly(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.time_machine,
        "health_check",
        lambda: {"targets": [{"id": "a" * 16, "status": "active"}], "checked_at": 1},
    )
    monkeypatch.setattr(
        server.time_machine,
        "list_protection_points",
        lambda: [{"id": "b" * 16, "target_id": "a" * 16}],
    )

    response = _client("readonly").get("/api/time-machine/overview")

    assert response.status_code == 200
    assert response.json()["targets"][0]["status"] == "active"
    assert len(response.json()["protection_points"]) == 1


def test_create_requires_admin_and_never_returns_password(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "target": {"id": "a" * 16, "owner": kwargs["owner"]}}

    monkeypatch.setattr(server.time_machine, "create_target", fake_create)
    data = {
        "display_name": "MacBook Maria",
        "owner": "tm-maria",
        "storage_root": "/srv/backups",
        "capacity_gb": "1000",
        "source_capacity_gb": "500",
        "password": "correct-horse-battery",
        "create_account": "true",
        "client_encryption_required": "true",
    }

    assert _client("readonly").post(
        "/api/time-machine/targets", data=data,
    ).status_code == 403
    response = _client().post("/api/time-machine/targets", data=data)

    assert response.status_code == 200
    assert captured["password"] == "correct-horse-battery"
    assert "password" not in response.text


def test_create_rejects_missing_client_encryption_confirmation(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    called = []
    monkeypatch.setattr(
        server.time_machine, "create_target",
        lambda **kwargs: called.append(kwargs) or {"ok": True},
    )

    response = _client().post("/api/time-machine/targets", data={
        "display_name": "MacBook", "owner": "tm-maria",
        "storage_root": "/srv/backups", "capacity_gb": "500",
        "password": "correct-horse-battery",
    })

    assert response.status_code == 400
    assert called == []


def test_permanent_delete_requires_matching_danger_confirmation(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    deleted = []
    monkeypatch.setattr(
        server.time_machine,
        "delete_target_data",
        lambda target_id: deleted.append(target_id) or {"ok": True},
    )
    admin = _client()
    target_id = "a" * 16

    assert admin.post(
        "/api/time-machine/targets/delete-data", data={"target_id": target_id},
    ).status_code == 403
    token = _confirm(admin, "time-machine:delete-data", target_id)
    response = admin.post(
        "/api/time-machine/targets/delete-data",
        data={"target_id": target_id, "confirm_token": token},
    )

    assert response.status_code == 200
    assert deleted == [target_id]


def test_target_lifecycle_and_protection_routes(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(
        server.time_machine, "set_target_enabled",
        lambda target_id, enabled: calls.append(("enabled", target_id, enabled)) or {"ok": True},
    )
    monkeypatch.setattr(
        server.time_machine, "remove_target",
        lambda target_id, actor="system": calls.append(("remove", target_id, actor))
        or {"ok": True},
    )
    monkeypatch.setattr(
        server.time_machine, "create_protection_point",
        lambda target_id, kind="manual": calls.append(("protect", target_id, kind)) or {"ok": True},
    )
    admin = _client()
    target_id = "a" * 16

    assert admin.post(
        "/api/time-machine/targets/enabled",
        data={"target_id": target_id, "enabled": "false"},
    ).status_code == 200
    assert admin.post(
        "/api/time-machine/protection-points",
        data={"target_id": target_id, "kind": "manual"},
    ).status_code == 200
    token = _confirm(admin, "time-machine:remove", target_id)
    assert admin.post(
        "/api/time-machine/targets/remove",
        data={"target_id": target_id, "confirm_token": token},
    ).status_code == 200
    assert calls == [
        ("enabled", target_id, False),
        ("protect", target_id, "manual"),
        ("remove", target_id, "tester"),
    ]


def test_target_policy_route_requires_admin(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    captured = []
    monkeypatch.setattr(
        server.time_machine, "update_target_policy",
        lambda target_id, **kwargs: captured.append((target_id, kwargs)) or {"ok": True},
        raising=False,
    )
    data = {
        "target_id": "a" * 16, "capacity_gb": "800",
        "daily": "14", "weekly": "8", "monthly": "6",
    }

    assert _client("readonly").post(
        "/api/time-machine/targets/policy", data=data,
    ).status_code == 403
    assert _client().post(
        "/api/time-machine/targets/policy", data=data,
    ).status_code == 200
    assert captured == [("a" * 16, {
        "capacity_gb": 800, "daily": 14, "weekly": 8, "monthly": 6,
        "actor": "tester",
    })]


def test_replication_routes_are_admin_mutations_and_readonly_can_view(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.time_machine, "list_replications",
        lambda target_id=None: [{"id": "b" * 16, "target_id": "a" * 16, "status": "passive"}],
    )
    captured = []
    monkeypatch.setattr(
        server.time_machine, "create_replication",
        lambda **kwargs: captured.append(kwargs) or {"ok": True, "replication": {"id": "b" * 16}},
    )
    monkeypatch.setattr(
        server.time_machine, "queue_replication",
        lambda replication_id: {"ok": True, "job": {"id": "c" * 16, "status": "queued"}},
        raising=False,
    )

    assert _client("readonly").get(
        "/api/time-machine/replications",
    ).status_code == 200
    data = {
        "target_id": "a" * 16,
        "destination_root": "/mnt/replicas",
        "schedule_hour": "2",
        "bandwidth_mbps": "100",
    }
    assert _client("readonly").post(
        "/api/time-machine/replications", data=data,
    ).status_code == 403
    assert _client().post(
        "/api/time-machine/replications", data=data,
    ).status_code == 200
    response = _client().post(
        "/api/time-machine/replications/run", data={"replication_id": "b" * 16},
    )
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"
    assert captured[0]["destination_root"] == "/mnt/replicas"


def test_replication_policy_route_requires_admin(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    captured = []
    monkeypatch.setattr(
        server.time_machine, "update_replication_policy",
        lambda replication_id, **kwargs: captured.append((replication_id, kwargs))
        or {"ok": True}, raising=False,
    )
    data = {
        "replication_id": "b" * 16, "schedule_hour": "6",
        "bandwidth_mbps": "100", "enabled": "false",
    }

    assert _client("readonly").post(
        "/api/time-machine/replications/policy", data=data,
    ).status_code == 403
    assert _client().post(
        "/api/time-machine/replications/policy", data=data,
    ).status_code == 200
    assert captured == [("b" * 16, {
        "schedule_hour": 6, "bandwidth_mbps": 100, "enabled": False,
        "actor": "tester",
    })]


def test_time_machine_events_are_available_to_readonly(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.time_machine, "list_events",
        lambda limit=50: [{
            "id": "d" * 16, "type": "target_policy_updated", "actor": "alice",
        }], raising=False,
    )

    response = _client("readonly").get("/api/time-machine/events?limit=10")

    assert response.status_code == 200
    assert response.json()["events"][0]["actor"] == "alice"


def test_replica_promotion_requires_danger_confirmation(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    promoted = []
    monkeypatch.setattr(
        server.time_machine, "promote_replica",
        lambda replication_id, source_unavailable_confirmed: promoted.append(replication_id) or {
            "ok": True, "target": {"id": "a" * 16, "verification_required": True},
        },
    )
    admin = _client()
    replica_id = "b" * 16
    data = {"replication_id": replica_id, "source_unavailable_confirmed": "true"}

    assert admin.post(
        "/api/time-machine/replications/promote", data=data,
    ).status_code == 403
    data["confirm_token"] = _confirm(admin, "time-machine:promote-replica", replica_id)
    response = admin.post("/api/time-machine/replications/promote", data=data)

    assert response.status_code == 200
    assert response.json()["target"]["verification_required"] is True
    assert promoted == [replica_id]


def test_received_replica_import_requires_danger_confirmation(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    imported = []
    monkeypatch.setattr(
        server.time_machine, "promote_received_replica",
        lambda **kwargs: imported.append(kwargs) or {
            "ok": True, "target": {"id": "a" * 16, "verification_required": True},
        },
        raising=False,
    )
    admin = _client()
    source_id = "c" * 16
    data = {
        "source_replication_id": source_id, "version": "1000",
        "display_name": "Remote Mac", "owner": "tm-maria",
        "storage_root": "/srv/runvard-replicas", "capacity_gb": "500",
        "password": "correct-horse-battery", "create_account": "true",
        "source_unavailable_confirmed": "true",
        "client_encryption_required": "true",
    }

    assert admin.post(
        "/api/time-machine/replications/import", data=data,
    ).status_code == 403
    data["confirm_token"] = _confirm(
        admin, "time-machine:promote-received", source_id,
    )
    response = admin.post("/api/time-machine/replications/import", data=data)

    assert response.status_code == 200
    assert imported[0]["password"] == "correct-horse-battery"
    assert "password" not in response.text


def test_received_replica_import_rejects_missing_encryption_confirmation(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    called = []
    monkeypatch.setattr(
        server.time_machine, "promote_received_replica",
        lambda **kwargs: called.append(kwargs) or {"ok": True},
    )
    admin = _client()
    source_id = "c" * 16
    data = {
        "source_replication_id": source_id, "version": "1000",
        "display_name": "Remote Mac", "owner": "tm-maria",
        "storage_root": "/srv/runvard-replicas", "capacity_gb": "500",
        "password": "correct-horse-battery",
        "source_unavailable_confirmed": "true",
        "confirm_token": _confirm(admin, "time-machine:promote-received", source_id),
    }

    response = admin.post("/api/time-machine/replications/import", data=data)

    assert response.status_code == 400
    assert called == []


def test_replication_key_onboarding_requires_admin(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.time_machine, "ensure_replication_identity",
        lambda: {"public_key": "ssh-ed25519 AAAA", "authorized_keys_line": "restrict ssh-ed25519 AAAA"},
    )
    monkeypatch.setattr(
        server.time_machine, "register_replication_client_key",
        lambda public_key: {"ok": True, "key_type": "ssh-ed25519"},
        raising=False,
    )

    assert _client("readonly").post(
        "/api/time-machine/replications/identity",
    ).status_code == 403
    assert _client().post(
        "/api/time-machine/replications/identity",
    ).status_code == 200
    assert _client().post(
        "/api/time-machine/replications/client-key",
        data={"public_key": "ssh-ed25519 AAAA"},
    ).status_code == 200


def test_managed_config_reconciliation_requires_admin(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(
        server.time_machine, "reconcile_managed_config",
        lambda: calls.append("reconcile") or {"ok": True, "drift": False},
        raising=False,
    )

    assert _client("readonly").post(
        "/api/time-machine/system/reconcile",
    ).status_code == 403
    assert _client().post(
        "/api/time-machine/system/reconcile",
    ).status_code == 200
    assert calls == ["reconcile"]


def test_time_machine_jobs_are_visible_to_readonly(monkeypatch):
    monkeypatch.setattr(server, "login_enabled", lambda: True)
    monkeypatch.setattr(
        server.time_machine, "list_jobs",
        lambda limit=50: [{"id": "d" * 16, "type": "replication", "status": "queued"}],
        raising=False,
    )

    response = _client("readonly").get("/api/time-machine/jobs?limit=10")

    assert response.status_code == 200
    assert response.json()["jobs"][0]["status"] == "queued"
