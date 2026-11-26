"""Configuration models and hard safety boundaries for the dual-tranche IBKR bot."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

from pydantic import BaseModel, Field, field_validator, model_validator


class ExecutionMode(str, Enum):
    DRY_RUN = "dry-run"
    PAPER = "paper"
    LIVE = "live"


class AccountType(str, Enum):
    ROTH_IRA = "ROTH_IRA"
    TAXABLE = "TAXABLE"


PAPER_PORTS = {4002, 7497}
LIVE_PORTS = {4001, 7496}
PAPER_ACCOUNT_PREFIXES = ("DU", "DF")
LIVE_ENV_TOKEN_NAME = "IBKR_LIVE_CONFIRM_TOKEN"
LIVE_ENV_TOKEN_VALUE = "DEPLOY_LIVE_IBKR"


class TaxProfile(BaseModel):
    """High-earner MFJ tax rates driving Roth vs. Taxable routing decisions."""

    fed_stcg_rate: float = Field(default=0.388, ge=0.0, le=0.60)  # 35% Fed + 3.8% NIIT
    fed_ltcg_rate: float = Field(default=0.238, ge=0.0, le=0.50)  # 20% Fed + 3.8% NIIT
    state_tax_rate: float = Field(default=0.060, ge=0.0, le=0.15)  # Configurable state marginal rate

    @property
    def combined_stcg_rate(self) -> float:
        return self.fed_stcg_rate + self.state_tax_rate

    @property
    def combined_ltcg_rate(self) -> float:
        return self.fed_ltcg_rate + self.state_tax_rate

    @property
    def sec1256_blended_rate(self) -> float:
        """Section 1256 automatic 60% LTCG / 40% STCG regardless of holding period."""
        return 0.60 * self.combined_ltcg_rate + 0.40 * self.combined_stcg_rate

    @property
    def tbill_effective_rate(self) -> float:
        """Direct U.S. Treasuries / SGOV are exempt from state and local income tax."""
        return self.fed_stcg_rate


class IBKRConnectionConfig(BaseModel):
    """IB Gateway / TWS connection parameters with hard Paper-vs-Live port guards."""

    host: str = "127.0.0.1"
    port: int = 4002  # Default: IB Gateway Paper Trading
    client_id: int = 101
    timeout_seconds: float = 15.0
    max_order_wait_seconds: float = 20.0
    cancel_confirm_timeout_seconds: float = 10.0
    commission_wait_seconds: float = 2.0
    adaptive_price_steps: int = 3
    adaptive_step_dollars: float = 0.01

    def validate_for_mode(self, mode: ExecutionMode) -> None:
        if mode == ExecutionMode.PAPER and self.port not in PAPER_PORTS:
            raise ValueError(
                f"SAFETY BLOCK: PAPER mode requires port in {sorted(PAPER_PORTS)}, got {self.port}."
            )
        if mode == ExecutionMode.LIVE and self.port not in LIVE_PORTS:
            raise ValueError(
                f"SAFETY BLOCK: LIVE mode requires port in {sorted(LIVE_PORTS)}, got {self.port}."
            )


class TrancheConfig(BaseModel):
    """Per-account allocation, ETF segregation ring, and strict strategy switches."""

    account_id: str
    account_type: AccountType
    initial_nav_usd: float = Field(gt=0.0)
    core_equity_targets: Dict[str, float]
    tlh_rings: List[List[str]] = Field(default_factory=list)
    cash_sweep_etf: str
    min_cash_buffer_usd: float = Field(default=500.0, ge=100.0)
    target_cash_reserve_pct: float = Field(default=0.20, ge=0.05, le=0.95)
    rebalance_drift_band: float = Field(default=0.05, ge=0.01, le=0.25)
    # Critic #2 Fix #9: IBKR defaults to FIFO; partial-position specific-lot TLH is blocked
    # unless the user explicitly verifies IBKR Tax Optimizer is set to HighestCost / TaxLossHarvester.
    ibkr_tax_optimizer_spec_id_enabled: bool = False
    # Critic #1 hard kill switches (disabled by default!)
    allow_single_stock_options: bool = False
    allow_automated_single_stock_churn: bool = False
    allow_xsp_overlay: bool = True

    @property
    def allowed_equity_symbols(self) -> Set[str]:
        """Strict per-tranche ETF allowlist (prevents arbitrary single-stock buys)."""
        syms: Set[str] = {k.upper() for k in self.core_equity_targets}
        syms.add(self.cash_sweep_etf.upper())
        for ring in self.tlh_rings:
            syms.update(s.upper() for s in ring)
        return syms

    @model_validator(mode="after")
    def _validate_weights(self) -> "TrancheConfig":
        eq_sum = sum(self.core_equity_targets.values())
        if eq_sum < 0.0 or eq_sum > 1.0:
            raise ValueError(f"core_equity_targets sum must be in [0, 1], got {eq_sum:.4f}")
        if abs((eq_sum + self.target_cash_reserve_pct) - 1.0) > 0.02:
            raise ValueError(
                f"core_equity_targets ({eq_sum:.2f}) + target_cash_reserve_pct "
                f"({self.target_cash_reserve_pct:.2f}) must sum to ~1.00."
            )
        return self


class XSPOverlayConfig(BaseModel):
    """
    Strictly gated European cash-settled XSP Put Credit Spread overlay parameters.
    Enforces every Critic #1 and Critic #2 kill/fix/cap directive.
    """

    allowed_symbols: List[str] = Field(default_factory=lambda: ["XSP"])
    banned_symbols: List[str] = Field(default_factory=lambda: ["XND"])
    min_vix: float = 20.0
    min_vrp_vol_pts: float = 4.0  # Live ATM IV30 (or ^VIX) minus RV21(SPY) >= +4.0 pts
    max_vix_term_ratio: float = 1.05  # VIX / VIX3M <= 1.05 (avoid backwardation crash)
    target_short_delta: float = 0.20
    max_short_delta: float = 0.30  # Hard upper delta ceiling (prevents near-ATM strike selection)
    spread_width: float = 10.0  # Proven $10-wide beats $5-wide after commissions/slippage
    min_spread_width: float = 8.0  # Blocks money-losing $5-wide XSP spreads!
    max_spread_width: float = 10.0  # Hard upper wing width cap (Critic #2 Fix #19)
    min_dte: int = 35
    max_dte: int = 55
    ideal_dte: int = 45
    min_net_credit: float = 0.80  # Keeps IBKR $2.60 RT comm < 3.5% of gross credit
    max_leg_bid_ask_usd: float = 0.08
    max_bid_ask_pct_of_credit: float = 0.12
    min_open_interest: int = 200
    take_profit_pct: float = 0.50
    time_exit_dte: int = 21
    stop_loss_credit_mult: float = 2.0
    max_risk_per_trade_pct: float = 0.025  # 2.5% NAV max loss per trade
    max_total_options_risk_pct: float = 0.10  # 10% NAV max total open spread risk
    max_concurrent_spreads: int = 2
    min_entry_stagger_days: int = 5
    collateral_haircut_pct: float = 0.05  # Conservative 5% IBKR SGOV/T-Bill haircut
    circuit_breaker_30d_dd: float = -0.05  # Halt new entries on -5% rolling 30d DD


class RiskConfig(BaseModel):
    """Global compliance, tax-safety, and runtime risk limits."""

    coi_exclusions_path: Path = Path("data/coi_exclusions.json")
    block_medtech_review: bool = True  # Block SIBN / ISRG buys until hospital COI confirms
    fidelity_positions_csv: Path = Path("data/Portfolio_Positions_latest.csv")
    fidelity_csv_max_age_days: int = 14  # Fail-closed on TLH_SELL if Fidelity CSV stale/missing
    require_fidelity_csv_for_tlh: bool = True
    wash_sale_window_days: int = 31  # Covers full 61-day window (30d before + trade day + 30d after)
    tlh_loss_trigger_pct: float = -0.05  # Harvest lot at <= -5% unrealized loss
    tlh_min_loss_usd: float = 250.0  # Avoid tiny friction-dominated harvests
    tlh_min_hold_days: int = 7
    min_order_notional_usd: float = 250.0
    max_single_order_pct_nav: float = 0.35
    daily_drawdown_halt_pct: float = -0.03
    max_quote_age_seconds: int = 900
    require_paper_soak_days: int = 90
    qcc_min_call_dte_days: int = 35  # IRC §1092(c)(4) safety buffer above 30d QCC floor
    ex_div_call_guard_days: int = 7
    ex_div_extrinsic_mult: float = 1.5
    earnings_blackout_buffer_days: int = 2


class BotConfig(BaseModel):
    """Top-level configuration container for the Dual-Tranche IBKR Bot."""

    mode: ExecutionMode = ExecutionMode.DRY_RUN
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    db_path: Optional[Path] = None  # Defaults to mode-segregated output/ibkr_bot_{mode}.sqlite3!
    report_dir: Path = Path("output")
    ibkr: IBKRConnectionConfig = Field(default_factory=IBKRConnectionConfig)
    tax: TaxProfile = Field(default_factory=TaxProfile)
    xsp: XSPOverlayConfig = Field(default_factory=XSPOverlayConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    tranches: List[TrancheConfig] = Field(default_factory=list)
    confirm_live: bool = False
    override_paper_soak: bool = False
    allow_proxy_chain_in_dry_run: bool = True

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, v: object) -> object:
        if isinstance(v, str):
            return ExecutionMode(v.lower())
        return v

    @model_validator(mode="after")
    def _validate_unique_accounts_and_db(self) -> "BotConfig":
        # Critic #2 Fix #21: forbid duplicate account_ids across tranches
        seen: Set[str] = set()
        for t in self.tranches:
            aid = t.account_id.strip()
            if not aid:
                raise ValueError("Tranche account_id must be non-empty.")
            if aid in seen:
                raise ValueError(f"Duplicate account_id '{aid}' in config.tranches.")
            seen.add(aid)
        # Critic #2 Fix #5: default DB path segregated strictly by execution mode
        if self.db_path is None:
            safe_mode = self.mode.value.replace("-", "_")
            self.db_path = Path(f"output/ibkr_bot_{safe_mode}.sqlite3")
        return self

    def resolve_path(self, p: Path) -> Path:
        return p if p.is_absolute() else (self.project_root / p)

    @property
    def effective_db_path(self) -> Path:
        assert self.db_path is not None
        return self.resolve_path(self.db_path)

    @property
    def paper_soak_db_path(self) -> Path:
        """Dedicated paper ledger path consulted when validating LIVE paper-soak gate."""
        return self.resolve_path(Path("output/ibkr_bot_paper.sqlite3"))

    def validate_mode_account_safety(self) -> List[str]:
        """Critic #2 Fix #21: verify IBKR paper (DU*/DF*) vs live account prefixes."""
        errors: List[str] = []
        if self.mode == ExecutionMode.PAPER:
            try:
                self.ibkr.validate_for_mode(self.mode)
            except ValueError as exc:
                errors.append(str(exc))
            for t in self.tranches:
                if not t.account_id.upper().startswith(PAPER_ACCOUNT_PREFIXES):
                    errors.append(
                        f"PAPER SAFETY BLOCK: Account '{t.account_id}' does not start with "
                        f"IBKR paper prefix {PAPER_ACCOUNT_PREFIXES}."
                    )
        elif self.mode == ExecutionMode.LIVE:
            for t in self.tranches:
                if t.account_id.upper().startswith(PAPER_ACCOUNT_PREFIXES):
                    errors.append(
                        f"LIVE SAFETY BLOCK: Refusing to run LIVE mode on paper account '{t.account_id}'."
                    )
        return errors

    def validate_live_safety(self, paper_days_completed: int) -> List[str]:
        """Returns list of blocking safety violations if LIVE mode is requested."""
        if self.mode != ExecutionMode.LIVE:
            return []
        errors: List[str] = []
        try:
            self.ibkr.validate_for_mode(self.mode)
        except ValueError as exc:
            errors.append(str(exc))
        errors.extend(self.validate_mode_account_safety())
        if not self.confirm_live:
            errors.append("LIVE mode requires explicit --confirm-live CLI flag.")
        token = os.environ.get(LIVE_ENV_TOKEN_NAME, "")
        if token != LIVE_ENV_TOKEN_VALUE:
            errors.append(
                f"LIVE mode requires environment variable {LIVE_ENV_TOKEN_NAME}={LIVE_ENV_TOKEN_VALUE}."
            )
        if not self.override_paper_soak and paper_days_completed < self.risk.require_paper_soak_days:
            errors.append(
                f"Paper soak incomplete: {paper_days_completed}/{self.risk.require_paper_soak_days} "
                f"paper trading days logged (use --override-paper-soak only after manual review)."
            )
        return errors


def build_default_config(
    mode: ExecutionMode = ExecutionMode.DRY_RUN,
    roth_account_id: str = "DU_ROTH_SIM",
    taxable_account_id: str = "DU_TAXABLE_SIM",
    roth_nav_usd: float = 25_000.0,
    taxable_nav_usd: float = 75_000.0,
    state_tax_rate: float = 0.060,
    port: Optional[int] = None,
    confirm_live: bool = False,
    override_paper_soak: bool = False,
) -> BotConfig:
    """
    Constructs the Critic #1 + #2 compliant Dual-Tranche configuration with ZERO
    structural ETF overlap across Fidelity (VTI/QQQM/EFA/VEU), IBKR Taxable
    (ITOT/SCHB, SCHG/VUG, SGOV), and IBKR Roth (VOO, VGT, USFR).
    """
    default_port = port if port is not None else (4001 if mode == ExecutionMode.LIVE else 4002)
    taxable = TrancheConfig(
        account_id=taxable_account_id,
        account_type=AccountType.TAXABLE,
        initial_nav_usd=taxable_nav_usd,
        core_equity_targets={"ITOT": 0.55, "SCHG": 0.25},
        # Critic #2 Fix #25: Removed IEFA (close MSCI EAFE overlap with Fidelity's EFA!)
        tlh_rings=[["ITOT", "SCHB"], ["SCHG", "VUG"]],
        cash_sweep_etf="SGOV",
        min_cash_buffer_usd=500.0,
        target_cash_reserve_pct=0.20,
        rebalance_drift_band=0.05,
        ibkr_tax_optimizer_spec_id_enabled=False,
        allow_single_stock_options=False,
        allow_automated_single_stock_churn=False,
        allow_xsp_overlay=True,
    )
    roth = TrancheConfig(
        account_id=roth_account_id,
        account_type=AccountType.ROTH_IRA,
        initial_nav_usd=roth_nav_usd,
        core_equity_targets={"VOO": 0.50, "VGT": 0.30},
        tlh_rings=[],
        cash_sweep_etf="USFR",
        min_cash_buffer_usd=500.0,
        target_cash_reserve_pct=0.20,
        rebalance_drift_band=0.05,
        ibkr_tax_optimizer_spec_id_enabled=False,
        allow_single_stock_options=False,
        allow_automated_single_stock_churn=False,
        allow_xsp_overlay=True,
    )
    return BotConfig(
        mode=mode,
        ibkr=IBKRConnectionConfig(port=default_port),
        tax=TaxProfile(state_tax_rate=state_tax_rate),
        tranches=[taxable, roth],
        confirm_live=confirm_live,
        override_paper_soak=override_paper_soak,
    )
