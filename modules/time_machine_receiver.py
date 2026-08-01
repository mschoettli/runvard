"""Forced-command SSH receiver for passive Time Machine replicas.

The associated SSH key must use this module as a forced command. It accepts
only rsync receiver mode and confines every destination below one configured
replica root. It never invokes a shell.
"""

from __future__ import annotations

import os
from pathlib import Path
import json
import re
import shlex
import shutil
import sys


DEFAULT_ROOT = "/srv/runvard-replicas"
RSYNC_BINARY = "/usr/bin/rsync"
_ALLOWED_LONG_OPTIONS = {
    "--server", "--delete-delay", "--partial", "--numeric-ids",
    "--protect-args", "--delay-updates",
}
_ALLOWED_SHORT_OPTIONS = {"-logDtpre.iLsfxCIvu"}


def _safe_destination(value: str, root: str) -> str:
    if not value.startswith("/") or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid replica destination")
    normalized = os.path.normpath(value)
    root_real = os.path.realpath(root)
    if normalized == root_real or os.path.commonpath([normalized, root_real]) != root_real:
        raise ValueError("Replica destination escapes receiver root")
    relative = os.path.relpath(normalized, root_real).split(os.sep)
    if len(relative) < 4 or relative[0] != "runvard-time-machine-replicas":
        raise ValueError("Replica destination is outside the managed namespace")
    if ".." in relative or any(not part for part in relative):
        raise ValueError("Invalid replica destination")
    current = Path(root_real)
    for part in relative[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError("Symbolic links are forbidden in replica destinations")
    return normalized


def validate_original_command(command: str, root: str = DEFAULT_ROOT) -> list[str]:
    if not command or len(command) > 4096 or any(
        char in command for char in ("\x00", "\n", "\r")
    ):
        raise ValueError("Invalid SSH command")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("Invalid SSH command") from exc
    if len(argv) < 5 or os.path.basename(argv[0]) != "rsync" or argv[1] != "--server":
        raise ValueError("Only rsync receiver mode is allowed")
    if "--sender" in argv or "--daemon" in argv:
        raise ValueError("Rsync sender or daemon mode is forbidden")
    if argv[-2] != ".":
        raise ValueError("Unexpected rsync receiver syntax")
    for argument in argv[1:-2]:
        if argument.startswith("--") and argument.split("=", 1)[0] not in _ALLOWED_LONG_OPTIONS:
            raise ValueError("Unsupported rsync option")
        if argument.startswith("-") and not argument.startswith("--") \
                and argument not in _ALLOWED_SHORT_OPTIONS:
            raise ValueError("Unsupported rsync short options")
        if not argument.startswith("-"):
            raise ValueError("Unexpected rsync argument")
    destination = _safe_destination(argv[-1], root)
    relative = Path(destination).relative_to(Path(os.path.realpath(root))).parts
    if (
        len(relative) != 4
        or not re.fullmatch(r"[a-f0-9]{8,32}", relative[1])
        or relative[2] != "versions"
        or not re.fullmatch(r"\.incomplete-[0-9]{1,20}-[a-f0-9]{8}", relative[3])
    ):
        raise ValueError("Rsync may write only to a managed staging version")
    return [RSYNC_BINARY, *argv[1:-1], destination]


def validate_capacity_command(command: str, root: str = DEFAULT_ROOT) -> str:
    """Allow a read-only filesystem-capacity probe for the exact receiver root."""
    if not command or len(command) > 4096 or any(
        char in command for char in ("\x00", "\n", "\r")
    ):
        raise ValueError("Invalid SSH command")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("Invalid SSH command") from exc
    root_real = os.path.realpath(root)
    if (
        len(argv) != 2
        or argv[0] != "runvard-tm-capacity"
        or os.path.realpath(argv[1]) != root_real
        or Path(root).is_symlink()
    ):
        raise ValueError("Invalid replica capacity path")
    return root_real


def commit_received_version(command: str, root: str = DEFAULT_ROOT) -> str:
    """Atomically expose a completely received staging directory."""
    if not command or len(command) > 4096 or any(
        char in command for char in ("\x00", "\n", "\r")
    ):
        raise ValueError("Invalid SSH command")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("Invalid SSH command") from exc
    if len(argv) != 3 or argv[0] != "runvard-tm-commit":
        raise ValueError("Invalid replica commit command")
    staging = _safe_destination(argv[1], root)
    final = _safe_destination(argv[2], root)
    staging_path = Path(staging)
    final_path = Path(final)
    if (
        staging_path.parent != final_path.parent
        or not staging_path.name.startswith(f".incomplete-{final_path.name}-")
        or not final_path.name.isdigit()
        or staging_path.is_symlink()
        or not staging_path.is_dir()
        or final_path.exists()
    ):
        raise ValueError("Invalid replica commit paths")
    marker = staging_path / ".runvard-complete"
    marker.write_text("complete\n", encoding="utf-8")
    os.replace(staging, final)
    committed = sorted(
        (
            item for item in final_path.parent.iterdir()
            if item.is_dir() and item.name.isdigit()
            and (item / ".runvard-complete").is_file()
        ),
        key=lambda item: int(item.name),
    )
    for obsolete in committed[:-2]:
        shutil.rmtree(obsolete)
    return final


def clean_staging_versions(command: str, root: str = DEFAULT_ROOT) -> int:
    """Remove only expired, unpublished staging generations for one replica."""
    if not command or len(command) > 4096 or any(
        char in command for char in ("\x00", "\n", "\r")
    ):
        raise ValueError("Invalid SSH command")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("Invalid SSH command") from exc
    if (
        len(argv) != 3 or argv[0] != "runvard-tm-clean-staging"
        or not re.fullmatch(r"[a-f0-9]{8,32}", argv[1])
        or not re.fullmatch(r"[0-9]{1,20}", argv[2])
        or Path(root).is_symlink()
    ):
        raise ValueError("Invalid replica staging cleanup command")
    cutoff = int(argv[2])
    root_real = os.path.realpath(root)
    versions = os.path.realpath(os.path.join(
        root_real, "runvard-time-machine-replicas", argv[1], "versions",
    ))
    if os.path.commonpath([versions, root_real]) != root_real:
        raise ValueError("Invalid replica staging namespace")
    try:
        entries = list(Path(versions).iterdir())
    except FileNotFoundError:
        return 0
    removed = 0
    for entry in entries:
        match = re.fullmatch(r"\.incomplete-([0-9]{1,20})-[a-f0-9]{8}", entry.name)
        if not match or int(match.group(1)) >= cutoff:
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        path = os.path.realpath(entry)
        if os.path.commonpath([path, versions]) != versions:
            raise ValueError("Replica staging path escapes managed namespace")
        shutil.rmtree(path)
        removed += 1
    return removed


def main() -> int:
    root = os.environ.get("RUNVARD_TM_RECEIVER_ROOT", DEFAULT_ROOT)
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if original.startswith("runvard-tm-capacity "):
        try:
            path = validate_capacity_command(original, root)
            usage = shutil.disk_usage(path)
        except (ValueError, OSError):
            print("runvard replica receiver rejected the command", file=sys.stderr)
            return 126
        print(json.dumps({
            "total": usage.total, "used": usage.used, "free": usage.free,
        }, separators=(",", ":")))
        return 0
    if original.startswith("runvard-tm-commit "):
        try:
            committed = commit_received_version(original, root)
        except (ValueError, OSError):
            print("runvard replica receiver rejected the command", file=sys.stderr)
            return 126
        print(json.dumps({"ok": True, "path": committed}, separators=(",", ":")))
        return 0
    if original.startswith("runvard-tm-clean-staging "):
        try:
            removed = clean_staging_versions(original, root)
        except (ValueError, OSError):
            print("runvard replica receiver rejected the command", file=sys.stderr)
            return 126
        print(json.dumps({"ok": True, "removed": removed}, separators=(",", ":")))
        return 0
    try:
        argv = validate_original_command(original, root)
    except ValueError:
        print("runvard replica receiver rejected the command", file=sys.stderr)
        return 126
    os.execv(RSYNC_BINARY, argv)
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
