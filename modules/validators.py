"""
Validate user-controlled names, paths, and host resources.

The helpers centralize guard rules for routes that run with elevated system
permissions.
"""

from __future__ import annotations

import os
import re
import zipfile
import base64
import binascii
from pathlib import Path

BLOCKED_PATHS = {"/proc", "/sys", "/dev", "/run"}
READONLY_PATHS = {"/etc", "/bin", "/sbin", "/usr", "/lib", "/lib64", "/boot"}
SENSITIVE_HOST_PATHS = {
    "/",
    "/etc",
    "/root",
    "/opt/runvard",
    "/var/run/docker.sock",
    "/var/lib/docker",
    "/var/lib/libvirt",
}

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
LINUX_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
APT_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9.+:_-]*$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")
DEV_PATH_RE = re.compile(r"^/dev/[A-Za-z0-9_.+-]+(/[A-Za-z0-9_.+-]+)?$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@:+-]+\.service$")
NETDEV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,14}$")
VM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
DOCKER_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$")
DOCKER_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
REMOTE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}$")
REMOTE_SHARE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$")
REMOTE_EXPORT_RE = re.compile(r"^/[A-Za-z0-9_./@+-]{0,511}$")
MOUNT_OPTIONS_RE = re.compile(r"^[A-Za-z0-9_.,=:/@+-]{0,512}$")
NFS_CLIENTS_RE = re.compile(r"^[A-Za-z0-9*?.:/,_ -]{1,256}$")
RSYNC_REMOTE_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_-]{0,31}@)?"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}:"
    r"[A-Za-z0-9_./~@+-]{1,512}$"
)
APPARMOR_PROFILE_RE = re.compile(r"^[A-Za-z0-9_./+-]{1,256}$")
SSH_KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}


def real_path(path: str) -> str:
    """
    Resolve a filesystem path.

    Args:
    -----
        path (str):
            Path supplied by the caller.

    Returns:
    --------
        str:
            Canonical absolute path.
    """
    if not path:
        raise ValueError("Path is required")
    return os.path.realpath(path)


def is_under(path: str, roots: set[str]) -> bool:
    """
    Check whether a path is equal to or below one of the roots.

    Args:
    -----
        path (str):
            Path to check.
        roots (set[str]):
            Canonical root paths.

    Returns:
    --------
        bool:
            True when the path is inside a protected root.
    """
    resolved = real_path(path)
    canonical_roots = {real_path(root) for root in roots}
    return any(
        resolved == root or resolved.startswith(root + "/")
        for root in canonical_roots
    )


def is_blocked_path(path: str) -> bool:
    """
    Check whether a path is in a blocked runtime filesystem.

    Args:
    -----
        path (str):
            Path to check.

    Returns:
    --------
        bool:
            True when the path is blocked.
    """
    return is_under(path, BLOCKED_PATHS)


def is_readonly_path(path: str) -> bool:
    """
    Check whether a path is read-only in runvard.

    Args:
    -----
        path (str):
            Path to check.

    Returns:
    --------
        bool:
            True when runvard must not write to the path.
    """
    return is_under(path, READONLY_PATHS)


def guard_read_path(path: str) -> str:
    """
    Validate a readable filesystem path.

    Args:
    -----
        path (str):
            Path to validate.

    Returns:
    --------
        str:
            Canonical path.

    Raises:
    -------
        PermissionError:
            Raised when the path is blocked.
    """
    resolved = real_path(path)
    if is_blocked_path(resolved):
        raise PermissionError("Path is blocked")
    return resolved


def guard_write_path(path: str) -> str:
    """
    Validate a writable filesystem path.

    Args:
    -----
        path (str):
            Path to validate.

    Returns:
    --------
        str:
            Canonical path.

    Raises:
    -------
        PermissionError:
            Raised when runvard must not write to the path.
    """
    resolved = guard_read_path(path)
    if is_readonly_path(resolved):
        raise PermissionError("Path is read-only")
    return resolved


def safe_join(directory: str, filename: str) -> str:
    """
    Join a directory and filename without allowing traversal.

    Args:
    -----
        directory (str):
            Base directory.
        filename (str):
            User-supplied filename.

    Returns:
    --------
        str:
            Canonical destination path.

    Raises:
    -------
        ValueError:
            Raised when the filename is unsafe.
    """
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("Invalid filename")
    if ".." in Path(filename).parts:
        raise ValueError("Invalid filename")
    base = guard_write_path(directory)
    dest = os.path.realpath(os.path.join(base, filename))
    if not (dest == base or dest.startswith(base + os.sep)):
        raise ValueError("Destination escapes base directory")
    return dest


def require_slug(value: str, label: str = "name") -> str:
    """
    Validate a stable slug-like identifier.

    Args:
    -----
        value (str):
            Identifier to validate.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated identifier.

    Raises:
    -------
        ValueError:
            Raised when the value is unsafe.
    """
    if not SLUG_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def require_int_range(value, minimum: int, maximum: int, label: str = "value") -> int:
    """
    Validate an integer constrained to an inclusive range.

    Args:
    -----
        value:
            Caller-supplied value.
        minimum (int):
            Smallest allowed value.
        maximum (int):
            Largest allowed value.
        label (str):
            Field label for the error message.

    Returns:
    --------
        int:
            Validated integer.

    Raises:
    -------
        ValueError:
            Raised when the value is not an integer or is outside the range.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {label}")
    if not (minimum <= parsed <= maximum):
        raise ValueError(f"Invalid {label}")
    return parsed


def require_linux_name(value: str, label: str = "name") -> str:
    """
    Validate a Linux user or group name.

    Args:
    -----
        value (str):
            Name to validate.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated name.

    Raises:
    -------
        ValueError:
            Raised when the name is unsafe.
    """
    if not LINUX_NAME_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def require_apt_package_name(value: str, label: str = "package name") -> str:
    """
    Validate a Debian/Ubuntu package name before invoking apt.

    Args:
    -----
        value (str):
            Package name supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated package name.

    Raises:
    -------
        ValueError:
            Raised when the package name is unsafe.
    """
    if not APT_PACKAGE_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def require_device(value: str) -> str:
    """
    Validate a Linux block device token.

    Args:
    -----
        value (str):
            Device name or path.

    Returns:
    --------
        str:
            Validated device token.

    Raises:
    -------
        ValueError:
            Raised when the device token is unsafe.
    """
    value = value or ""
    if value.startswith("/dev/"):
        if not DEV_PATH_RE.fullmatch(value):
            raise ValueError("Invalid device")
        parts = value.removeprefix("/dev/").split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid device")
        return value
    if "/" in value or not DEVICE_RE.fullmatch(value):
        raise ValueError("Invalid device")
    return value


def require_service(value: str) -> str:
    """
    Validate a systemd service unit name.

    Args:
    -----
        value (str):
            Service name.

    Returns:
    --------
        str:
            Validated service name.

    Raises:
    -------
        ValueError:
            Raised when the service name is unsafe.
    """
    if not SERVICE_RE.fullmatch(value or ""):
        raise ValueError("Invalid service")
    return value


def require_netdev(value: str, label: str = "interface") -> str:
    """
    Validate a Linux network interface name.

    Args:
    -----
        value (str):
            Interface name.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated interface name.

    Raises:
    -------
        ValueError:
            Raised when the interface name is unsafe.
    """
    if not NETDEV_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def require_vm_name(value: str, label: str = "VM name") -> str:
    """
    Validate a libvirt VM, network, or snapshot-style name.

    Args:
    -----
        value (str):
            Name supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated VM-style name.

    Raises:
    -------
        ValueError:
            Raised when the name is unsafe.
    """
    if not VM_NAME_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def require_docker_ref(value: str, label: str = "Docker reference") -> str:
    """
    Validate a Docker image/container/image-id reference.
    """
    value = str(value or "").strip()
    if (
        not DOCKER_REF_RE.fullmatch(value)
        or value.startswith("-")
        or ".." in value
        or any(ch in value for ch in "\n\r\0")
    ):
        raise ValueError(f"Invalid {label}")
    return value


def require_docker_volume(value: str, label: str = "Docker volume") -> str:
    """
    Validate a Docker volume name before SDK calls.
    """
    value = str(value or "").strip()
    if (
        not DOCKER_VOLUME_RE.fullmatch(value)
        or value.startswith("-")
        or ".." in value
    ):
        raise ValueError(f"Invalid {label}")
    return value


def require_remote_host(value: str, label: str = "server") -> str:
    """
    Validate a remote host token for SMB/NFS mounts.

    Args:
    -----
        value (str):
            Hostname or IP address supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated host token.
    """
    if not REMOTE_HOST_RE.fullmatch(value or "") or "/" in value:
        raise ValueError(f"Invalid {label}")
    return value


def require_remote_share(value: str, label: str = "share") -> str:
    """
    Validate a remote share/export name segment.

    Args:
    -----
        value (str):
            Share name supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated share/export segment.
    """
    if not REMOTE_SHARE_RE.fullmatch(value or "") or "/" in value or "\\" in value:
        raise ValueError(f"Invalid {label}")
    return value


def require_remote_export(value: str, label: str = "export") -> str:
    """
    Validate an absolute remote NFS export path without resolving it locally.

    Args:
    -----
        value (str):
            Remote export path supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated remote export path.
    """
    value = value or ""
    if not REMOTE_EXPORT_RE.fullmatch(value) or "/../" in value or value.endswith("/.."):
        raise ValueError(f"Invalid {label}")
    return value


def require_mount_options(value: str, label: str = "options") -> str:
    """
    Validate comma-separated mount options before passing them to mount(8).

    Args:
    -----
        value (str):
            Option string supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated option string.
    """
    if not MOUNT_OPTIONS_RE.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def require_nfs_clients(value: str) -> str:
    """
    Validate the clients field of an /etc/exports NFS entry.

    Args:
    -----
        value (str):
            Client selector string, for example "*" or "192.168.1.0/24".

    Returns:
    --------
        str:
            Validated client selector.
    """
    value = str(value or "").strip()
    if not NFS_CLIENTS_RE.fullmatch(value):
        raise ValueError("Invalid NFS clients")
    return value


def require_rsync_remote(value: str, label: str = "rsync path") -> str:
    """
    Validate an rsync remote path before it becomes a command argument.

    Args:
    -----
        value (str):
            Remote path in [user@]host:path form.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated remote path.
    """
    value = str(value or "").strip()
    if value.startswith("-") or not RSYNC_REMOTE_RE.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    if any(ch in value for ch in "\n\r\0"):
        raise ValueError(f"Invalid {label}")
    return value


def require_apparmor_profile(value: str) -> str:
    """
    Validate an AppArmor profile before passing it to aa-* tools.

    AppArmor commonly reports profiles as absolute paths such as /usr/bin/foo,
    dotted names such as usr.bin.foo, and named profiles such as docker-default.
    """
    value = str(value or "").strip()
    if (
        not APPARMOR_PROFILE_RE.fullmatch(value)
        or value.startswith(("-", "."))
        or value.endswith(("/", "."))
        or "//" in value
        or "/../" in value
        or value in {".", ".."}
    ):
        raise ValueError("Invalid AppArmor profile")
    return value


def require_ssh_public_key(value: str) -> str:
    """
    Validate a single OpenSSH public key line before writing authorized_keys.
    """
    value = str(value or "").strip()
    if not value or value.startswith("#") or len(value) > 8192:
        raise ValueError("Invalid SSH public key")
    if any(ch in value for ch in "\n\r\0"):
        raise ValueError("Invalid SSH public key")
    parts = value.split()
    if len(parts) < 2 or parts[0] not in SSH_KEY_TYPES:
        raise ValueError("Invalid SSH public key")
    try:
        decoded = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        raise ValueError("Invalid SSH public key")
    if len(decoded) < 16:
        raise ValueError("Invalid SSH public key")
    return value


def require_password_value(value: str, label: str = "password") -> str:
    """
    Validate a password before passing it to line-oriented system tools.
    """
    value = str(value or "")
    if not value or any(ch in value for ch in "\n\r\0"):
        raise ValueError(f"Invalid {label}")
    return value


def require_mount_option_value(value: str, label: str = "option") -> str:
    """
    Validate a single mount option value.

    Args:
    -----
        value (str):
            Option value supplied by the caller.
        label (str):
            Field label for the error message.

    Returns:
    --------
        str:
            Validated option value.
    """
    if any(ch in (value or "") for ch in ",\n\r\0"):
        raise ValueError(f"Invalid {label}")
    return value


def guard_host_mount(path: str) -> str:
    """
    Validate a Docker host mount path.

    Args:
    -----
        path (str):
            Host path to mount.

    Returns:
    --------
        str:
            Canonical host path.

    Raises:
    -------
        PermissionError:
            Raised when the mount path is sensitive.
    """
    resolved = guard_read_path(path)
    if is_under(resolved, SENSITIVE_HOST_PATHS):
        raise PermissionError("Sensitive host path cannot be mounted")
    return resolved


def guard_mountpoint(path: str) -> str:
    """
    Validate a mount target path for storage operations.

    Args:
    -----
        path (str):
            Mountpoint supplied by the caller.

    Returns:
    --------
        str:
            Canonical mountpoint path.

    Raises:
    -------
        ValueError:
            Raised when the path is not absolute.
        PermissionError:
            Raised when the mount target is unsafe.
    """
    if not path or not path.startswith("/"):
        raise ValueError("Mountpoint must be an absolute path")
    resolved = real_path(path)
    if resolved == "/" or is_blocked_path(resolved) or is_under(resolved, SENSITIVE_HOST_PATHS):
        raise PermissionError("Mountpoint is protected")
    return resolved


def validate_zip_members(archive: zipfile.ZipFile, destination: str) -> None:
    """
    Ensure every ZIP member extracts inside the destination directory.

    Args:
    -----
        archive (zipfile.ZipFile):
            Open ZIP archive.
        destination (str):
            Extraction destination.

    Raises:
    -------
        ValueError:
            Raised when a member escapes the destination.
    """
    dest = guard_write_path(destination)
    for member in archive.infolist():
        target = os.path.realpath(os.path.join(dest, member.filename))
        if not (target == dest or target.startswith(dest + os.sep)):
            raise ValueError("ZIP archive contains unsafe paths")
