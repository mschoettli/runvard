#!/usr/bin/env python3
"""SSH ForceCommand entry point for incoming runvard replicas."""

from __future__ import annotations

import os
import sys


INSTALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if INSTALL_DIR not in sys.path:
    sys.path.insert(0, INSTALL_DIR)

from modules.time_machine_receiver import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
