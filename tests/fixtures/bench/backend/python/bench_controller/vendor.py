"""Make ``r3_hal`` importable on every runtime.

Development and the twin host process have ``r3_hal`` on ``PYTHONPATH``. A BLITZ
bundle only exposes this module's own folder, so ``backend/deploy.py`` stages a
copy of ``r3_hal`` into ``_vendor/`` before building (see ``stage_hal``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"


def ensure_r3_hal() -> None:
    if importlib.util.find_spec("r3_hal") is None and (VENDOR_DIR / "r3_hal").is_dir():
        sys.path.insert(0, str(VENDOR_DIR))


ensure_r3_hal()
