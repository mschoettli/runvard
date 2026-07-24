from modules.federation.status import build_snapshot


def test_snapshot_is_bounded_and_subsystem_failures_are_isolated():
    def fail():
        raise RuntimeError("/secret/path should not leak")

    snapshot = build_snapshot(
        stats=lambda: {
            "cpu": {"percent": 12.3},
            "memory": {"percent": 45.6},
        },
        disks=lambda: [{"mountpoint": "/", "percent": 67.8}],
        containers=fail,
        virtual_machines=lambda: [
            {"active": True}, {"active": False}, {"active": True},
        ],
        app_updates=lambda: {"updates": ["one", "two"]},
        alerts=lambda: [{"resolved": False}, {"resolved": True}],
        version=lambda: "abcdef0123456789",
        now=100,
    )

    assert snapshot["cpu_percent"] == 12.3
    assert snapshot["ram_percent"] == 45.6
    assert snapshot["disk_percent"] == 67.8
    assert snapshot["docker"] == {"total": 0, "running": 0, "available": False}
    assert snapshot["vms"] == {"total": 3, "running": 2, "available": True}
    assert snapshot["updates"] == 2
    assert snapshot["alerts"] == 1
    assert snapshot["version"] == "abcdef012345"
    assert "/secret" not in str(snapshot)
