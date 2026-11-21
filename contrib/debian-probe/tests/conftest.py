# SPDX-License-Identifier: LGPL-2.1-only
"""Unit tests for the probe-side code; no probe, root or network needed.

tests/.venv/bin/pytest tests/contrib/debian-probe/tests
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # dprobe
sys.path.insert(0, str(HERE.parents[2]))  # openwrt-tests root: drivers/
