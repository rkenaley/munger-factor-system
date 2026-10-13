"""Data models for Fidelity Control Center."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional, Dict, Any, List


@dataclass
class SecurityMetrics:
    ticker: str
    name: str = ""
    sector: str = "General"
    theme: str = "Core"
    current_price: float = 0.0
    pe: float = 999.0
    pb: float = 999.0
    de: float = 999.0
    sales5y: float = 0.0
    fcf_margin: float = 0.0
    roic_proxy: float = 0.0
    
    # Technical & Moving Averages
    sma50d: float = 0.0
    sma200d: float = 0.0
    sma200w: float = 0.0
    dist_200w_pct: float = 0.0
    dist_200d_pct: float = 0.0
    munger_gating_open: bool = False
    
    # Sentiment & Momentum
    rsi14: float = 50.0
    residual_mom_12_1: float = 0.0
    pead_score: float = 50.0
    sentiment_score: float = 50.0
    extremizing_index: float = 0.0
    extremizing_status: str = "NEUTRAL"
    
    # Valuation & Target Thresholds
    intrinsic_value: float = 0.0
    buy_below: float = 0.0
    strong_buy_below: float = 0.0
    trim_above: float = 0.0
    
    # Overall Signal
    master_signal: str = "HOLD"


@dataclass
class DailyTradeOrder:
    order_id: str
    account_id: str
    account_name: str
    ticker: str
    action: Literal["BUY", "SELL", "WRITE_COVERED_CALL", "BUY_TO_CLOSE"]
    order_type: str = "LIMIT"
    limit_price: float = 0.0
    quantity: int = 0
    est_principal_usd: float = 0.0
    target_tier: str = "Core Beta"
    urgency: Literal["IMMEDIATE", "TRANCHE_SCHEDULE", "OPPORTUNISTIC_DIP", "REBALANCE"] = "TRANCHE_SCHEDULE"
    rationale: str = ""
    fidelity_instructions: str = ""


@dataclass
class FidelityAlert:
    ticker: str
    alert_type: str
    trigger_level: float
    condition: str
    purpose: str
    active: bool = True


@dataclass
class AccountSnapshot:
    account_id: str
    name: str
    tax_type: str
    cash_usd: float
    holdings_value_usd: float
    total_value_usd: float
    holdings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
