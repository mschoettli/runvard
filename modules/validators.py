"""
Validate user-controlled names, paths, and host resources.

The helpers centralize guard rules for routes that run with elevated system
permissions.
"""

from __future__ import annotations

import os
import re
import zipfile
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
DEVICE_RE = re.compile(r"^(/dev/)?[A-Za-z0-9_.+-]+$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@:+-]+\\.service$")


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
    return any(resolved == root or resolved.startswith(root + "/") for root in roots)


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
    if not DEVICE_RE.fullmatch(value or ""):
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
