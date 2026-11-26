#!/usr/bin/env python3
"""Standalone runner for ibkr_bot CLI without needing PYTHONPATH env setup."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ibkr_bot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
