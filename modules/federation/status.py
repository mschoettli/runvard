"""Compact, failure-isolated federation status snapshots."""

from __future__ import annotations

import time


def _safe(call, fallback):
    try:
        return call()
    except Exception:
        return fallback


def build_snapshot(
    *, stats, disks, containers, virtual_machines, app_updates, alerts, version,
    now=None,
):
    host = _safe(stats, {}) or {}
    disk_rows = _safe(disks, []) or []
    root = next(
        (row for row in disk_rows if row.get("mountpoint") == "/"),
        disk_rows[0] if disk_rows else {},
    )
    docker_ok = True
    try:
        container_rows = containers() or []
    except Exception:
        docker_ok = False
        container_rows = []
    vm_ok = True
    try:
        vm_rows = virtual_machines() or []
    except Exception:
        vm_ok = False
        vm_rows = []
    update_data = _safe(app_updates, {}) or {}
    update_rows = update_data.get("updates", []) if isinstance(update_data, dict) else []
    alert_rows = _safe(alerts, []) or []
    version_value = str(_safe(version, "") or "")[:12]
    memory = host.get("memory") or {}
    cpu = host.get("cpu") or {}
    network = host.get("network") or {}
    return {
        "captured_at": int(time.time() if now is None else now),
        "cpu_percent": round(float(cpu.get("percent") or 0), 1),
        "ram_percent": round(float(memory.get("percent") or 0), 1),
        "network_down_rate": round(float(network.get("down_rate") or 0), 1),
        "network_up_rate": round(float(network.get("up_rate") or 0), 1),
        "disk_percent": round(float(root.get("percent") or 0), 1),
        "docker": {
            "total": len(container_rows),
            "running": sum(
                1 for row in container_rows
                if row.get("state") == "running" or row.get("status") == "running"
            ),
            "available": docker_ok,
        },
        "vms": {
            "total": len(vm_rows),
            "running": sum(1 for row in vm_rows if row.get("active")),
            "available": vm_ok,
        },
        "updates": len(update_rows),
        "alerts": sum(1 for row in alert_rows if not row.get("resolved")),
        "version": version_value or "unknown",
        "api_version": 1,
    }


def default_snapshot():
    from modules import apps, docker_mgr, monitoring, system, system_mgr, vms

    return build_snapshot(
        stats=system.get_stats,
        disks=system.get_disk_usage,
        containers=docker_mgr.list_containers,
        virtual_machines=vms.list_vms,
        app_updates=apps._load_updates,
        alerts=monitoring.get_alert_history,
        version=lambda: system_mgr._stored_commit() or "development",
    )
