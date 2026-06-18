"""Sicherheit: Benutzer, Gruppen, SSL-Zertifikate, SSH-Keys."""
import os
import pwd
import grp
import subprocess


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


# --- Benutzer ---

def list_users(min_uid=1000):
    """Echte Login-User (UID >= 1000) + root."""
    users = []
    for u in pwd.getpwall():
        if u.pw_uid == 0 or u.pw_uid >= min_uid:
            if u.pw_shell not in ("/usr/sbin/nologin", "/bin/false"):
                users.append({
                    "name": u.pw_name,
                    "uid": u.pw_uid,
                    "gid": u.pw_gid,
                    "home": u.pw_dir,
                    "shell": u.pw_shell,
                    "groups": [g.gr_name for g in grp.getgrall()
                               if u.pw_name in g.gr_mem],
                })
    return users


def add_user(name: str, create_home=True, shell="/bin/bash"):
    cmd = ["useradd"]
    if create_home:
        cmd.append("-m")
    cmd += ["-s", shell, name]
    return _run(cmd)


def delete_user(name: str, remove_home=False):
    cmd = ["userdel"]
    if remove_home:
        cmd.append("-r")
    cmd.append(name)
    return _run(cmd)


def set_password(name: str, password: str):
    """Passwort setzen via chpasswd."""
    try:
        p = subprocess.run(["chpasswd"], input=f"{name}:{password}",
                           text=True, capture_output=True, timeout=15)
        return {"ok": p.returncode == 0, "stderr": p.stderr}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}


def add_to_group(name: str, group: str):
    return _run(["usermod", "-aG", group, name])


def remove_from_group(name: str, group: str):
    """User aus Gruppe entfernen via gpasswd."""
    return _run(["gpasswd", "-d", name, group])


# --- Gruppen ---

def list_groups():
    return [{"name": g.gr_name, "gid": g.gr_gid, "members": list(g.gr_mem)}
            for g in grp.getgrall() if g.gr_gid >= 1000 or g.gr_gid == 0]


def add_group(name: str):
    return _run(["groupadd", name])


def delete_group(name: str):
    return _run(["groupdel", name])


# --- SSH-Keys ---

def list_ssh_keys(user: str):
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        return []
    auth = os.path.join(home, ".ssh", "authorized_keys")
    keys = []
    if os.path.exists(auth):
        with open(auth) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    keys.append({
                        "type": parts[0] if parts else "",
                        "comment": parts[-1] if len(parts) > 2 else "",
                        "key": line,
                    })
    return keys


def set_smb_password(name: str, password: str):
    """Samba-Passwort für einen User setzen (smbpasswd -a)."""
    try:
        p = subprocess.run(
            ["smbpasswd", "-a", "-s", name],
            input=f"{password}\n{password}\n",
            text=True, capture_output=True, timeout=15
        )
        if p.returncode != 0:
            # User existiert schon, nur Passwort ändern
            p = subprocess.run(
                ["smbpasswd", "-s", name],
                input=f"{password}\n{password}\n",
                text=True, capture_output=True, timeout=15
            )
        return {"ok": p.returncode == 0, "stderr": p.stderr}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}


def list_smb_users():
    """Liste aller Samba-User via pdbedit."""
    r = _run(["pdbedit", "-L"])
    users = []
    for line in r["stdout"].splitlines():
        if ":" in line:
            users.append(line.split(":")[0].strip())
    return users


def add_ssh_key(user: str, key: str):
    """Public-Key an ~/.ssh/authorized_keys eines Benutzers anhaengen."""
    key = (key or "").strip()
    if not key or key.startswith("#"):
        return {"ok": False, "error": "Kein Schluessel angegeben"}
    parts = key.split()
    if len(parts) < 2 or not parts[0].startswith(("ssh-", "ecdsa-", "sk-")):
        return {"ok": False, "error": "Kein gueltiger SSH-Public-Key"}
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return {"ok": False, "error": "Benutzer existiert nicht"}
    ssh_dir = os.path.join(pw.pw_dir, ".ssh")
    auth = os.path.join(ssh_dir, "authorized_keys")
    try:
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        existing = ""
        if os.path.exists(auth):
            with open(auth) as f:
                existing = f.read()
        if key in existing:
            return {"ok": True, "duplicate": True}
        with open(auth, "a") as f:
            f.write(key + "\n")
        os.chmod(auth, 0o600)
        os.chown(ssh_dir, pw.pw_uid, pw.pw_gid)
        os.chown(auth, pw.pw_uid, pw.pw_gid)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def remove_ssh_key(user: str, key: str):
    """Eine Schluessel-Zeile aus authorized_keys entfernen (exakter Abgleich)."""
    key = (key or "").strip()
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return {"ok": False, "error": "Benutzer existiert nicht"}
    auth = os.path.join(pw.pw_dir, ".ssh", "authorized_keys")
    if not os.path.exists(auth):
        return {"ok": True, "removed": 0}
    try:
        with open(auth) as f:
            lines = f.read().splitlines()
        kept = [l for l in lines if l.strip() != key]
        with open(auth, "w") as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))
        os.chmod(auth, 0o600)
        os.chown(auth, pw.pw_uid, pw.pw_gid)
        return {"ok": True, "removed": len(lines) - len(kept)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- SSL-Zertifikate ---

CERT_DIR = "/opt/runvard/data/certs"


def list_certificates():
    certs = []
    if not os.path.isdir(CERT_DIR):
        return certs
    for f in os.listdir(CERT_DIR):
        if f.endswith(".crt") or f.endswith(".pem"):
            path = os.path.join(CERT_DIR, f)
            info = _run(["openssl", "x509", "-in", path, "-noout",
                         "-subject", "-enddate"])
            certs.append({"file": f, "info": info["stdout"]})
    return certs


def generate_self_signed(common_name: str, days=365):
    os.makedirs(CERT_DIR, exist_ok=True)
    key = os.path.join(CERT_DIR, f"{common_name}.key")
    crt = os.path.join(CERT_DIR, f"{common_name}.crt")
    return _run([
        "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
        "-keyout", key, "-out", crt, "-days", str(days),
        "-subj", f"/CN={common_name}",
    ])


# --- sudo-Policy + Passwort-Ablauf (chage) ---

import re as _re

_LINUX_NAME = _re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SUDOERS_DROPIN = "/etc/sudoers.d/runvard-%s"


def _valid_name(name):
    return bool(_LINUX_NAME.fullmatch(name or ""))


def _in_sudo_group(name):
    for g in grp.getgrall():
        if g.gr_name in ("sudo", "wheel") and name in g.gr_mem:
            return True
    return False


def _chage_info(name):
    """`chage -l` parsen -> Aging-Felder."""
    r = _run(["chage", "-l", name])
    info = {"last_change": "", "expires": "", "account_expires": "",
            "min_days": "", "max_days": "", "warn_days": ""}
    if not r["ok"]:
        return info
    for line in r["stdout"].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if "last password change" in k:
            info["last_change"] = v
        elif "password expires" in k:
            info["expires"] = v
        elif "account expires" in k:
            info["account_expires"] = v
        elif "minimum number" in k:
            info["min_days"] = v
        elif "maximum number" in k:
            info["max_days"] = v
        elif "number of days of warning" in k:
            info["warn_days"] = v
    return info


def user_security(name):
    """sudo-Status + Passwort-Aging eines OS-Users."""
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Benutzername"}
    nopasswd = os.path.exists(_SUDOERS_DROPIN % name)
    return {
        "ok": True,
        "sudo": _in_sudo_group(name),
        "nopasswd": nopasswd,
        "aging": _chage_info(name),
    }


def set_sudo(name, enable, nopasswd=False):
    """sudo-Rechte gewähren/entziehen. nopasswd schreibt einen sudoers.d-Drop-in."""
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Benutzername"}
    dropin = _SUDOERS_DROPIN % name
    if enable:
        res = _run(["usermod", "-aG", "sudo", name])
        if not res["ok"]:
            return res
        if nopasswd:
            try:
                with open(dropin, "w") as f:
                    f.write(f"{name} ALL=(ALL) NOPASSWD:ALL\n")
                os.chmod(dropin, 0o440)
            except OSError as e:
                return {"ok": False, "stderr": str(e)}
            # Syntax prüfen; bei Fehler Drop-in wieder entfernen
            chk = _run(["visudo", "-cf", dropin])
            if not chk["ok"]:
                try:
                    os.remove(dropin)
                except OSError:
                    pass
                return {"ok": False, "stderr": "sudoers-Syntax ungültig: " + chk["stderr"]}
        elif os.path.exists(dropin):
            try:
                os.remove(dropin)
            except OSError:
                pass
        return {"ok": True}
    else:
        if os.path.exists(dropin):
            try:
                os.remove(dropin)
            except OSError:
                pass
        return _run(["gpasswd", "-d", name, "sudo"])


def set_password_aging(name, max_days="", min_days="", warn_days="", expire=""):
    """Passwort-Ablauf via chage setzen. Leere Felder bleiben unverändert.

    expire: 'YYYY-MM-DD' oder leer. max/min/warn: Zahl oder -1 (= nie).
    """
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Benutzername"}
    cmd = ["chage"]

    def _num(v):
        v = str(v).strip()
        if v == "":
            return None
        if v == "-1":
            return "-1"
        return v if v.lstrip("-").isdigit() else None

    pairs = [("-M", max_days), ("-m", min_days), ("-W", warn_days)]
    for flag, val in pairs:
        n = _num(val)
        if n is not None:
            cmd += [flag, n]
    exp = str(expire).strip()
    if exp:
        if exp == "-1":
            cmd += ["-E", "-1"]
        elif _re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp):
            cmd += ["-E", exp]
        else:
            return {"ok": False, "stderr": "Ablaufdatum muss YYYY-MM-DD sein"}
    if len(cmd) == 1:
        return {"ok": False, "stderr": "Keine Änderung angegeben"}
    cmd.append(name)
    return _run(cmd)


def expire_password_now(name):
    """Passwortänderung bei nächster Anmeldung erzwingen (chage -d 0)."""
    if not _valid_name(name):
        return {"ok": False, "stderr": "Ungueltiger Benutzername"}
    return _run(["chage", "-d", "0", name])
