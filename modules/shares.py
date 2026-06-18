"""Freigaben: Samba, NFS, FTP/SFTP verwalten."""
import os
import subprocess

SMB_CONF = "/etc/samba/smb.conf"
NFS_EXPORTS = "/etc/exports"


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        return {"ok": False, "stderr": str(e)}


# --- Samba ---

def list_samba_shares():
    """Bestehende Samba-Freigaben aus smb.conf parsen."""
    shares = []
    if not os.path.exists(SMB_CONF):
        return shares
    current = None
    with open(SMB_CONF) as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                if section.lower() not in ("global", "printers", "print$"):
                    current = {"name": section, "path": "", "writable": False}
                    shares.append(current)
                else:
                    current = None
            elif current and "=" in line:
                key, val = [x.strip() for x in line.split("=", 1)]
                if key.lower() == "path":
                    current["path"] = val
                elif key.lower() in ("writable", "writeable", "read only"):
                    current["writable"] = val.lower() in ("yes", "true")
    return shares


def add_samba_share(name: str, path: str, writable=True, guest=False):
    os.makedirs(path, exist_ok=True)
    block = f"""
[{name}]
   path = {path}
   browseable = yes
   writable = {'yes' if writable else 'no'}
   guest ok = {'yes' if guest else 'no'}
"""
    with open(SMB_CONF, "a") as f:
        f.write(block)
    _run(["systemctl", "restart", "smbd"])
    return {"ok": True}


# --- NFS ---

def list_nfs_exports():
    exports = []
    if not os.path.exists(NFS_EXPORTS):
        return exports
    with open(NFS_EXPORTS) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split()
                if parts:
                    exports.append({"path": parts[0],
                                    "clients": " ".join(parts[1:])})
    return exports


def add_nfs_export(path: str, clients="*", options="rw,sync,no_subtree_check"):
    os.makedirs(path, exist_ok=True)
    with open(NFS_EXPORTS, "a") as f:
        f.write(f"\n{path} {clients}({options})\n")
    _run(["exportfs", "-ra"])
    return {"ok": True}


# --- FTP (vsftpd) ---

def ftp_status():
    r = _run(["systemctl", "is-active", "vsftpd"])
    return {"active": r["stdout"].strip() == "active"}


def ftp_action(action: str):
    if action not in ("start", "stop", "restart"):
        raise ValueError("Unbekannte Aktion")
    return _run(["systemctl", action, "vsftpd"])
