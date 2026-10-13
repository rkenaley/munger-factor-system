"""Configuration manager for Fidelity Control Center."""

import json
from pathlib import Path
from typing import Dict, Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


def load_config(config_path: Path | str | None = None) -> Dict[str, Any]:
    target = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not target.exists():
        # Fallback to current working directory
        cwd_target = Path("config.json")
        if cwd_target.exists():
            target = cwd_target
        else:
            raise FileNotFoundError(f"Config file not found at {target}")
            
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)
