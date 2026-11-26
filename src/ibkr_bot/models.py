"""Typed domain models for orders, option spreads, tax lots, snapshots, and risk decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .config import AccountType


def is_irs_long_term(acquired_at: date, close_date: date) -> bool:
    """
    Critic #2 Fix #9: Exact IRS IRC §1222(3) holding-period rule (> 1 full calendar year).
    Handles leap-year anniversaries accurately (e.g., 2024-02-28 -> 2025-02-28 is STILL
    short-term even though 366 days elapsed; 2024-02-29 anniversary rolls to 2025-02-28,
    so first LTCG day is 2025-03-01).
    """
    try:
        anniversary = acquired_at.replace(year=acquired_at.year + 1)
    except ValueError:
        # Feb 29 in leap year -> 1-year anniversary is Feb 28 of next year; >1yr starts Mar 1
        anniversary = date(acquired_at.year + 1, 2, 28)
    return close_date > anniversary


class IntentType(str, Enum):
    CORE_BUY = "CORE_BUY"
    CORE_REBALANCE_SELL = "CORE_REBALANCE_SELL"
    TLH_SELL = "TLH_SELL"
    TLH_BUY = "TLH_BUY"
    CASH_SWEEP_BUY = "CASH_SWEEP_BUY"
    CASH_SWEEP_SELL = "CASH_SWEEP_SELL"
    XSP_OPEN_CREDIT_SPREAD = "XSP_OPEN_CREDIT_SPREAD"
    XSP_CLOSE_CREDIT_SPREAD = "XSP_CLOSE_CREDIT_SPREAD"
    SINGLE_STOCK_OPTION = "SINGLE_STOCK_OPTION"  # Explicitly modeled so RiskFirewall hard-blocks it!
    SINGLE_STOCK_CHURN = "SINGLE_STOCK_CHURN"    # Explicitly modeled so RiskFirewall hard-blocks it!


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    OPTION_COMBO = "OPTION_COMBO"
    OPTION_SINGLE = "OPTION_SINGLE"


@dataclass(frozen=True)
class OptionLegSpec:
    """Single option leg definition inside a multi-leg IBKR BAG combo."""

    symbol: str
    expiry: str  # YYYYMMDD or YYYY-MM-DD (normalized to YYYYMMDD for IBKR)
    strike: float
    right: str  # "P" or "C"
    action: str  # "BUY" or "SELL"
    ratio: int = 1
    multiplier: int = 100
    bid: float = 0.0
    ask: float = 0.0
    open_interest: int = 0
    implied_vol: Optional[float] = None
    delta: Optional[float] = None
    con_id: Optional[int] = None
    is_european: bool = True

    @property
    def mid(self) -> float:
        if self.bid > 0.0 and self.ask > 0.0:
            return 0.5 * (self.bid + self.ask)
        return max(self.bid, self.ask, 0.0)

    @property
    def spread_width_usd(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def ibkr_expiry(self) -> str:
        return self.expiry.replace("-", "")


@dataclass
class OrderIntent:
    """Normalized pre-risk order intent emitted by SignalEngine."""

    account_id: str
    account_type: AccountType
    intent_type: IntentType
    asset_class: AssetClass
    symbol: str
    action: str  # "BUY" or "SELL"
    quantity: int
    limit_price: float  # Always POSITIVE quote price here; IBKRBroker applies signed BAG convention!
    rationale: str
    trade_date: date
    is_risk_reducing: bool = False
    estimated_mid_price: Optional[float] = None
    legs: List[OptionLegSpec] = field(default_factory=list)
    spread_id: Optional[str] = None
    max_loss_total_usd: float = 0.0
    net_credit_per_share: float = 0.0
    dte_days: Optional[int] = None
    lot_id: Optional[str] = None
    lot_acquired_date: Optional[date] = None
    lot_unrealized_pnl_usd: Optional[float] = None
    linked_tlh_symbol: Optional[str] = None
    linked_tlh_lot_id: Optional[str] = None
    linked_tlh_proceeds_usd: Optional[float] = None
    is_proxy_chain: bool = False
    max_step_limit_price: Optional[float] = None  # Hard adaptive stepping cap enforced in IBKRBroker!
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        """
        Critic #2 Fix #4: Strictly deterministic daily idempotency key across restarts.
        - Never embeds volatile share quantity for CORE_BUY / CASH_SWEEP_BUY (intraday
          1-share tick shifts won't trigger duplicate orders on restart!).
        - Uses deterministic leg signature + lot_id + spread_id (which itself contains
          NO random UUID).
        """
        leg_sig = "|".join(
            f"{l.symbol.upper()}:{l.ibkr_expiry}:{l.right}:{l.strike:.2f}:{l.action}"
            for l in self.legs
        )
        lot_key = self.lot_id or self.linked_tlh_lot_id or ""
        raw = (
            f"{self.trade_date.isoformat()}|{self.account_id}|{self.intent_type.value}|"
            f"{self.symbol.upper()}|{self.action}|{leg_sig}|{self.spread_id or ''}|{lot_key}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class RiskDecision:
    approved: bool
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    idempotency_key: str
    status: str  # "FILLED", "PARTIALLY_FILLED", "SIMULATED", "SUBMITTED", "REJECTED", "SKIPPED_DUPLICATE", "CANCELLED", "ERROR"
    broker_order_id: Optional[str] = None
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    commission_usd: float = 0.0
    slippage_bps: float = 0.0
    message: str = ""
    raw_order_spec: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaxLot:
    lot_id: str
    account_id: str
    account_type: AccountType
    symbol: str
    quantity: float
    cost_basis_per_share: float
    acquired_at: date

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.cost_basis_per_share) * self.quantity

    def unrealized_return(self, price: float) -> float:
        if self.cost_basis_per_share <= 0.0:
            return 0.0
        return (price / self.cost_basis_per_share) - 1.0

    def days_held(self, as_of: date) -> int:
        return (as_of - self.acquired_at).days

    def is_long_term(self, as_of: date) -> bool:
        return is_irs_long_term(self.acquired_at, as_of)


@dataclass
class OpenSpreadPosition:
    spread_id: str
    account_id: str
    account_type: AccountType
    symbol: str
    expiry: str  # YYYY-MM-DD
    short_strike: float
    long_strike: float
    right: str  # "P"
    contracts: int
    entry_credit: float
    max_loss_total: float
    opened_at: date
    short_con_id: Optional[int] = None
    long_con_id: Optional[int] = None


@dataclass
class AccountSnapshot:
    account_id: str
    account_type: AccountType
    nav_usd: float
    cash_usd: float
    buying_power_usd: float
    settled_cash_usd: Optional[float] = None
    positions: Dict[str, float] = field(default_factory=dict)  # symbol -> share qty
    avg_cost: Dict[str, float] = field(default_factory=dict)   # symbol -> avg cost
    open_spreads: List[OpenSpreadPosition] = field(default_factory=list)
    lots: List[TaxLot] = field(default_factory=list)
    broker_option_positions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_open_options_risk_usd(self) -> float:
        return sum(s.max_loss_total for s in self.open_spreads)

    @property
    def effective_cash_usd(self) -> float:
        """Uses settled cash for Roth IRA cash accounts to prevent T+1 Good-Faith Violations."""
        if self.account_type == AccountType.ROTH_IRA and self.settled_cash_usd is not None:
            return min(self.cash_usd, self.settled_cash_usd)
        return self.cash_usd


@dataclass
class MarketRegime:
    as_of: datetime
    spy_spot: float
    xsp_spot: float
    vix: float
    vix3m: float
    rv21_spy_pct: float
    xsp_atm_iv30_pct: Optional[float] = None
    tbill_yield_pct: float = 4.20
    quote_age_seconds: float = 0.0
    prices: Dict[str, float] = field(default_factory=dict)
    ex_dividend_calendar: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    earnings_calendar: Dict[str, date] = field(default_factory=dict)
    live_spread_marks_verified: bool = False  # True when marks come from live IBKR reqTickers

    @property
    def effective_iv30_pct(self) -> float:
        return float(self.xsp_atm_iv30_pct) if self.xsp_atm_iv30_pct is not None else self.vix

    @property
    def vrp_vol_pts(self) -> float:
        return self.effective_iv30_pct - self.rv21_spy_pct

    @property
    def vix_term_ratio(self) -> float:
        # Critic #2 Fix #15: fail-CLOSED if vix3m is missing/non-positive (infinite backwardation!)
        if self.vix3m <= 0.0:
            return float("inf")
        return self.vix / self.vix3m
