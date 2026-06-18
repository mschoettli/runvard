"""Runtime configuration shared by runvard modules."""

from __future__ import annotations

import os

DEFAULT_DATA_DIR = "/opt/runvard/data"


def data_dir() -> str:
    """Return the configured persistent data directory."""
    return os.path.abspath(os.environ.get("RUNVARD_DATA_DIR") or DEFAULT_DATA_DIR)


def data_path(*parts: str) -> str:
    """Build a path below the configured persistent data directory."""
    base = data_dir()
    for part in parts:
        if os.path.isabs(part):
            raise ValueError("data path parts must be relative")
    path = os.path.abspath(os.path.join(base, *parts))
    if path != base and not path.startswith(base + os.sep):
        raise ValueError("data path escapes data directory")
    return path
