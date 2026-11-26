"""Independent fail-closed Pre-Trade & Runtime Risk Firewall."""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import AccountType, BotConfig, ExecutionMode, TrancheConfig
from .ledger import StateLedger
from .models import (
    AccountSnapshot,
    AssetClass,
    IntentType,
    MarketRegime,
    OrderIntent,
    RiskDecision,
    is_irs_long_term,
)


class COIConfigError(RuntimeError):
    """Raised when COI exclusion configuration is missing, disabled, or invalid."""


def load_coi_blocked_tickers(
    coi_path: Path, block_medtech_review: bool = True
) -> Tuple[Set[str], Set[str]]:
    """
    Critic #2 Fix #10: Strictly FAIL-CLOSED COI loader.
    Raises COIConfigError if `coi_exclusions.json` is missing, unreadable, disabled,
    or contains zero excluded tickers so COI exclusions can NEVER silently drop to 0.
    """
    if not coi_path.exists():
        raise COIConfigError(f"FAIL-CLOSED COI ERROR: Missing required COI file at {coi_path}")
    try:
        with open(coi_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise COIConfigError(f"FAIL-CLOSED COI ERROR: Cannot parse {coi_path}: {exc}") from exc

    if not data.get("enabled", False):
        raise COIConfigError(f"FAIL-CLOSED COI ERROR: COI policy is disabled in {coi_path}.")

    excluded = {str(t).strip().upper() for t in data.get("excluded_tickers", []) if str(t).strip()}
    if not excluded:
        raise COIConfigError(f"FAIL-CLOSED COI ERROR: excluded_tickers is empty in {coi_path}.")

    medtech = {
        str(t).strip().upper() for t in data.get("medtech_review_tickers", []) if str(t).strip()
    }
    if block_medtech_review:
        return excluded | medtech, medtech
    return excluded, medtech


def load_fidelity_shadow_positions(
    csv_path: Path,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[date], bool]:
    """
    Critic #2 Fix #25: Case-insensitive Fidelity CSV parser with export-date extraction.
    Returns (holdings_dict, export_date_or_None, file_loaded_bool).
    """
    holdings: Dict[str, Dict[str, Any]] = {}
    export_dt: Optional[date] = None
    if not csv_path.exists():
        return holdings, None, False

    raw_text = csv_path.read_text(encoding="utf-8-sig", errors="replace")
    # Extract footer date like: "Date downloaded Nov-12-2026 11:13 a.m ET"
    m = re.search(r"Date downloaded\s+([A-Za-z]{3}-\d{1,2}-\d{4})", raw_text)
    if m:
        try:
            export_dt = datetime.strptime(m.group(1), "%b-%d-%Y").date()
        except ValueError:
            export_dt = None

    lines = raw_text.splitlines()
    reader = csv.DictReader(lines)
    for raw_row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k is not None}
        sym_raw = row.get("symbol", "").upper().replace("*", "")
        acct_name = row.get("account name", "")
        if not sym_raw or len(sym_raw) > 10 or sym_raw.startswith('"'):
            continue
        if any(ch.isdigit() for ch in sym_raw) or sym_raw in {"FDRXX", "FDLXX", "FZDXX", "CORE"}:
            continue
        val_str = row.get("current value", "").replace("$", "").replace(",", "")
        qty_str = row.get("quantity", "").replace(",", "")
        try:
            val = float(val_str) if val_str and val_str != "--" else 0.0
            qty = float(qty_str) if qty_str and qty_str != "--" else 0.0
        except ValueError:
            continue
        if qty <= 0.0 and val <= 0.0:
            continue
        rec = holdings.setdefault(sym_raw, {"total_value_usd": 0.0, "total_qty": 0.0, "accounts": []})
        rec["total_value_usd"] += val
        rec["total_qty"] += qty
        if acct_name and acct_name not in rec["accounts"]:
            rec["accounts"].append(acct_name)
    return holdings, export_dt, True


def is_clean_option_strike(strike: float, step: float = 0.50) -> bool:
    """Rejects adjusted/split legacy strikes (e.g. QQQ 579.78) that have broken liquidity."""
    if not math.isfinite(strike) or strike <= 0.0:
        return False
    rem = abs((strike / step) - round(strike / step))
    return rem < 1e-4


class RiskFirewall:
    """
    Fail-closed pre-trade and compliance firewall.
    Every single OrderIntent MUST pass `evaluate()` before any broker call.
    """

    def __init__(self, config: BotConfig, ledger: StateLedger) -> None:
        self.config = config
        self.ledger = ledger
        coi_file = config.resolve_path(config.risk.coi_exclusions_path)
        self.coi_blocked, self.medtech_tickers = load_coi_blocked_tickers(
            coi_file, block_medtech_review=config.risk.block_medtech_review
        )
        fid_csv = config.resolve_path(config.risk.fidelity_positions_csv)
        self.fidelity_shadow, self.fidelity_export_date, self.fidelity_csv_loaded = (
            load_fidelity_shadow_positions(fid_csv)
        )
        self._tranche_map: Dict[str, TrancheConfig] = {t.account_id: t for t in config.tranches}

    def _verify_risk_reducing_claim(self, intent: OrderIntent, snapshot: AccountSnapshot) -> Tuple[bool, Optional[str]]:
        """
        Critic #2 Fix #16: Never blindly trust caller-supplied `is_risk_reducing=True`.
        Verify against intent_type AND actual open position / spread inventory!
        """
        if not intent.is_risk_reducing:
            return False, None
        if intent.intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD:
            matching = [s for s in snapshot.open_spreads if s.spread_id == intent.spread_id]
            if not matching:
                return False, f"Invalid risk-reducing close: spread_id '{intent.spread_id}' not in open_spreads."
            if intent.quantity > matching[0].contracts:
                return False, (
                    f"Over-closing spread '{intent.spread_id}': qty {intent.quantity} > "
                    f"open contracts {matching[0].contracts}."
                )
            return True, None
        if intent.intent_type in {IntentType.CORE_REBALANCE_SELL, IntentType.TLH_SELL, IntentType.CASH_SWEEP_SELL}:
            if intent.action != "SELL":
                return False, f"Risk-reducing equity intent {intent.intent_type.value} must be SELL."
            held = snapshot.positions.get(intent.symbol.upper(), 0.0)
            if intent.quantity > held + 1e-6:
                return False, f"Cannot reduce {intent.symbol}: qty {intent.quantity} > held {held}."
            return True, None
        return False, f"Intent type {intent.intent_type.value} cannot be flagged is_risk_reducing=True."

    def evaluate(
        self,
        intent: OrderIntent,
        snapshot: AccountSnapshot,
        regime: MarketRegime,
    ) -> RiskDecision:
        reasons: List[str] = []
        warnings: List[str] = []
        tranche = self._tranche_map.get(intent.account_id)
        sym = intent.symbol.upper()

        if tranche is None:
            return RiskDecision(False, [f"Unknown account_id '{intent.account_id}' not in config.tranches."])
        if intent.account_type != tranche.account_type:
            reasons.append(
                f"Account type mismatch: intent={intent.account_type.value} vs tranche={tranche.account_type.value}."
            )

        verified_risk_reducing, rr_err = self._verify_risk_reducing_claim(intent, snapshot)
        if rr_err:
            reasons.append(rr_err)

        # ------------------------------------------------------------------
        # 1. Basic Data Sanity, NaN & Stale-Quote Protection
        # ------------------------------------------------------------------
        if intent.quantity <= 0:
            reasons.append(f"Invalid quantity: {intent.quantity}")
        if not math.isfinite(intent.limit_price) or intent.limit_price <= 0.0:
            reasons.append(f"Invalid non-finite or non-positive limit_price: {intent.limit_price}")
        if not math.isfinite(snapshot.nav_usd) or snapshot.nav_usd <= 0.0:
            reasons.append(f"Invalid account NAV: {snapshot.nav_usd}")

        # Critic #2 Fix #15: Stale quote guard (exempt verified exits priced off live IBKR marks!)
        skip_stale = verified_risk_reducing and (
            intent.intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD and regime.live_spread_marks_verified
        )
        if (
            self.config.mode != ExecutionMode.DRY_RUN
            and not skip_stale
            and (not math.isfinite(regime.quote_age_seconds) or regime.quote_age_seconds > self.config.risk.max_quote_age_seconds)
        ):
            reasons.append(
                f"Stale or missing market data (age={regime.quote_age_seconds}s > "
                f"{self.config.risk.max_quote_age_seconds}s max)."
            )

        # ------------------------------------------------------------------
        # 2. Mode/Account Safety, Live Opt-In & Idempotency Gate
        # ------------------------------------------------------------------
        if self.config.mode == ExecutionMode.LIVE:
            soak = self.ledger.get_paper_soak_metrics(self.config.paper_soak_db_path)
            reasons.extend(self.config.validate_live_safety(soak["paper_days_logged"]))
            if intent.is_proxy_chain:
                reasons.append("LIVE BLOCK: Cannot place live option orders using proxy option chain data.")
        elif self.config.mode == ExecutionMode.PAPER:
            reasons.extend(self.config.validate_mode_account_safety())
            if intent.is_proxy_chain:
                reasons.append("PAPER BLOCK: Cannot submit paper IBKR option orders from proxy chain data.")

        if self.ledger.has_active_or_completed_order(intent.idempotency_key):
            reasons.append(f"Duplicate idempotency_key already reserved/submitted/filled: {intent.idempotency_key}")

        # ------------------------------------------------------------------
        # 3. COI Sector Exclusion (Biotech/Pharma + optional MedTech)
        # ------------------------------------------------------------------
        if not verified_risk_reducing and sym in self.coi_blocked:
            tag = "MedTech Review" if sym in self.medtech_tickers else "Biotech/Pharma COI"
            reasons.append(f"COI HARD BLOCK ({tag}): Symbol '{sym}' is excluded by household COI policy.")
        for leg in intent.legs:
            if not verified_risk_reducing and leg.symbol.upper() in self.coi_blocked:
                reasons.append(f"COI HARD BLOCK: Option leg '{leg.symbol}' is excluded by household COI policy.")

        # ------------------------------------------------------------------
        # 4. Critic #1 Banned Strategies, Instruments & Strict ETF Allowlist
        # ------------------------------------------------------------------
        if intent.intent_type == IntentType.SINGLE_STOCK_OPTION and not tranche.allow_single_stock_options:
            reasons.append("CRITIC #1 HARD BLOCK: Automated single-stock options are disabled.")
        if intent.intent_type == IntentType.SINGLE_STOCK_CHURN and not tranche.allow_automated_single_stock_churn:
            reasons.append("CRITIC #1 HARD BLOCK: Automated single-stock equity churn is disabled (advisory only).")
        if intent.asset_class == AssetClass.OPTION_SINGLE and not verified_risk_reducing:
            reasons.append("CRITIC #1 HARD BLOCK: Naked/single-leg options are prohibited (defined-risk combos only).")
        if intent.asset_class == AssetClass.EQUITY and intent.action == "BUY":
            if sym not in tranche.allowed_equity_symbols:
                reasons.append(
                    f"ETF ALLOWLIST BLOCK: '{sym}' is not in {tranche.account_type.value} allowed symbols "
                    f"{sorted(tranche.allowed_equity_symbols)}."
                )
        if intent.asset_class == AssetClass.OPTION_COMBO:
            if sym in self.config.xsp.banned_symbols:
                reasons.append(f"CRITIC #1 HARD BLOCK: Option symbol '{sym}' is banned (thin liquidity).")
            if not verified_risk_reducing and sym not in self.config.xsp.allowed_symbols:
                reasons.append(
                    f"CRITIC #1 HARD BLOCK: Option symbol '{sym}' not in allowed_symbols "
                    f"{self.config.xsp.allowed_symbols}."
                )
            if not tranche.allow_xsp_overlay and not verified_risk_reducing:
                reasons.append(f"Options overlay disabled on account {intent.account_id}.")

        # ------------------------------------------------------------------
        # 5. Circuit Breaker & Daily Kill Switch (Critic #2 Fix #13: TLH Pair Symmetry!)
        # ------------------------------------------------------------------
        daily_dd, dd_30 = self.ledger.get_rolling_drawdowns(
            intent.account_id, snapshot.nav_usd, intent.trade_date
        )
        # Allow XSP stop-loss/TP closes and Roth trims during kill switch,
        # BUT block TLH_SELL during kill switch so a crash never liquidates core ETFs into cash!
        if daily_dd <= self.config.risk.daily_drawdown_halt_pct:
            is_emergency_exit = verified_risk_reducing and intent.intent_type != IntentType.TLH_SELL
            if not is_emergency_exit:
                reasons.append(
                    f"DAILY KILL SWITCH: Account daily drawdown {daily_dd*100:.2f}% <= "
                    f"{self.config.risk.daily_drawdown_halt_pct*100:.2f}% (blocking {intent.intent_type.value})."
                )
        if (
            intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD
            and dd_30 <= self.config.xsp.circuit_breaker_30d_dd
        ):
            reasons.append(
                f"OPTIONS CIRCUIT BREAKER: 30d drawdown {dd_30*100:.2f}% <= "
                f"{self.config.xsp.circuit_breaker_30d_dd*100:.2f}%."
            )

        # ------------------------------------------------------------------
        # 6. IRS Rev. Rul. 2008-5 FULL 61-Day Global Cross-Account Wash-Sale Guard
        # ------------------------------------------------------------------
        is_acquisition = (
            (intent.asset_class == AssetClass.EQUITY and intent.action == "BUY")
            or (intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD)
        )
        if is_acquisition and not verified_risk_reducing:
            wash = self.ledger.get_active_wash_sale_block(sym, intent.trade_date)
            if wash is not None:
                reasons.append(
                    f"IRS WASH-SALE BLOCK (Forward 30d, Rev. Rul. 2008-5): Cannot acquire '{sym}' in "
                    f"{intent.account_type.value} until after {wash['expires_date']} "
                    f"(loss realized in {wash['loss_account_id']} on {wash['loss_date']})."
                )
            # Critic #2 Fix #25: Block/warn buying symbols already held at Fidelity in IBKR
            if intent.asset_class == AssetClass.EQUITY and sym in self.fidelity_shadow:
                reasons.append(
                    f"FIDELITY SEGREGATION GUARD: Refusing to buy '{sym}' in IBKR because it is held "
                    f"at Fidelity ({self.fidelity_shadow[sym]['accounts']}) — preserves zero cross-broker wash-sale overlap."
                )

        # Critic #2 Fix #8: BACKWARD 30-day check before ANY Taxable TLH_SELL!
        if intent.intent_type == IntentType.TLH_SELL:
            prior_acq = self.ledger.get_recent_acquisition(
                symbol=sym,
                as_of=intent.trade_date,
                window_days=30,
                exclude_lot_id=intent.lot_id,
            )
            if prior_acq is not None:
                reasons.append(
                    f"IRS WASH-SALE BLOCK (Backward 30d, IRC §1091 / Rev. Rul. 2008-5): "
                    f"Cannot TLH-sell '{sym}' because account '{prior_acq['account_id']}' "
                    f"({prior_acq['account_type']}) acquired '{sym}' on {prior_acq['acquired_at']} (within prior 30d)."
                )
            # Fidelity CSV presence & staleness check (fail-closed!)
            if self.config.risk.require_fidelity_csv_for_tlh:
                if not self.fidelity_csv_loaded:
                    reasons.append(
                        f"FIDELITY SHADOW FAIL-CLOSED: Missing {self.config.risk.fidelity_positions_csv}; "
                        f"cannot verify zero cross-broker DRIP wash-sale risk for TLH_SELL."
                    )
                elif (
                    self.fidelity_export_date is not None
                    and (intent.trade_date - self.fidelity_export_date).days > self.config.risk.fidelity_csv_max_age_days
                ):
                    reasons.append(
                        f"FIDELITY SHADOW STALE: Positions CSV dated {self.fidelity_export_date} is "
                        f"{(intent.trade_date - self.fidelity_export_date).days}d old (> "
                        f"{self.config.risk.fidelity_csv_max_age_days}d cap); refresh before TLH_SELL."
                    )
            if sym in self.fidelity_shadow:
                reasons.append(
                    f"FIDELITY DRIP WASH-SALE GUARD: '{sym}' is held at Fidelity "
                    f"({self.fidelity_shadow[sym]['accounts']}); harvesting a loss in IBKR risks "
                    f"a cross-broker wash sale if Fidelity DRIP reinvests within 30 days."
                )

            # Critic #2 Fix #9: IBKR FIFO-vs-Specific-ID TLH protection!
            if not tranche.ibkr_tax_optimizer_spec_id_enabled and intent.lot_id:
                sym_lots = [l for l in snapshot.lots if l.symbol == sym]
                target_lots = [l for l in sym_lots if l.lot_id == intent.lot_id]
                if target_lots:
                    t_lot = target_lots[0]
                    spot_px = regime.prices.get(sym, intent.limit_price)
                    older_appreciated = [
                        l for l in sym_lots
                        if l.lot_id != t_lot.lot_id
                        and l.acquired_at <= t_lot.acquired_at
                        and l.unrealized_pnl(spot_px) > 0.0
                    ]
                    if older_appreciated:
                        reasons.append(
                            f"IBKR FIFO TAX TRAP: Older appreciated lot '{older_appreciated[0].lot_id}' "
                            f"exists for '{sym}'. Enable IBKR Tax Optimizer (HighestCost/TaxLossHarvester) "
                            f"and set ibkr_tax_optimizer_spec_id_enabled=True before partial-position TLH."
                        )

        # ------------------------------------------------------------------
        # 7. Taxable LTCG Protection (Fail-CLOSED + Exact IRS Calendar-Year Rule!)
        # ------------------------------------------------------------------
        if intent.account_type == AccountType.TAXABLE and intent.intent_type == IntentType.CORE_REBALANCE_SELL:
            if intent.lot_acquired_date is None or intent.lot_unrealized_pnl_usd is None:
                reasons.append(
                    "TAXABLE LTCG FAIL-CLOSED: CORE_REBALANCE_SELL requires explicit lot_acquired_date "
                    "and lot_unrealized_pnl_usd to verify >1yr LTCG status."
                )
            elif not is_irs_long_term(intent.lot_acquired_date, intent.trade_date) and intent.lot_unrealized_pnl_usd > 0.0:
                reasons.append(
                    f"TAXABLE LTCG GUARD (IRC §1222(3)): Refusing to sell appreciated lot '{intent.lot_id}' "
                    f"acquired {intent.lot_acquired_date} (<= 1 full calendar year -> 44.8% STCG)."
                )

        if intent.legs:
            self._validate_option_legs(intent, snapshot, regime, verified_risk_reducing, reasons)

        # ------------------------------------------------------------------
        # 8. Equity Order Sizing, Cash Buffer (incl. TLH_BUY!) & Concentration Cap
        # ------------------------------------------------------------------
        if intent.asset_class == AssetClass.EQUITY:
            notional = intent.quantity * intent.limit_price
            if not verified_risk_reducing and intent.intent_type not in {IntentType.TLH_BUY, IntentType.CASH_SWEEP_BUY}:
                if notional < self.config.risk.min_order_notional_usd:
                    reasons.append(
                        f"Order notional ${notional:.2f} < min ${self.config.risk.min_order_notional_usd:.2f}."
                    )
            if intent.action == "BUY":
                # Critic #2 Fix #16: Enforce cash check on ALL equity buys including TLH_BUY!
                # For TLH_BUY, allow spending up to effective cash (minus $100 floor) since TLH_SELL just settled proceeds.
                buf = 100.0 if intent.intent_type == IntentType.TLH_BUY else tranche.min_cash_buffer_usd
                avail_cash = max(0.0, snapshot.effective_cash_usd - buf)
                if notional > avail_cash + 1.0:
                    reasons.append(
                        f"Insufficient settled cash after ${buf:.0f} buffer: "
                        f"need ${notional:.2f}, available ${avail_cash:.2f}."
                    )
                if notional > snapshot.buying_power_usd + 1.0:
                    reasons.append(
                        f"Insufficient buying power: need ${notional:.2f}, available ${snapshot.buying_power_usd:.2f}."
                    )
                if (
                    intent.intent_type not in {IntentType.CASH_SWEEP_BUY, IntentType.TLH_BUY}
                    and len(snapshot.positions) > 0
                    and notional > snapshot.nav_usd * self.config.risk.max_single_order_pct_nav
                ):
                    reasons.append(
                        f"MAX SINGLE ORDER CAP: ${notional:.2f} > "
                        f"{self.config.risk.max_single_order_pct_nav*100:.0f}% NAV."
                    )
            if intent.action == "SELL":
                held_qty = snapshot.positions.get(sym, 0.0)
                if intent.quantity > held_qty + 1e-6:
                    reasons.append(
                        f"Short-sale block: Trying to sell {intent.quantity} {sym}, only hold {held_qty}."
                    )

        # ------------------------------------------------------------------
        # 9. XSP Credit Spread VRP, Liquidity, Risk Budget & Stagger Gate
        # ------------------------------------------------------------------
        if intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD:
            self._validate_xsp_open(intent, snapshot, regime, tranche, reasons)

        return RiskDecision(approved=(len(reasons) == 0), reasons=reasons, warnings=warnings)

    def _validate_option_legs(
        self,
        intent: OrderIntent,
        snapshot: AccountSnapshot,
        regime: MarketRegime,
        verified_risk_reducing: bool,
        reasons: List[str],
    ) -> None:
        if len(intent.legs) < 2 and not verified_risk_reducing:
            reasons.append("Defined-risk combo requires at least 2 legs.")
        for leg in intent.legs:
            if leg.multiplier != 100:
                reasons.append(f"Non-standard option multiplier {leg.multiplier} on {leg.symbol} (must be 100).")
            if leg.ratio != 1:
                reasons.append(f"Non-unit leg ratio {leg.ratio} on {leg.symbol} (must be 1:1 defined spread).")
            if not is_clean_option_strike(leg.strike):
                reasons.append(f"Adjusted/non-standard strike {leg.strike} on {leg.symbol}.")
            if not verified_risk_reducing and (leg.bid <= 0.0 or leg.ask <= 0.0 or leg.ask < leg.bid):
                reasons.append(f"Broken bid/ask ({leg.bid}/{leg.ask}) on {leg.symbol} {leg.strike}{leg.right}.")

            # Defense-in-depth: Earnings blackout on non-index tickers
            if not verified_risk_reducing and leg.symbol.upper() not in {"XSP", "SPX", "SPY", "QQQ", "IWM"}:
                earn_dt = regime.earnings_calendar.get(leg.symbol.upper())
                if earn_dt is not None:
                    exp_dt = date.fromisoformat(
                        f"{leg.ibkr_expiry[:4]}-{leg.ibkr_expiry[4:6]}-{leg.ibkr_expiry[6:8]}"
                    )
                    if intent.trade_date <= earn_dt <= exp_dt + timedelta(days=self.config.risk.earnings_blackout_buffer_days):
                        reasons.append(
                            f"EARNINGS BLACKOUT: {leg.symbol} reports on {earn_dt}, inside expiry {exp_dt}."
                        )

            # Defense-in-depth: IRS IRC §1092(c)(4) QCC short-call guard using EXACT IRS calendar-year check!
            if (
                not verified_risk_reducing
                and intent.account_type == AccountType.TAXABLE
                and leg.right == "C"
                and leg.action == "SELL"
            ):
                young_lots = [
                    l for l in snapshot.lots
                    if l.symbol == leg.symbol.upper() and not l.is_long_term(intent.trade_date)
                ]
                if young_lots:
                    spot = regime.prices.get(leg.symbol.upper(), 0.0)
                    if (intent.dte_days is not None and intent.dte_days <= self.config.risk.qcc_min_call_dte_days) or (
                        spot > 0.0 and leg.strike < spot
                    ):
                        reasons.append(
                            f"IRS §1092(c)(4) QCC GUARD: Short call on Taxable non-LTCG '{leg.symbol}' "
                            f"(DTE={intent.dte_days}, K={leg.strike}, spot={spot:.2f}) risks suspending LTCG clock."
                        )

            # Defense-in-depth: Ex-dividend early assignment guard on American short calls
            if (
                not verified_risk_reducing
                and not leg.is_european
                and leg.right == "C"
                and leg.action == "SELL"
            ):
                div_info = regime.ex_dividend_calendar.get(leg.symbol.upper())
                if div_info:
                    ex_dt = div_info.get("ex_date")
                    div_amt = float(div_info.get("amount", 0.0))
                    spot = regime.prices.get(leg.symbol.upper(), 0.0)
                    if isinstance(ex_dt, date) and 0 <= (ex_dt - intent.trade_date).days <= self.config.risk.ex_div_call_guard_days:
                        intrinsic = max(0.0, spot - leg.strike)
                        extrinsic = max(0.0, leg.mid - intrinsic)
                        if extrinsic < self.config.risk.ex_div_extrinsic_mult * div_amt:
                            reasons.append(
                                f"EX-DIV ASSIGNMENT GUARD: Short American call {leg.symbol} {leg.strike}C "
                                f"extrinsic ${extrinsic:.2f} < {self.config.risk.ex_div_extrinsic_mult}x div ${div_amt:.2f} "
                                f"before ex-date {ex_dt}."
                            )

    def _validate_xsp_open(
        self,
        intent: OrderIntent,
        snapshot: AccountSnapshot,
        regime: MarketRegime,
        tranche: TrancheConfig,
        reasons: List[str],
    ) -> None:
        cfg = self.config.xsp

        # 1. Fail-CLOSED Live Real-VIX / VRP / Term-Structure Gate
        if not math.isfinite(regime.vix) or regime.vix < cfg.min_vix:
            reasons.append(f"VIX GATE: VIX {regime.vix} < min {cfg.min_vix:.2f}.")
        if not math.isfinite(regime.vrp_vol_pts) or regime.vrp_vol_pts < cfg.min_vrp_vol_pts:
            reasons.append(
                f"VRP GATE: Live VRP {regime.vrp_vol_pts:+.2f} pts "
                f"(IV30={regime.effective_iv30_pct:.1f}% - RV21={regime.rv21_spy_pct:.1f}%) "
                f"< min +{cfg.min_vrp_vol_pts:.1f} pts."
            )
        if not math.isfinite(regime.vix_term_ratio) or regime.vix_term_ratio > cfg.max_vix_term_ratio:
            reasons.append(
                f"TERM-STRUCTURE GATE: VIX/VIX3M {regime.vix_term_ratio:.3f} > max {cfg.max_vix_term_ratio:.2f} "
                f"(backwardation or missing VIX3M)."
            )

        # 2. Structural Integrity, Leg Alignment, Delta Cap & Credit Floor (validated on `intent.limit_price`!)
        if len(intent.legs) != 2:
            reasons.append(f"XSP Put Credit Spread must have exactly 2 legs, got {len(intent.legs)}.")
            return

        short_legs = [l for l in intent.legs if l.action == "SELL" and l.right == "P"]
        long_legs = [l for l in intent.legs if l.action == "BUY" and l.right == "P"]
        if len(short_legs) != 1 or len(long_legs) != 1:
            reasons.append("XSP combo must contain 1 SELL Put and 1 BUY Put.")
            return
        s_leg, l_leg = short_legs[0], long_legs[0]

        if s_leg.symbol.upper() != "XSP" or l_leg.symbol.upper() != "XSP":
            reasons.append(f"Non-XSP leg symbol detected: {s_leg.symbol}/{l_leg.symbol}.")
        if not (s_leg.is_european and l_leg.is_european):
            reasons.append("Both legs of XSP spread must be European cash-settled.")
        if s_leg.ibkr_expiry != l_leg.ibkr_expiry:
            reasons.append(f"Mismatched leg expiries: {s_leg.ibkr_expiry} vs {l_leg.ibkr_expiry} (diagonal blocked).")

        width = s_leg.strike - l_leg.strike
        if width < cfg.min_spread_width or width > cfg.max_spread_width:
            reasons.append(
                f"Spread width ${width:.2f} outside allowed [${cfg.min_spread_width:.2f}, ${cfg.max_spread_width:.2f}]."
            )
        if regime.xsp_spot > 0.0 and s_leg.strike >= regime.xsp_spot:
            reasons.append(f"ITM/ATM short put blocked: strike {s_leg.strike} >= XSP spot {regime.xsp_spot:.2f}.")
        if s_leg.delta is not None and abs(s_leg.delta) > cfg.max_short_delta:
            reasons.append(
                f"Short put |delta| {abs(s_leg.delta):.2f} > max allowed {cfg.max_short_delta:.2f}."
            )
        if intent.dte_days is None or not (cfg.min_dte <= intent.dte_days <= cfg.max_dte):
            reasons.append(f"DTE {intent.dte_days} outside [{cfg.min_dte}, {cfg.max_dte}].")

        # Critic #2 Fix #16: Validate `intent.limit_price` (the actual order credit submitted to IBKR!)
        submitted_credit = float(intent.limit_price)
        if submitted_credit < cfg.min_net_credit:
            reasons.append(
                f"Submitted limit credit ${submitted_credit:.2f} < min ${cfg.min_net_credit:.2f} "
                f"(commission drag guard)."
            )
        if submitted_credit >= width:
            reasons.append(f"Invalid limit credit ${submitted_credit:.2f} >= width ${width:.2f}.")

        for leg in (s_leg, l_leg):
            if leg.open_interest < cfg.min_open_interest:
                reasons.append(
                    f"Low OI ({leg.open_interest} < {cfg.min_open_interest}) on XSP {leg.strike}P."
                )
            if leg.spread_width_usd > cfg.max_leg_bid_ask_usd:
                reasons.append(
                    f"Wide bid-ask ${leg.spread_width_usd:.2f} > max ${cfg.max_leg_bid_ask_usd:.2f} "
                    f"on XSP {leg.strike}P."
                )
        total_ba = s_leg.spread_width_usd + l_leg.spread_width_usd
        if submitted_credit > 0 and (total_ba / submitted_credit) > cfg.max_bid_ask_pct_of_credit:
            reasons.append(
                f"Combo bid-ask ${total_ba:.2f} is {total_ba/submitted_credit*100:.1f}% of credit "
                f"(> {cfg.max_bid_ask_pct_of_credit*100:.0f}% cap)."
            )

        # 3. Hard Risk Caps, Concurrency, Stagger, Buying Power & Collateral Check
        exact_max_loss = max(0.0, (width - submitted_credit) * 100.0 * intent.quantity)
        max_per_trade = snapshot.nav_usd * cfg.max_risk_per_trade_pct
        max_total_risk = snapshot.nav_usd * cfg.max_total_options_risk_pct
        cur_open_risk = snapshot.total_open_options_risk_usd

        if exact_max_loss > max_per_trade:
            reasons.append(
                f"MAX-RISK-PER-TRADE CAP: Spread max loss ${exact_max_loss:.2f} > "
                f"{cfg.max_risk_per_trade_pct*100:.1f}% NAV (${max_per_trade:.2f})."
            )
        if (cur_open_risk + exact_max_loss) > max_total_risk:
            reasons.append(
                f"MAX-TOTAL-OPTIONS-RISK CAP: Post-trade risk ${cur_open_risk + exact_max_loss:.2f} > "
                f"{cfg.max_total_options_risk_pct*100:.1f}% NAV (${max_total_risk:.2f})."
            )
        if len(snapshot.open_spreads) >= cfg.max_concurrent_spreads:
            reasons.append(
                f"MAX CONCURRENT SPREADS: Already hold {len(snapshot.open_spreads)}/{cfg.max_concurrent_spreads}."
            )

        last_entry = self.ledger.get_last_spread_entry_date(intent.account_id, "XSP")
        if last_entry is not None:
            days_since = (intent.trade_date - last_entry).days
            if days_since < cfg.min_entry_stagger_days:
                reasons.append(
                    f"ENTRY STAGGER GUARD: Last XSP entry was {days_since}d ago (< {cfg.min_entry_stagger_days}d)."
                )

        if exact_max_loss > snapshot.buying_power_usd:
            reasons.append(
                f"BUYING POWER SHORTFALL: Max loss ${exact_max_loss:.2f} > buying power ${snapshot.buying_power_usd:.2f}."
            )

        sweep_val = snapshot.positions.get(tranche.cash_sweep_etf, 0.0) * regime.prices.get(tranche.cash_sweep_etf, 0.0)
        eff_collat = snapshot.effective_cash_usd + sweep_val * (1.0 - cfg.collateral_haircut_pct) - tranche.min_cash_buffer_usd
        if (cur_open_risk + exact_max_loss) > eff_collat:
            reasons.append(
                f"COLLATERAL SHORTFALL: Need ${cur_open_risk + exact_max_loss:.2f}, "
                f"effective T-Bill/cash collateral is ${eff_collat:.2f}."
            )
