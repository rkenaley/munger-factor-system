"""Exporters for Fidelity CSV formats and JSON reporting."""

from __future__ import annotations
import csv
import json
from pathlib import Path
from typing import List, Dict, Any
from .models import DailyTradeOrder, SecurityMetrics, FidelityAlert


def export_fidelity_orders_csv(orders: List[DailyTradeOrder], output_dir: Path) -> Path:
    """Exports daily trade orders into a clean CSV formatted for Fidelity execution."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "fidelity_daily_orders.csv"
    
    headers = [
        "Order ID", "Account Name", "Ticker", "Action", "Order Type", 
        "Limit Price ($)", "Quantity", "Est Principal ($)", "Target Tier", 
        "Urgency", "Fidelity Execution Instructions"
    ]
    
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for o in orders:
            writer.writerow([
                o.order_id,
                o.account_name,
                o.ticker,
                o.action,
                o.order_type,
                f"{o.limit_price:.2f}",
                o.quantity,
                f"{o.est_principal_usd:.2f}",
                o.target_tier,
                o.urgency,
                o.fidelity_instructions
            ])
            
    return target


def export_fidelity_alerts_csv(alerts: List[FidelityAlert], output_dir: Path) -> Path:
    """Exports technical and price alert levels for Fidelity Alert Center."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "fidelity_alerts.csv"
    
    headers = ["Ticker", "Alert Type", "Trigger Level ($)", "Condition", "Purpose", "Status"]
    
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for a in alerts:
            writer.writerow([
                a.ticker,
                a.alert_type,
                f"{a.trigger_level:.2f}",
                a.condition,
                a.purpose,
                "ACTIVE" if a.active else "TRIGGERED"
            ])
            
    return target


def export_fidelity_watchlist_csv(metrics_map: Dict[str, SecurityMetrics], output_dir: Path) -> Path:
    """Exports high-conviction watchlist with extremizing indicators and Munger gating."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "fidelity_watchlist.csv"
    
    headers = [
        "Symbol", "Company Name", "Theme", "Current Price ($)", "RSI (14d)", 
        "Extremizing Status", "Extremizing Index", "Munger 200w Gating", 
        "Buy Below ($)", "Trim Above ($)", "Master Signal"
    ]
    
    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for ticker, m in metrics_map.items():
            writer.writerow([
                ticker,
                m.name,
                m.theme,
                f"{m.current_price:.2f}",
                f"{m.rsi14:.1f}",
                m.extremizing_status,
                f"{m.extremizing_index:.1f}",
                "OPEN (BUY)" if m.munger_gating_open else f"CLOSED (+{m.dist_200w_pct:.1f}%)",
                f"{m.buy_below:.2f}",
                f"{m.trim_above:.2f}",
                m.master_signal
            ])
            
    return target


def export_unified_json(payload: Dict[str, Any], output_dir: Path) -> Path:
    """Exports unified state JSON consumed by the control center web dashboard."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "daily_report.json"
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return target
