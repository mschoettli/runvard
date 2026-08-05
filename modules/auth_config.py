"""Mandatory authentication configuration and safe legacy migration."""
import json
import logging
import os
import tempfile


def _atomic_write(path: str, value: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".auth-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def enforce_authentication(path: str) -> bool:
    try:
        with open(path) as handle:
            disabled = json.load(handle).get("login_enabled") is False
    except (FileNotFoundError, OSError, ValueError, TypeError):
        disabled = False
    if disabled:
        logging.getLogger(__name__).critical(
            "Legacy configuration disabled authentication; authentication was re-enabled"
        )
    _atomic_write(path, {"login_enabled": True})
    return True
