"""Monitoring: System-Logs, Alerts, Benachrichtigungen."""
import os
import json
import time
import subprocess

from modules.runtime import data_path
from modules import validators

ALERT_CONFIG = data_path("alerts.json")
ALERT_HISTORY = data_path("alert_history.json")
PRIORITIES = {"", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug",
              "0", "1", "2", "3", "4", "5", "6", "7"}
ALERT_METRICS = {"cpu", "ram", "disk", "smart", "service_down", "raid_degraded"}
ALERT_CHANNELS = {"webhook", "email"}


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout,
            "stderr": r.stderr,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "stdout": e.stdout or "",
            "stderr": f"{cmd[0]} timed out",
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        try:
            os.replace(path, f"{path}.corrupt-{int(time.time())}")
        except OSError:
            pass
        return default
    except OSError:
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
    if priority not in PRIORITIES:
        return {"logs": "Ungueltige Prioritaet"}
    if unit:
        try:
            unit = validators.require_service(unit)
        except ValueError:
            return {"logs": "Ungueltige Unit"}
    grep = str(grep or "")
    if len(grep) > 200 or "\n" in grep or "\r" in grep:
        return {"logs": "Ungueltiger Suchfilter"}
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
        result = _run(cmd)
        if not result["ok"]:
            return {
                "ok": False,
                "logs": result["stderr"] or result["stdout"],
                "stderr": result["stderr"],
            }
        return {"ok": True, "logs": result["stdout"]}
    # Nicht-journald-Quellen (z. B. dmesg): clientseitige Filterung
    result = _run(base)
    if not result["ok"]:
        return {
            "ok": False,
            "logs": result["stderr"] or result["stdout"],
            "stderr": result["stderr"],
        }
    log_lines = result["stdout"].splitlines()
    if grep:
        gl = grep.lower()
        log_lines = [l for l in log_lines if gl in l.lower()]
    return {"ok": True, "logs": "\n".join(log_lines[-lines:])}


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
    if metric not in ALERT_METRICS:
        return {"ok": False, "stderr": "Ungueltige Metrik"}
    if channel not in ALERT_CHANNELS:
        return {"ok": False, "stderr": "Ungueltiger Kanal"}
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Ungueltiger Schwellwert"}
    if not (0 <= threshold <= 100000):
        return {"ok": False, "stderr": "Ungueltiger Schwellwert"}
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
