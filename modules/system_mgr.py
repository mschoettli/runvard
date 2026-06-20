"""System: Info, Updates, Cron-Jobs, Power-Management, GPU."""
import json
import os
import re
import shutil
import tempfile
import subprocess
import time
import urllib.error
import urllib.request


RUNVARD_REPO_API = "https://api.github.com/repos/mschoettli/runvard/commits/main"
RUNVARD_REPO_URL = "https://github.com/mschoettli/runvard"
RUNVARD_REPO_GIT_URL = "https://github.com/mschoettli/runvard.git"
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


def _start_detached_update_script(script_path):
    try:
        subprocess.Popen(
            ["/bin/bash", script_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except Exception as e:
        raise RuntimeError(f"detached update start failed: {e}") from e
    return {"stdout": "", "method": "detached"}


def _start_systemd_update_script(script_path):
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
        detail = result.stderr.strip() or result.stdout.strip() or "systemd-run failed"
        raise RuntimeError(detail)
    return {"stdout": result.stdout, "method": "systemd"}


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
    systemd_error = ""
    try:
        start_result = _start_systemd_update_script(script_path)
    except Exception as e:
        systemd_error = str(e)
        try:
            start_result = _start_detached_update_script(script_path)
        except Exception as fallback_error:
            raise RuntimeError(
                f"systemd-run failed: {systemd_error}; {fallback_error}"
            ) from fallback_error
    return {
        "ok": True,
        "message": "runvard update started. The service will restart when the update finishes.",
        "log": RUNVARD_UPDATE_LOG,
        "stdout": start_result["stdout"],
        "method": start_result["method"],
        "warning": systemd_error,
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
    def git_fallback(error="", rate_limited=False):
        r = _run(["git", "ls-remote", RUNVARD_REPO_GIT_URL, "refs/heads/main"], timeout=20)
        line = r["stdout"].strip().splitlines()[0] if r["ok"] and r["stdout"].strip() else ""
        commit = line.split(None, 1)[0] if line else ""
        if re.fullmatch(r"[0-9a-f]{40}", commit):
            return {
                "ok": True,
                "commit": commit,
                "short": commit[:7],
                "url": f"{RUNVARD_REPO_URL}/commit/{commit}",
                "message": "",
                "date": "",
                "source": "git",
                "warning": error,
                "rate_limited": rate_limited,
            }
        return {
            "ok": False,
            "error": error or r["stderr"] or "GitHub unavailable",
            "rate_limited": rate_limited,
        }

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
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return git_fallback("GitHub API rate limit reached. Using git fallback.", True)
        return git_fallback(str(e))
    except (OSError, urllib.error.URLError) as e:
        return git_fallback(str(e))
    except json.JSONDecodeError as e:
        return git_fallback(str(e))
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

_POWER_ACTIONS = {
    "shutdown": ["shutdown", "-h"],
    "reboot": ["shutdown", "-r"],
    "suspend": ["systemctl", "suspend"],
    "hibernate": ["systemctl", "hibernate"],
    "hybrid-sleep": ["systemctl", "hybrid-sleep"],
    "suspend-then-hibernate": ["systemctl", "suspend-then-hibernate"],
}
_SCHEDULED_SHUTDOWN_FILE = "/run/systemd/shutdown/scheduled"
_POWER_PROFILE_RE = re.compile(r"^(power-saver|balanced|performance)$")
_LOGIND_DROPIN = "/etc/systemd/logind.conf.d/90-runvard-power.conf"
_LOGIND_KEYS = {
    "IdleAction",
    "IdleActionSec",
    "HandlePowerKey",
    "HandleLidSwitch",
    "HandleLidSwitchDocked",
    "HandleLidSwitchExternalPower",
}
_LOGIND_ACTIONS = {
    "ignore",
    "poweroff",
    "reboot",
    "halt",
    "kexec",
    "suspend",
    "hibernate",
    "hybrid-sleep",
    "suspend-then-hibernate",
    "lock",
}
_LOGIND_SECS_RE = re.compile(r"^\d+(us|ms|s|min|h|d|w|month|y)?$")


def _pkg_installed(name: str) -> bool:
    r = _run(["dpkg-query", "-W", "-f=${Status}", name])
    return r["ok"] and "install ok installed" in r["stdout"]


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def power_action(action: str, delay_min: int = 0):
    try:
        delay_min = max(0, int(delay_min))
    except (TypeError, ValueError):
        return {"ok": False, "stderr": "Delay must be a non-negative number"}
    if action == "cancel":
        return _run(["shutdown", "-c"])
    if action not in _POWER_ACTIONS:
        return {"ok": False, "stderr": "Unbekannte Aktion"}
    cmd = list(_POWER_ACTIONS[action])
    if action in ("shutdown", "reboot"):
        cmd.append(f"+{delay_min}")
    elif delay_min:
        return {"ok": False, "stderr": "Delay is only supported for shutdown and reboot"}
    return _run(cmd)


def power_status():
    scheduled = None
    try:
        with open(_SCHEDULED_SHUTDOWN_FILE) as f:
            data = dict(
                line.strip().split("=", 1)
                for line in f
                if "=" in line and line.strip()
            )
        usec = int(data.get("USEC", "0") or "0")
        when = int(usec / 1_000_000) if usec else 0
        if when:
            scheduled = {
                "action": data.get("MODE", "scheduled"),
                "timestamp": when,
                "in_seconds": max(0, when - int(time.time())),
            }
    except (OSError, ValueError):
        scheduled = None
    return {
        "scheduled": scheduled,
        "actions": ["shutdown", "reboot", "suspend", "hibernate",
                    "hybrid-sleep", "suspend-then-hibernate", "cancel"],
    }


def parse_powerprofiles_list(text: str):
    profiles = []
    active = ""
    for line in (text or "").splitlines():
        m = re.match(r"\s*(\*)?\s*([A-Za-z0-9_-]+):\s*$", line)
        if not m:
            continue
        name = m.group(2)
        if _POWER_PROFILE_RE.fullmatch(name):
            profiles.append(name)
            if m.group(1):
                active = name
    return profiles, active


def power_profiles_status():
    get = _run(["powerprofilesctl", "get"])
    if not get["ok"]:
        return {
            "available": False,
            "active": "",
            "profiles": [],
            "installed": _pkg_installed("power-profiles-daemon"),
        }
    active = get["stdout"].strip()
    lst = _run(["powerprofilesctl", "list"])
    profiles, listed_active = parse_powerprofiles_list(lst["stdout"])
    if not profiles:
        profiles = ["power-saver", "balanced", "performance"]
    return {
        "available": True,
        "active": active or listed_active,
        "profiles": profiles,
        "installed": True,
    }


def power_profiles_set(profile: str):
    if not _POWER_PROFILE_RE.fullmatch(profile or ""):
        return {"ok": False, "stderr": "Ungueltiges Energieprofil"}
    return _run(["powerprofilesctl", "set", profile])


def parse_logind_config(text: str):
    values = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in _LOGIND_KEYS:
            values[key] = value.strip()
    return values


def logind_power_status():
    values = {
        "IdleAction": "ignore",
        "IdleActionSec": "30min",
        "HandlePowerKey": "poweroff",
        "HandleLidSwitch": "suspend",
        "HandleLidSwitchDocked": "ignore",
        "HandleLidSwitchExternalPower": "suspend",
    }
    managed = {}
    for path in ("/etc/systemd/logind.conf", _LOGIND_DROPIN):
        try:
            with open(path) as f:
                parsed = parse_logind_config(f.read())
        except OSError:
            parsed = {}
        values.update(parsed)
        if path == _LOGIND_DROPIN:
            managed = parsed
    return {"values": values, "managed": managed, "dropin": _LOGIND_DROPIN}


def logind_power_set(settings: dict):
    clean = {}
    for key in _LOGIND_KEYS:
        value = str(settings.get(key, "")).strip()
        if not value:
            continue
        if key == "IdleActionSec":
            if not _LOGIND_SECS_RE.fullmatch(value):
                return {"ok": False, "stderr": "IdleActionSec has an invalid duration"}
        elif value not in _LOGIND_ACTIONS:
            return {"ok": False, "stderr": f"Invalid value for {key}"}
        clean[key] = value
    try:
        os.makedirs(os.path.dirname(_LOGIND_DROPIN), exist_ok=True)
        with open(_LOGIND_DROPIN, "w") as f:
            f.write("[Login]\n")
            for key in sorted(clean):
                f.write(f"{key}={clean[key]}\n")
    except OSError as e:
        return {"ok": False, "stderr": str(e)}
    reload_result = _run(["systemctl", "restart", "systemd-logind"], timeout=30)
    return {"ok": reload_result["ok"], "stderr": reload_result["stderr"], "stdout": reload_result["stdout"]}


def parse_systemd_inhibitors(text: str):
    inhibitors = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("who ", "who\t")) or " inhibitors listed" in line:
            continue
        parts = line.split(None, 6)
        if len(parts) >= 7 and parts[1].isdigit() and parts[3].isdigit():
            inhibitors.append({
                "who": parts[0],
                "uid": parts[1],
                "user": parts[2],
                "pid": parts[3],
                "what": parts[5],
                "why": parts[6],
            })
            continue
        parts = re.split(r"\s{2,}", line, maxsplit=5)
        if len(parts) >= 3:
            inhibitors.append({"who": parts[0], "what": parts[-2], "why": parts[-1]})
    return inhibitors


def power_inhibitors():
    r = _run(["systemd-inhibit", "--list"], timeout=15)
    return {"available": r["ok"], "raw": r["stdout"] or r["stderr"], "inhibitors": parse_systemd_inhibitors(r["stdout"])}


def power_tools_status():
    return {
        "powertop": {"installed": _pkg_installed("powertop"), "available": _command_available("powertop")},
        "tlp": {"installed": _pkg_installed("tlp"), "available": _command_available("tlp")},
        "acpid": {"installed": _pkg_installed("acpid"), "available": _command_available("acpid")},
    }


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
