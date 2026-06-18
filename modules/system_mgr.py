"""System: Info, Updates, Cron-Jobs, Power-Management, GPU."""
import json
import os
import re
import tempfile
import subprocess
import urllib.error
import urllib.request


RUNVARD_REPO_API = "https://api.github.com/repos/mschoettli/runvard/commits/main"
RUNVARD_REPO_URL = "https://github.com/mschoettli/runvard"
RUNVARD_INSTALL_URL = "https://raw.githubusercontent.com/mschoettli/runvard/main/install.sh"
RUNVARD_UPDATE_LOG = "/opt/runvard/data/runvard-update.log"
VERSION_FILE = os.environ.get(
    "RUNVARD_VERSION_FILE",
    "/opt/runvard/data/runvard.version",
)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


# --- Updates ---

def check_updates():
    """Verfügbare apt-Updates zählen."""
    _run(["apt-get", "update", "-qq"])
    r = _run(["apt-get", "--just-print", "upgrade"])
    count = r["stdout"].count("Inst ")
    return {"updates": count}


def list_upgradable():
    r = _run(["apt", "list", "--upgradable"])
    pkgs = []
    for line in r["stdout"].splitlines()[1:]:
        if "/" in line:
            pkgs.append(line.split("/")[0])
    return {"packages": pkgs}


def apply_updates():
    return _run(["apt-get", "upgrade", "-y"], timeout=1800)


def start_runvard_update():
    """
    Start a detached runvard self-update.

    The update runs as a transient systemd unit because the web service restarts
    during the update and cannot keep its own background thread alive.

    Returns:
    --------
        dict[str, str | bool]:
            Contains the start result and update log path.

    Raises:
    -------
    RuntimeError:
        Raised when the transient update unit cannot be started.
    """
    os.makedirs(os.path.dirname(RUNVARD_UPDATE_LOG), exist_ok=True)
    script = f"""#!/usr/bin/env bash
set -euo pipefail
LOG="{RUNVARD_UPDATE_LOG}"
exec > "$LOG" 2>&1
echo "runvard update started: $(date -Is)"
WORK_DIR="$(mktemp -d)"
cleanup() {{ rm -rf "$WORK_DIR"; }}
trap cleanup EXIT
echo "Downloading latest runvard release..."
curl -fsSL "{RUNVARD_INSTALL_URL}" -o "$WORK_DIR/install.sh"
chmod +x "$WORK_DIR/install.sh"
REMOTE_COMMIT="$(curl -fsSL {RUNVARD_REPO_API} 2>/dev/null | sed -n 's/.*"sha": "\\([0-9a-f]\\{{40\\}}\\)".*/\\1/p' | head -n 1 || true)"
echo "Latest commit: ${{REMOTE_COMMIT:-unknown}}"
echo "Running installer in update mode..."
if [ -f /opt/runvard/data/runvard.env ]; then
  set -a
  . /opt/runvard/data/runvard.env
  set +a
elif [ -f /opt/actax/data/actax.env ]; then
  set -a
  . /opt/actax/data/actax.env
  RUNVARD_USER="${{RUNVARD_USER:-${{ACTAX_USER:-}}}}"
  RUNVARD_PASS="${{RUNVARD_PASS:-${{ACTAX_PASS:-}}}}"
  RUNVARD_PORT="${{RUNVARD_PORT:-${{ACTAX_PORT:-}}}}"
  set +a
fi
RUNVARD_SOURCE_COMMIT="$REMOTE_COMMIT" bash "$WORK_DIR/install.sh" --yes
echo "runvard update finished: $(date -Is)"
"""
    with tempfile.NamedTemporaryFile(
        "w", delete=False, encoding="utf-8", prefix="runvard-update-", suffix=".sh"
    ) as tmp:
        tmp.write(script)
        script_path = tmp.name
    os.chmod(script_path, 0o700)
    result = subprocess.run(
        [
            "systemd-run",
            "--unit=runvard-self-update",
            "--collect",
            "/bin/bash",
            script_path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "systemd-run failed")
    return {
        "ok": True,
        "message": "runvard update started. The service will restart when the update finishes.",
        "log": RUNVARD_UPDATE_LOG,
        "stdout": result.stdout,
    }


def runvard_update_log():
    """
    Return the latest runvard self-update log output.

    Returns:
    --------
        dict[str, str | bool]:
            Contains availability and recent log text.
    """
    try:
        with open(RUNVARD_UPDATE_LOG, encoding="utf-8", errors="replace") as log_file:
            data = log_file.read()[-12000:]
    except OSError:
        return {"ok": False, "log": ""}
    return {"ok": True, "log": data}


def _git_commit():
    r = _run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"], timeout=10)
    commit = r["stdout"].strip()
    if r["ok"] and re.fullmatch(r"[0-9a-f]{40}", commit):
        return commit
    return ""


def _stored_commit():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            commit = f.read().strip()
    except OSError:
        return ""
    if re.fullmatch(r"[0-9a-f]{40}", commit):
        return commit
    return ""


def _remote_commit():
    req = urllib.request.Request(
        RUNVARD_REPO_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "runvard-Update-Check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)}
    commit = data.get("sha", "")
    info = data.get("commit", {})
    return {
        "ok": bool(re.fullmatch(r"[0-9a-f]{40}", commit)),
        "commit": commit,
        "short": commit[:7] if commit else "",
        "url": data.get("html_url") or f"{RUNVARD_REPO_URL}/commit/{commit}",
        "message": (info.get("message") or "").splitlines()[0],
        "date": ((info.get("committer") or {}).get("date") or ""),
    }


def runvard_release_status():
    """Return local and GitHub release status for runvard."""
    local = _stored_commit() or _git_commit()
    remote = _remote_commit()
    remote_commit = remote.get("commit", "") if remote.get("ok") else ""
    return {
        "repo": RUNVARD_REPO_URL,
        "branch": "main",
        "local_commit": local,
        "local_short": local[:7] if local else "",
        "remote": remote,
        "update_available": bool(local and remote_commit and local != remote_commit),
        "local_known": bool(local),
    }


# --- Cron ---

def list_cron_jobs(user="root"):
    r = _run(["crontab", "-l", "-u", user])
    jobs = []
    for line in r["stdout"].splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split(None, 5)
            if len(parts) >= 6:
                jobs.append({
                    "schedule": " ".join(parts[:5]),
                    "command": parts[5],
                })
    return jobs


def add_cron_job(schedule: str, command: str, user="root"):
    """schedule z.B. '0 3 * * *' für täglich 3 Uhr."""
    current = _run(["crontab", "-l", "-u", user])["stdout"]
    new = current + f"\n{schedule} {command}\n"
    try:
        p = subprocess.run(["crontab", "-u", user, "-"], input=new,
                           text=True, capture_output=True, timeout=15)
        return {"ok": p.returncode == 0, "stderr": p.stderr}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}


# --- Power ---

def power_action(action: str, delay_min: int = 0):
    if action == "shutdown":
        return _run(["shutdown", "-h", f"+{delay_min}"])
    elif action == "reboot":
        return _run(["shutdown", "-r", f"+{delay_min}"])
    elif action == "cancel":
        return _run(["shutdown", "-c"])
    raise ValueError("Unbekannte Aktion")


def set_hostname(name: str):
    return _run(["hostnamectl", "set-hostname", name])


# --- AppArmor ---

def apparmor_status():
    r = _run(["aa-status"])
    if not r["ok"]:
        return {"available": False, "raw": r["stderr"] or "AppArmor nicht verfügbar"}
    return {"available": True, "raw": r["stdout"]}


def apparmor_set(profile: str, mode: str):
    tool = {"enforce": "aa-enforce", "complain": "aa-complain",
            "disable": "aa-disable"}.get(mode)
    if not tool:
        return {"ok": False, "stderr": "Unbekannter Modus"}
    if not re.match(r"^[A-Za-z0-9._/-]+$", profile or ""):
        return {"ok": False, "stderr": "Ungueltiges Profil"}
    return _run([tool, profile])


# --- Generische Paketverwaltung (apt) ---

_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9.+:_-]*$")


def pkg_search(query: str):
    r = _run(["apt-cache", "search", query or ""])
    pkgs = []
    for line in r["stdout"].splitlines()[:100]:
        if " - " in line:
            n, d = line.split(" - ", 1)
            pkgs.append({"name": n.strip(), "desc": d.strip()})
    return {"packages": pkgs}


def pkg_install(name: str):
    if not _PKG_RE.match(name or ""):
        return {"ok": False, "stderr": "Ungueltiger Paketname"}
    return _run(["apt-get", "install", "-y", name], timeout=1800)


def pkg_remove(name: str):
    if not _PKG_RE.match(name or ""):
        return {"ok": False, "stderr": "Ungueltiger Paketname"}
    return _run(["apt-get", "remove", "-y", name], timeout=900)


# --- GPU ---

def gpu_info():
    """NVIDIA GPU via nvidia-smi, sonst leer."""
    r = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,"
              "memory.total,temperature.gpu",
              "--format=csv,noheader,nounits"])
    if not r["ok"]:
        return {"available": False}
    gpus = []
    for line in r["stdout"].strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            gpus.append({
                "name": parts[0],
                "util": int(parts[1]),
                "mem_used": int(parts[2]),
                "mem_total": int(parts[3]),
                "temp": int(parts[4]),
            })
    return {"available": True, "gpus": gpus}


# --- Wartung: unattended-upgrades, tuned, kdump, sosreport ---

import os as _os
import glob as _glob

_AUTO_UPGRADES = "/etc/apt/apt.conf.d/20auto-upgrades"
_UU_DROPIN = "/etc/apt/apt.conf.d/52runvard-unattended"
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _conf_flag(path, key):
    """Wert eines APT::-Schlüssels aus einer apt.conf-Datei lesen ('' wenn fehlt)."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(re.escape(key) + r'\s+"([^"]*)"', text)
    return m.group(1) if m else ""


def unattended_status():
    installed = _run(["dpkg-query", "-W", "-f=${Status}", "unattended-upgrades"])
    is_installed = installed["ok"] and "install ok installed" in installed["stdout"]
    enabled = _conf_flag(_AUTO_UPGRADES, "APT::Periodic::Unattended-Upgrade") == "1"
    # Auto-Reboot bevorzugt aus unserem Drop-in, sonst aus dem Paket-Default
    reboot = _conf_flag(_UU_DROPIN, "Unattended-Upgrade::Automatic-Reboot")
    rtime = _conf_flag(_UU_DROPIN, "Unattended-Upgrade::Automatic-Reboot-Time")
    if not reboot:
        reboot = _conf_flag("/etc/apt/apt.conf.d/50unattended-upgrades",
                            "Unattended-Upgrade::Automatic-Reboot")
    return {
        "available": True,
        "installed": is_installed,
        "enabled": enabled,
        "auto_reboot": reboot == "true",
        "reboot_time": rtime or "02:00",
    }


def unattended_set(enable, auto_reboot=False, reboot_time="02:00"):
    if reboot_time and not _TIME_RE.fullmatch(reboot_time):
        return {"ok": False, "stderr": "Uhrzeit muss HH:MM sein"}
    on = "1" if enable else "0"
    try:
        with open(_AUTO_UPGRADES, "w") as f:
            f.write(f'APT::Periodic::Update-Package-Lists "{on}";\n')
            f.write(f'APT::Periodic::Unattended-Upgrade "{on}";\n')
        with open(_UU_DROPIN, "w") as f:
            f.write("// Von runvard verwaltet – überschreibt Paket-Defaults\n")
            f.write(f'Unattended-Upgrade::Automatic-Reboot "{"true" if auto_reboot else "false"}";\n')
            if reboot_time:
                f.write(f'Unattended-Upgrade::Automatic-Reboot-Time "{reboot_time}";\n')
    except OSError as e:
        return {"ok": False, "stderr": str(e)}
    # Konfiguration validieren, falls apt-config vorhanden
    chk = _run(["apt-config", "dump"])
    if not chk["ok"] and chk["stderr"]:
        return {"ok": False, "stderr": chk["stderr"][:200]}
    return {"ok": True}


# --- tuned ---

def tuned_status():
    ver = _run(["tuned-adm", "--version"])
    if not ver["ok"]:
        return {"available": False, "active": "", "profiles": []}
    active = ""
    a = _run(["tuned-adm", "active"])
    m = re.search(r"active profile:\s*(\S+)", a["stdout"])
    if m:
        active = m.group(1)
    profiles = []
    lst = _run(["tuned-adm", "list"])
    for line in lst["stdout"].splitlines():
        line = line.strip()
        if line.startswith("- "):
            profiles.append(line[2:].strip())
    return {"available": True, "active": active, "profiles": profiles}


def tuned_set(profile):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", profile or ""):
        return {"ok": False, "stderr": "Ungueltiges Profil"}
    return _run(["tuned-adm", "profile", profile])


# --- kdump ---

def kdump_status():
    active = _run(["systemctl", "is-active", "kdump-tools"])
    enabled = _run(["systemctl", "is-enabled", "kdump-tools"])
    # Reservierter Crashkernel-Speicher (>0 = reserviert)
    crash_size = 0
    try:
        with open("/sys/kernel/kexec_crash_size") as f:
            crash_size = int(f.read().strip() or "0")
    except OSError:
        crash_size = 0
    cmdline = ""
    try:
        with open("/proc/cmdline") as f:
            m = re.search(r"crashkernel=(\S+)", f.read())
            cmdline = m.group(1) if m else ""
    except OSError:
        cmdline = ""
    state = (active["stdout"] or active["stderr"] or "").strip()
    avail = state in ("active", "inactive", "failed", "activating")
    return {
        "available": avail,
        "active": state == "active",
        "enabled": (enabled["stdout"] or "").strip() == "enabled",
        "crash_size": crash_size,
        "crashkernel": cmdline,
    }


def kdump_action(action):
    cmd = {"start": "start", "stop": "stop",
           "enable": "enable", "disable": "disable"}.get(action)
    if not cmd:
        return {"ok": False, "stderr": "Unbekannte Aktion"}
    return _run(["systemctl", cmd, "kdump-tools"])


# --- sosreport / Diagnosebericht ---

_SOS_GLOB = "/var/tmp/sosreport-*.tar.*"


def sosreport_available():
    if _run(["sos", "--version"])["ok"]:
        return True
    return _run(["sosreport", "--version"])["ok"]


def sosreport_list():
    reports = []
    for p in sorted(_glob.glob(_SOS_GLOB), key=_os.path.getmtime, reverse=True):
        if p.endswith((".md5", ".sha256")):
            continue
        try:
            st = _os.stat(p)
            reports.append({"path": p, "name": _os.path.basename(p),
                            "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            pass
    return {"available": sosreport_available(), "reports": reports}


def sosreport_run():
    """Diagnosebericht erzeugen (langlaufend; via jobs im Hintergrund)."""
    if _run(["sos", "--version"])["ok"]:
        cmd = ["sos", "report", "--batch"]
    elif _run(["sosreport", "--version"])["ok"]:
        cmd = ["sosreport", "--batch"]
    else:
        return {"ok": False, "stderr": "sos/sosreport nicht installiert"}
    r = _run(cmd, timeout=1800)
    m = re.search(r"/var/tmp/sosreport-\S+?\.tar\.\w+", r["stdout"])
    path = m.group(0) if m else ""
    tail = (r["stdout"] or "")[-1500:] + (("\n" + r["stderr"][-500:]) if r["stderr"] else "")
    return {"ok": r["ok"], "path": path, "output": tail}
