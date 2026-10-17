#!/usr/bin/env python3
"""
================================================================================
FIDELITY AUTOMATIC FILE-WATCHER DAEMON (CLI STANDALONE)
================================================================================
Monitors your local Downloads folder (e.g. ~/Downloads) and project data/ folder
for newly exported Fidelity 'Portfolio_Positions_*.csv' files.

Whenever you click 'Download' inside Fidelity.com:
  1. The daemon detects the file in < 2 seconds.
  2. Strips cash sweep rows (SPAXX**, FDRXX**).
  3. Reconciles all tax lots against 365-day LTCG rules.
  4. Generates instant actionable buy/hold/sell directives.
================================================================================
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Add src to path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fidelity_control.watcher import (
    WATCH_DIRS,
    WATCHER_STATE,
    check_and_ingest_once,
    find_latest_fidelity_file,
    start_fidelity_watcher,
)


def main():
    print("=" * 80)
    print(" 🚀 FIDELITY AUTOMATIC LOCAL FILE-WATCHER DAEMON")
    print("=" * 80)
    print("Watching the following directories for 'Portfolio_Positions_*.csv':")
    for d in WATCH_DIRS:
        status = "[EXISTS]" if d.exists() else "[NOT FOUND]"
        print(f"  - {status} {d}")

    print("\nInstructions:")
    print("  1. Log into Fidelity.com -> 'Accounts & Trade' -> 'Portfolio'.")
    print("  2. Click the 'Download' icon (CSV format).")
    print("  3. This watcher will automatically ingest and reconcile your lots!\n")

    latest = find_latest_fidelity_file()
    if latest:
        print(f"[*] Found existing export: {latest.name} (Modified: {time.ctime(latest.stat().st_mtime)})")
        print("[*] Reconciling existing export...")
        check_and_ingest_once()
    else:
        print("[*] No existing Fidelity export found yet. Waiting for your first download...")

    print("\n[*] Daemon running. Press Ctrl+C to stop.\n")
    start_fidelity_watcher(poll_interval=2.0)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[*] Fidelity File-Watcher stopped.")


if __name__ == "__main__":
    main()
