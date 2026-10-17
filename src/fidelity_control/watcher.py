"""Automatic Local File-Watcher Daemon for Fidelity Exports.

Monitors the user's default Downloads folder and workspace directories for
newly exported Fidelity Portfolio_Positions_*.csv files. When detected,
the daemon automatically ingests, archives, and reconciles the tax lots
into the Munger Factor Engine in real-time.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fidelity-watcher")

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"

WATCH_DIRS: List[Path] = [
    Path.home() / "Downloads",
    BASE_DIR / "data",
    BASE_DIR,
]

_WATCHER_THREAD: Optional[Thread] = None
_WATCHER_RUNNING = False

WATCHER_STATE: Dict[str, Any] = {
    "status": "IDLE",
    "active": False,
    "watch_dirs": [str(d) for d in WATCH_DIRS if d.exists()],
    "last_detected_file": None,
    "last_detected_time": None,
    "last_processed_mtime": 0.0,
    "lots_reconciled": 0,
    "total_accounts": 0,
    "total_value": 0.0,
    "message": "Watcher not started.",
}


def is_valid_fidelity_csv(path: Path) -> bool:
    """Verifies that a CSV file has standard Fidelity export headers."""
    try:
        if not path.is_file() or path.stat().st_size < 50:
            return False
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            sample = f.read(4096)
        # Fidelity CSV markers
        markers = ["Account Number", "Symbol", "Current Price", "Quantity", "Cost Basis", "Fidelity"]
        return any(m in sample for m in markers)
    except Exception:
        return False


def process_fidelity_csv_file(csv_path: Path) -> Optional[Dict[str, Any]]:
    """Ingests a detected Fidelity CSV file and triggers reconciliation."""
    global WATCHER_STATE
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            csv_text = f.read()

        # Archive copy into data/
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        archive_name = f"Portfolio_Positions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        archive_path = DATA_DIR / archive_name
        latest_path = DATA_DIR / "Portfolio_Positions_latest.csv"

        try:
            shutil.copy2(csv_path, archive_path)
            shutil.copy2(csv_path, latest_path)
        except Exception as e:
            logger.warning("Could not create local archive copy of CSV: %s", e)

        # Trigger Munger reconciliation
        from .munger_api import reconcile_fidelity_api
        recon_result = reconcile_fidelity_api(csv_text)

        lots_count = len(recon_result.get("lots", []))
        summary = recon_result.get("summary", {})

        WATCHER_STATE.update({
            "status": "ACTIVE_WATCHING",
            "active": True,
            "last_detected_file": csv_path.name,
            "last_detected_path": str(csv_path),
            "last_detected_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_processed_mtime": csv_path.stat().st_mtime,
            "lots_reconciled": lots_count,
            "total_value": summary.get("total_value", 0.0),
            "message": f"Successfully ingested {csv_path.name}: {lots_count} tax lots reconciled.",
            "recon_result": recon_result,
        })

        print(f"\n[FIDELITY WATCHER] Automatically reconciled {csv_path.name} ({lots_count} tax lots) at {WATCHER_STATE['last_detected_time']}.")
        return recon_result

    except Exception as e:
        logger.error("Error processing Fidelity CSV %s: %s", csv_path, e)
        WATCHER_STATE["message"] = f"Error processing {csv_path.name}: {e}"
        return None


def find_latest_fidelity_file() -> Optional[Path]:
    """Scans all watch directories for the newest Fidelity Portfolio_Positions_*.csv."""
    candidates: List[Path] = []
    for d in WATCH_DIRS:
        if not d.exists() or not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                if p.is_file() and p.name.lower().startswith("portfolio_positions") and p.suffix.lower() == ".csv":
                    if is_valid_fidelity_csv(p):
                        candidates.append(p)
        except Exception:
            continue

    if not candidates:
        return None

    # Return newest by modification time
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def check_and_ingest_once() -> Optional[Dict[str, Any]]:
    """One-shot check for new Fidelity CSV files."""
    latest = find_latest_fidelity_file()
    if not latest:
        return None

    mtime = latest.stat().st_mtime
    if mtime > WATCHER_STATE["last_processed_mtime"]:
        return process_fidelity_csv_file(latest)
    return None


def _watcher_loop(poll_interval: float = 2.0) -> None:
    """Background polling loop for the watcher thread."""
    global _WATCHER_RUNNING
    logger.info("Fidelity File-Watcher Daemon loop started (poll interval=%.1fs)", poll_interval)
    while _WATCHER_RUNNING:
        try:
            check_and_ingest_once()
        except Exception as e:
            logger.debug("Watcher loop iteration error: %s", e)
        time.sleep(poll_interval)


def start_fidelity_watcher(poll_interval: float = 2.0) -> None:
    """Spawns the background watcher daemon thread if not already running."""
    global _WATCHER_THREAD, _WATCHER_RUNNING, WATCHER_STATE
    if _WATCHER_RUNNING and _WATCHER_THREAD and _WATCHER_THREAD.is_alive():
        return

    _WATCHER_RUNNING = True
    WATCHER_STATE["active"] = True
    WATCHER_STATE["status"] = "ACTIVE_WATCHING"
    WATCHER_STATE["message"] = f"Watching {len(WATCHER_STATE['watch_dirs'])} directories for Fidelity exports..."

    # Check for existing latest file immediately
    check_and_ingest_once()

    _WATCHER_THREAD = Thread(target=_watcher_loop, args=(poll_interval,), daemon=True, name="FidelityWatcherDaemon")
    _WATCHER_THREAD.start()
    print(f"[FIDELITY WATCHER] Automatic Local File-Watcher Daemon active. Monitoring Downloads folder...")


def stop_fidelity_watcher() -> None:
    """Stops the watcher daemon."""
    global _WATCHER_RUNNING, WATCHER_STATE
    _WATCHER_RUNNING = False
    WATCHER_STATE["active"] = False
    WATCHER_STATE["status"] = "STOPPED"
    WATCHER_STATE["message"] = "Watcher stopped."
