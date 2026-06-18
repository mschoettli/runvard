"""Systemd-Services: auflisten, steuern, Journal-Logs."""
import subprocess


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def list_services():
    """Alle Service-Units mit Status."""
    r = _run(["systemctl", "list-units", "--type=service", "--all",
              "--no-pager", "--no-legend", "--plain"])
    services = []
    for line in r["stdout"].splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        desc = parts[4] if len(parts) > 4 else ""
        if not unit.endswith(".service"):
            continue
        services.append({
            "name": unit,
            "load": load,
            "active": active,
            "sub": sub,
            "description": desc,
        })
    return services


def service_action(name: str, action: str):
    if action not in ("start", "stop", "restart", "enable", "disable"):
        raise ValueError("Unbekannte Aktion")
    return _run(["systemctl", action, name])


def service_status(name: str):
    r = _run(["systemctl", "status", name, "--no-pager", "-l"])
    return {"status": r["stdout"]}


def service_logs(name: str, lines: int = 100):
    r = _run(["journalctl", "-u", name, "-n", str(lines), "--no-pager"])
    return {"logs": r["stdout"]}
