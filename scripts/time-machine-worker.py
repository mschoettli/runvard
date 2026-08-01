#!/usr/bin/env python3
"""Persistent Time Machine maintenance entry point used by systemd."""

from __future__ import annotations

import json
import os
import sys


INSTALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if INSTALL_DIR not in sys.path:
    sys.path.insert(0, INSTALL_DIR)

from modules import time_machine  # noqa: E402


def main() -> int:
    result = time_machine.run_scheduled_maintenance()
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
