"""Monitoring: System-Logs, Alerts, Benachrichtigungen."""
import os
import json
import time
import subprocess

ALERT_CONFIG = "/opt/runvard/data/alerts.json"
ALERT_HISTORY = "/opt/runvard/data/alert_history.json"


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception as e:
        return f"Fehler: {e}"


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# --- Logs ---

LOG_SOURCES = {
    "syslog": ["journalctl", "-n", "200", "--no-pager"],
    "auth": ["journalctl", "-n", "200", "--no-pager", "-t", "sshd"],
    "kernel": ["dmesg", "-T"],
    "boot": ["journalctl", "-b", "-n", "200", "--no-pager"],
}


def get_logs(source: str, lines: int = 200, priority: str = "",
             unit: str = "", grep: str = ""):
    base = LOG_SOURCES.get(source)
    if not base:
        return {"logs": "Unbekannte Quelle"}
    try:
        lines = max(1, min(int(lines), 5000))
    except (TypeError, ValueError):
        lines = 200
    # journalctl-Quellen: Filter direkt an journalctl uebergeben
    if base[0] == "journalctl":
        cmd = list(base)
        if "-n" in cmd:
            cmd[cmd.index("-n") + 1] = str(lines)
        else:
            cmd += ["-n", str(lines)]
        if priority:
            cmd += ["-p", priority]
        if unit:
            cmd += ["-u", unit]
        if grep:
            cmd += ["--case-sensitive=no", "-g", grep]
        out = _run(cmd)
        return {"logs": out}
    # Nicht-journald-Quellen (z. B. dmesg): clientseitige Filterung
    log_lines = _run(base).splitlines()
    if grep:
        gl = grep.lower()
        log_lines = [l for l in log_lines if gl in l.lower()]
    return {"logs": "\n".join(log_lines[-lines:])}


# --- Alerts ---

def list_alert_rules():
    return _load(ALERT_CONFIG, {
        "rules": [],
        "channels": {"email": None, "webhook": None},
    })


def save_alert_rules(config: dict):
    _save(ALERT_CONFIG, config)
    return {"ok": True}


def add_alert_rule(metric: str, threshold: float, channel: str):
    """metric: cpu, ram, disk, smart, service_down, raid_degraded."""
    cfg = list_alert_rules()
    cfg["rules"].append({
        "id": int(time.time()),
        "metric": metric,
        "threshold": threshold,
        "channel": channel,
        "enabled": True,
    })
    _save(ALERT_CONFIG, cfg)
    return {"ok": True}


def get_alert_history():
    return _load(ALERT_HISTORY, [])


def trigger_alert(message: str, channel: str = "webhook"):
    """Alert auslösen + in Verlauf schreiben."""
    cfg = list_alert_rules()
    history = _load(ALERT_HISTORY, [])
    history.insert(0, {"time": time.time(), "message": message, "channel": channel})
    _save(ALERT_HISTORY, history[:200])

    if channel == "webhook" and cfg["channels"].get("webhook"):
        _send_webhook(cfg["channels"]["webhook"], message)
    elif channel == "email" and cfg["channels"].get("email"):
        _send_email(cfg["channels"]["email"], message)
    return {"ok": True}


def _send_webhook(url: str, message: str):
    """Webhook für Discord/Slack/Telegram."""
    try:
        import requests
        requests.post(url, json={"content": message, "text": message}, timeout=10)
    except Exception:
        pass


def _send_email(config: dict, message: str):
    """E-Mail via SMTP. config: {host, port, user, pass, to}."""
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(message)
        msg["Subject"] = "runvard Alert"
        msg["From"] = config["user"]
        msg["To"] = config["to"]
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as s:
            s.starttls()
            s.login(config["user"], config["pass"])
            s.send_message(msg)
    except Exception:
        pass
