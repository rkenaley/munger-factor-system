"""
Broker abstraction with SimulatedBroker (dry-run/test) and hardened IBKRBroker (`ib_async`).

CRITICAL IBKR API SIGN CONVENTION & HARDENING (Verified against ib_async 2.1.0 + Critic #2):
  - Opening a Put Credit Spread (`XSP_OPEN_CREDIT_SPREAD`):
      BAG legs: SELL short put + BUY long put (tradingClass="XSP", multiplier="100").
      Order:    `LimitOrder(action="BUY", totalQuantity=qty, lmtPrice=-credit)`
      IBKR requires NEGATIVE lmtPrice for BUY credit combos; adaptive stepping is
      hard-capped so it NEVER crosses min_net_credit, 2.5% NAV cap, or 0.00!
  - Closing a Put Credit Spread (`XSP_CLOSE_CREDIT_SPREAD`):
      BAG legs: BUY short put + SELL long put.
      Order:    `LimitOrder(action="BUY", totalQuantity=qty, lmtPrice=+max_debit)`
      Adaptive stepping is hard-capped at `<= spread_width`.
  - Guaranteed SMART Combo Routing:
      `NonGuaranteed` is NEVER set -> guaranteed complex book eliminates single-leg legging.
  - Strict Fail-Closed `whatIfOrder`:
      Enables `RaiseRequestErrors = True`, requires `isinstance(whatif, OrderState)`,
      rejects IBKR `UNSET_DOUBLE` (`>= 1e300`), and allows margin-reducing exits even
      when margin utilization exceeds 95%.
  - Confirmed Cancel & Partial-Fill Preservation:
      Polls `trade.isDone()` after `cancelOrder()`, preserves any partial/racing fills,
      and waits cleanly for IBKR's async `commissionReport`.
  - Live `OPT` Position & Spread Marks:
      Reads `secType == "OPT"` positions for broker-vs-SQLite reconciliation and fetches
      live bid/ask option marks via `reqTickers()` (zero fake BSM in Paper/Live!).
"""

from __future__ import annotations

import math
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from .config import BotConfig, ExecutionMode, TrancheConfig
from .ledger import StateLedger
from .models import (
    AccountSnapshot,
    AssetClass,
    ExecutionResult,
    IntentType,
    MarketRegime,
    OpenSpreadPosition,
    OrderIntent,
)

IBKR_UNSET_THRESHOLD = 1e300


def _parse_ibkr_float(raw: Any) -> Optional[float]:
    """Safely parses IBKR OrderState / commission fields, rejecting UNSET_DOUBLE (~1.797e308)."""
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or abs(v) >= IBKR_UNSET_THRESHOLD:
        return None
    return v


def validate_whatif_order_state(whatif: Any, is_risk_reducing: bool) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Critic #2 Fix #1: Pure, unit-testable fail-closed validator for `ib.whatIfOrder()` results.
    Rejects empty lists `[]`, `None`, non-OrderState objects, and IBKR `UNSET_DOUBLE` sentinels.
    For risk-reducing exits, verifies margin does not increase rather than blocking on >95% margin.
    """
    if whatif is None or isinstance(whatif, (list, tuple, dict)):
        return False, f"IBKR whatIfOrder returned invalid/empty response: {type(whatif).__name__}", {}

    status = str(getattr(whatif, "status", "") or "").strip()
    init_change = _parse_ibkr_float(getattr(whatif, "initMarginChange", None))
    init_after = _parse_ibkr_float(getattr(whatif, "initMarginAfter", None))
    eq_after = _parse_ibkr_float(getattr(whatif, "equityWithLoanAfter", None))

    meta = {
        "whatif_status": status,
        "whatif_init_margin_change": init_change,
        "whatif_init_margin_after": init_after,
        "whatif_equity_after": eq_after,
    }

    if init_change is None and init_after is None:
        return False, f"IBKR whatIfOrder returned UNSET/missing margin fields (status='{status}').", meta

    if is_risk_reducing:
        # Always allow risk-reducing exits as long as margin doesn't unexpectedly surge
        if init_change is not None and init_change > 250.0:
            return False, (
                f"IBKR whatIfOrder rejected risk-reducing exit: initMarginChange=+${init_change:.2f} > $250."
            ), meta
        return True, "OK (risk-reducing)", meta

    if eq_after is None or eq_after <= 0.0:
        return False, "IBKR whatIfOrder returned missing/non-positive equityWithLoanAfter.", meta
    if init_after is None or init_after < 0.0:
        return False, "IBKR whatIfOrder returned missing/invalid initMarginAfter.", meta
    if init_after > eq_after * 0.95:
        return False, (
            f"IBKR whatIfOrder margin reject: initMarginAfter ${init_after:.2f} > "
            f"95% equityWithLoanAfter ${eq_after:.2f}."
        ), meta
    return True, "OK", meta


def compute_adaptive_step_bound(intent: OrderIntent, config: BotConfig) -> float:
    """
    Critic #2 Fix #12: Computes hard signed limit-price ceiling/floor for adaptive stepping
    so stepping can NEVER breach min_net_credit, 2.5% NAV cap, spread width, or flip sign!
    """
    if intent.asset_class == AssetClass.EQUITY:
        if intent.max_step_limit_price is not None and math.isfinite(intent.max_step_limit_price):
            return round(float(intent.max_step_limit_price), 2)
        # Default: allow at most +0.15% upward on BUY or -0.15% downward on SELL
        base = float(intent.limit_price)
        return round(base * (1.0015 if intent.action == "BUY" else 0.9985), 2)

    if intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD:
        # Signed limit price is NEGATIVE (-credit). Stepping adds +delta (making it LESS negative).
        # Hard ceiling (most aggressive allowed) = -min_credit_allowed (strictly < 0!).
        floor_credit = max(config.xsp.min_net_credit, 0.05)
        if intent.max_step_limit_price is not None and math.isfinite(intent.max_step_limit_price):
            floor_credit = max(floor_credit, float(intent.max_step_limit_price))
        # Ensure floor never exceeds initial limit_price (otherwise no stepping occurs)
        floor_credit = min(floor_credit, float(intent.limit_price))
        return round(-floor_credit, 2)

    if intent.intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD:
        # Signed limit price is POSITIVE (+debit). Hard ceiling = min(width - 0.05, max_step).
        strikes = [l.strike for l in intent.legs]
        width = max(strikes) - min(strikes) if len(strikes) == 2 else 10.0
        cap = max(0.05, width - 0.05)
        if intent.max_step_limit_price is not None and math.isfinite(intent.max_step_limit_price):
            cap = min(cap, float(intent.max_step_limit_price))
        return round(max(float(intent.limit_price), cap), 2)

    return round(float(intent.limit_price), 2)


def build_ibkr_combo_order_spec(intent: OrderIntent) -> Dict[str, Any]:
    """
    Pure, unit-testable builder translating an `OrderIntent` into exact IBKR
    `BAG` leg actions and signed `LimitOrder` price.
    Raises ValueError on any sign or structural inconsistency (fail-closed!).
    """
    if intent.asset_class != AssetClass.OPTION_COMBO:
        raise ValueError("build_ibkr_combo_order_spec requires AssetClass.OPTION_COMBO.")
    if len(intent.legs) != 2:
        raise ValueError(f"Expected exactly 2 legs for spread combo, got {len(intent.legs)}.")
    if not math.isfinite(intent.limit_price) or intent.limit_price <= 0.0:
        raise ValueError(f"Intent limit_price must be positive finite float, got {intent.limit_price}.")

    price_2dp = round(float(intent.limit_price), 2)

    if intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD:
        s_legs = [l for l in intent.legs if l.action == "SELL" and l.right == "P"]
        l_legs = [l for l in intent.legs if l.action == "BUY" and l.right == "P"]
        if len(s_legs) != 1 or len(l_legs) != 1 or s_legs[0].strike <= l_legs[0].strike:
            raise ValueError("Invalid Put Credit Spread legs for OPEN intent.")
        return {
            "symbol": intent.symbol.upper(),
            "trading_class": intent.symbol.upper(),
            "exchange": "SMART",
            "currency": "USD",
            "account": intent.account_id,
            "order_action": "BUY",
            "order_type": "LMT",
            "tif": "DAY",
            "total_quantity": int(intent.quantity),
            "signed_limit_price": -price_2dp,  # NEGATIVE = minimum net CREDIT received!
            "guaranteed_combo": True,  # NonGuaranteed NEVER set!
            "legs": [
                {
                    "symbol": s_legs[0].symbol.upper(),
                    "trading_class": s_legs[0].symbol.upper(),
                    "expiry": s_legs[0].ibkr_expiry,
                    "strike": float(s_legs[0].strike),
                    "right": "P",
                    "action": "SELL",
                    "ratio": 1,
                    "con_id": s_legs[0].con_id,
                },
                {
                    "symbol": l_legs[0].symbol.upper(),
                    "trading_class": l_legs[0].symbol.upper(),
                    "expiry": l_legs[0].ibkr_expiry,
                    "strike": float(l_legs[0].strike),
                    "right": "P",
                    "action": "BUY",
                    "ratio": 1,
                    "con_id": l_legs[0].con_id,
                },
            ],
        }

    if intent.intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD:
        b_legs = [l for l in intent.legs if l.action == "BUY" and l.right == "P"]
        s_legs = [l for l in intent.legs if l.action == "SELL" and l.right == "P"]
        if len(b_legs) != 1 or len(s_legs) != 1 or b_legs[0].strike <= s_legs[0].strike:
            raise ValueError("Invalid Put Credit Spread legs for CLOSE intent.")
        return {
            "symbol": intent.symbol.upper(),
            "trading_class": intent.symbol.upper(),
            "exchange": "SMART",
            "currency": "USD",
            "account": intent.account_id,
            "order_action": "BUY",
            "order_type": "LMT",
            "tif": "DAY",
            "total_quantity": int(intent.quantity),
            "signed_limit_price": +price_2dp,  # POSITIVE = maximum net DEBIT paid to close!
            "guaranteed_combo": True,
            "legs": [
                {
                    "symbol": b_legs[0].symbol.upper(),
                    "trading_class": b_legs[0].symbol.upper(),
                    "expiry": b_legs[0].ibkr_expiry,
                    "strike": float(b_legs[0].strike),
                    "right": "P",
                    "action": "BUY",
                    "ratio": 1,
                    "con_id": b_legs[0].con_id,
                },
                {
                    "symbol": s_legs[0].symbol.upper(),
                    "trading_class": s_legs[0].symbol.upper(),
                    "expiry": s_legs[0].ibkr_expiry,
                    "strike": float(s_legs[0].strike),
                    "right": "P",
                    "action": "SELL",
                    "ratio": 1,
                    "con_id": s_legs[0].con_id,
                },
            ],
        }

    raise ValueError(f"Unsupported combo intent_type: {intent.intent_type}")


class BrokerInterface(ABC):
    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def get_account_snapshot(
        self, tranche: TrancheConfig, regime: MarketRegime, ledger: StateLedger
    ) -> AccountSnapshot:
        ...

    def get_live_spread_marks(
        self, open_spreads: List[OpenSpreadPosition]
    ) -> Optional[Dict[str, Tuple[float, float]]]:
        """Returns {spread_id: (mid_debit, natural_debit)} from live broker quotes, or None."""
        return None

    @abstractmethod
    def submit_order(self, intent: OrderIntent, regime: MarketRegime) -> ExecutionResult:
        ...


class SimulatedBroker(BrokerInterface):
    """
    Local deterministic broker for `dry-run` mode and automated tests.
    Simulates realistic equity/option fills, IBKR tiered commissions, and
    option position inventory without touching any live socket.
    """

    def __init__(
        self,
        config: BotConfig,
        initial_positions: Optional[Dict[str, Dict[str, float]]] = None,
        initial_cash: Optional[Dict[str, float]] = None,
        equity_slippage_bps: float = 3.0,
        option_slippage_per_leg: float = 0.02,
    ) -> None:
        self.config = config
        self.equity_slippage_bps = equity_slippage_bps
        self.option_slippage_per_leg = option_slippage_per_leg
        self._connected = False
        self._positions: Dict[str, Dict[str, float]] = {
            t.account_id: dict((initial_positions or {}).get(t.account_id, {}))
            for t in config.tranches
        }
        self._cash: Dict[str, float] = {
            t.account_id: float((initial_cash or {}).get(t.account_id, t.initial_nav_usd))
            for t in config.tranches
        }

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def get_account_snapshot(
        self, tranche: TrancheConfig, regime: MarketRegime, ledger: StateLedger
    ) -> AccountSnapshot:
        acct = tranche.account_id
        pos = dict(self._positions.get(acct, {}))
        cash = float(self._cash.get(acct, tranche.initial_nav_usd))
        eq_val = sum(qty * regime.prices.get(sym, 0.0) for sym, qty in pos.items())
        open_spreads = ledger.get_open_spreads(acct)
        lots = ledger.get_open_tax_lots(acct)
        avg_cost: Dict[str, float] = {}
        for sym in pos:
            sym_lots = [l for l in lots if l.symbol == sym]
            if sym_lots:
                tot_q = sum(l.quantity for l in sym_lots)
                avg_cost[sym] = sum(l.quantity * l.cost_basis_per_share for l in sym_lots) / max(1e-9, tot_q)
            else:
                avg_cost[sym] = regime.prices.get(sym, 0.0)
        nav = cash + eq_val
        # Synthesize matching broker_option_positions from open_spreads in simulation
        opt_pos: List[Dict[str, Any]] = []
        for sp in open_spreads:
            opt_pos.append({"symbol": sp.symbol, "expiry": sp.expiry.replace("-", ""), "strike": sp.short_strike, "right": sp.right, "position": -sp.contracts})
            opt_pos.append({"symbol": sp.symbol, "expiry": sp.expiry.replace("-", ""), "strike": sp.long_strike, "right": sp.right, "position": sp.contracts})
        return AccountSnapshot(
            account_id=acct,
            account_type=tranche.account_type,
            nav_usd=nav,
            cash_usd=cash,
            settled_cash_usd=cash,
            buying_power_usd=max(0.0, cash + eq_val * 0.90),
            positions=pos,
            avg_cost=avg_cost,
            open_spreads=open_spreads,
            lots=lots,
            broker_option_positions=opt_pos,
        )

    def submit_order(self, intent: OrderIntent, regime: MarketRegime) -> ExecutionResult:
        acct = intent.account_id
        sym = intent.symbol.upper()
        sim_id = f"SIM-{uuid.uuid4().hex[:10]}"

        if intent.asset_class == AssetClass.EQUITY:
            mid = float(intent.estimated_mid_price or intent.limit_price)
            slip_frac = self.equity_slippage_bps / 10_000.0
            fill_px = mid * (1.0 + slip_frac) if intent.action == "BUY" else mid * (1.0 - slip_frac)
            if intent.action == "BUY" and fill_px > intent.limit_price + 1e-6:
                return ExecutionResult(
                    idempotency_key=intent.idempotency_key,
                    status="CANCELLED",
                    broker_order_id=sim_id,
                    message=f"Simulated BUY fill ${fill_px:.2f} exceeded limit ${intent.limit_price:.2f}.",
                )
            if intent.action == "SELL" and fill_px < intent.limit_price - 1e-6:
                return ExecutionResult(
                    idempotency_key=intent.idempotency_key,
                    status="CANCELLED",
                    broker_order_id=sim_id,
                    message=f"Simulated SELL fill ${fill_px:.2f} below limit ${intent.limit_price:.2f}.",
                )
            comm = max(0.35, 0.0035 * intent.quantity)
            notional = fill_px * intent.quantity
            if intent.action == "BUY":
                self._cash[acct] = self._cash.get(acct, 0.0) - notional - comm
                self._positions.setdefault(acct, {})[sym] = self._positions.get(acct, {}).get(sym, 0.0) + intent.quantity
            else:
                self._cash[acct] = self._cash.get(acct, 0.0) + notional - comm
                new_q = max(0.0, self._positions.get(acct, {}).get(sym, 0.0) - intent.quantity)
                if new_q <= 1e-6:
                    self._positions.setdefault(acct, {}).pop(sym, None)
                else:
                    self._positions.setdefault(acct, {})[sym] = new_q

            return ExecutionResult(
                idempotency_key=intent.idempotency_key,
                status="SIMULATED",
                broker_order_id=sim_id,
                filled_qty=intent.quantity,
                avg_fill_price=round(fill_px, 4),
                commission_usd=round(comm, 2),
                slippage_bps=self.equity_slippage_bps,
                message=f"Simulated {intent.action} {intent.quantity} {sym} @ ${fill_px:.2f}",
                raw_order_spec={
                    "order_type": "LMT",
                    "action": intent.action,
                    "symbol": sym,
                    "quantity": intent.quantity,
                    "lmtPrice": round(intent.limit_price, 2),
                    "account": acct,
                },
            )

        if intent.asset_class == AssetClass.OPTION_COMBO:
            spec = build_ibkr_combo_order_spec(intent)
            comm = 0.65 * len(intent.legs) * intent.quantity
            mid = float(intent.estimated_mid_price or intent.limit_price)
            slip = self.option_slippage_per_leg * len(intent.legs)
            if intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD:
                fill_credit = max(0.01, mid - slip)
                if fill_credit < intent.limit_price - 1e-6:
                    fill_credit = intent.limit_price
                self._cash[acct] = self._cash.get(acct, 0.0) + fill_credit * 100.0 * intent.quantity - comm
                slip_bps = ((mid - fill_credit) / max(0.01, mid)) * 10_000.0
                return ExecutionResult(
                    idempotency_key=intent.idempotency_key,
                    status="SIMULATED",
                    broker_order_id=sim_id,
                    filled_qty=intent.quantity,
                    avg_fill_price=round(fill_credit, 2),
                    commission_usd=round(comm, 2),
                    slippage_bps=round(slip_bps, 2),
                    message=(
                        f"Simulated XSP OPEN {intent.quantity}x credit spread @ ${fill_credit:.2f} cr "
                        f"(IBKR BAG lmtPrice={spec['signed_limit_price']:+.2f})"
                    ),
                    raw_order_spec=spec,
                )
            else:
                fill_debit = mid + slip
                if fill_debit > intent.limit_price + 1e-6:
                    fill_debit = intent.limit_price
                self._cash[acct] = self._cash.get(acct, 0.0) - fill_debit * 100.0 * intent.quantity - comm
                slip_bps = ((fill_debit - mid) / max(0.01, mid)) * 10_000.0
                return ExecutionResult(
                    idempotency_key=intent.idempotency_key,
                    status="SIMULATED",
                    broker_order_id=sim_id,
                    filled_qty=intent.quantity,
                    avg_fill_price=round(fill_debit, 2),
                    commission_usd=round(comm, 2),
                    slippage_bps=round(slip_bps, 2),
                    message=(
                        f"Simulated XSP CLOSE {intent.quantity}x credit spread @ ${fill_debit:.2f} dr "
                        f"(IBKR BAG lmtPrice={spec['signed_limit_price']:+.2f})"
                    ),
                    raw_order_spec=spec,
                )

        return ExecutionResult(
            idempotency_key=intent.idempotency_key,
            status="REJECTED",
            message=f"Unsupported asset_class: {intent.asset_class}",
        )


class IBKRBroker(BrokerInterface):
    """
    Hardened production IBKR TWS / IB Gateway adapter powered by `ib_async`.
    Implements ALL Critic #2 execution fixes (#1–#6, #11, #12, #14, #26, #27).
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._ib: Any = None
        # Critic #2 Fix #14: Track in-cycle cash & share deltas because IBKR accountSummary
        # throttles pushes by up to 3 minutes!
        self._cycle_cash_delta: Dict[str, float] = {}
        self._cycle_pos_delta: Dict[str, Dict[str, float]] = {}

    def connect(self) -> None:
        self.config.ibkr.validate_for_mode(self.config.mode)
        acct_errs = self.config.validate_mode_account_safety()
        if acct_errs:
            raise ValueError("; ".join(acct_errs))
        try:
            from ib_async import IB
        except ImportError as exc:
            raise RuntimeError("ib_async is required for IBKRBroker. Install via `uv pip install ib_async`.") from exc

        self._ib = IB()
        # Critic #2 Fix #1: Raise errors on failed requests instead of silent empty lists!
        self._ib.RaiseRequestErrors = True
        self._ib.connect(
            host=self.config.ibkr.host,
            port=self.config.ibkr.port,
            clientId=self.config.ibkr.client_id,
            timeout=self.config.ibkr.timeout_seconds,
            readonly=(self.config.mode == ExecutionMode.DRY_RUN),
        )
        if not self._ib.isConnected():
            raise ConnectionError(
                f"Failed to connect to IB Gateway at {self.config.ibkr.host}:{self.config.ibkr.port}."
            )
        self._cycle_cash_delta.clear()
        self._cycle_pos_delta.clear()

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    def get_account_snapshot(
        self, tranche: TrancheConfig, regime: MarketRegime, ledger: StateLedger
    ) -> AccountSnapshot:
        if self._ib is None or not self._ib.isConnected():
            raise RuntimeError("IBKRBroker is not connected.")
        acct = tranche.account_id
        summary = self._ib.accountSummary(acct)
        vals: Dict[str, float] = {}
        for item in summary:
            if item.account == acct and item.currency in {"USD", ""}:
                v = _parse_ibkr_float(item.value)
                if v is not None:
                    vals[item.tag] = v

        # Critic #2 Fix #14: Fail-CLOSED if NetLiquidation is missing from IBKR accountSummary!
        if "NetLiquidation" not in vals or vals["NetLiquidation"] <= 0.0:
            raise RuntimeError(
                f"IBKR FAIL-CLOSED: Missing valid NetLiquidation for account '{acct}'. "
                f"Verify account ID matches connected IB Gateway session."
            )

        c_delta = self._cycle_cash_delta.get(acct, 0.0)
        nav = vals["NetLiquidation"]
        cash = vals.get("TotalCashValue", 0.0) + c_delta
        settled = vals.get("SettledCash")
        settled_adj = (settled + c_delta) if settled is not None else None
        bp = max(0.0, vals.get("AvailableFunds", vals.get("BuyingPower", cash)) + c_delta)

        positions: Dict[str, float] = {}
        avg_cost: Dict[str, float] = {}
        broker_opt_pos: List[Dict[str, Any]] = []
        for p in self._ib.positions(acct):
            if p.account != acct:
                continue
            sec_type = str(getattr(p.contract, "secType", "")).upper()
            pos_qty = float(p.position or 0.0)
            if sec_type == "STK" and pos_qty != 0.0:
                sym = str(p.contract.symbol).upper()
                positions[sym] = pos_qty
                avg_cost[sym] = float(p.avgCost or 0.0)
            elif sec_type == "OPT" and pos_qty != 0.0:
                broker_opt_pos.append({
                    "symbol": str(p.contract.symbol).upper(),
                    "expiry": str(getattr(p.contract, "lastTradeDateOrContractMonth", "")),
                    "strike": float(getattr(p.contract, "strike", 0.0)),
                    "right": str(getattr(p.contract, "right", "")).upper(),
                    "position": pos_qty,
                    "con_id": getattr(p.contract, "conId", None),
                })

        # Apply in-cycle share deltas so sequential orders never misread cached positions
        for sym, dq in self._cycle_pos_delta.get(acct, {}).items():
            new_q = positions.get(sym, 0.0) + dq
            if new_q <= 1e-6:
                positions.pop(sym, None)
            else:
                positions[sym] = new_q

        return AccountSnapshot(
            account_id=acct,
            account_type=tranche.account_type,
            nav_usd=nav,
            cash_usd=cash,
            settled_cash_usd=settled_adj,
            buying_power_usd=bp,
            positions=positions,
            avg_cost=avg_cost,
            open_spreads=ledger.get_open_spreads(acct),
            lots=ledger.get_open_tax_lots(acct),
            broker_option_positions=broker_opt_pos,
        )

    def get_live_spread_marks(
        self, open_spreads: List[OpenSpreadPosition]
    ) -> Optional[Dict[str, Tuple[float, float]]]:
        """Critic #2 Fix #3: Fetches real live IBKR bid/ask quotes for every open XSP spread."""
        if not open_spreads or self._ib is None or not self._ib.isConnected():
            return {}
        from ib_async import Option

        marks: Dict[str, Tuple[float, float]] = {}
        for sp in open_spreads:
            exp_ib = sp.expiry.replace("-", "")
            s_opt = Option(
                symbol=sp.symbol,
                lastTradeDateOrContractMonth=exp_ib,
                strike=sp.short_strike,
                right=sp.right,
                exchange="SMART",
                currency="USD",
                multiplier="100",
                tradingClass=sp.symbol,
            )
            l_opt = Option(
                symbol=sp.symbol,
                lastTradeDateOrContractMonth=exp_ib,
                strike=sp.long_strike,
                right=sp.right,
                exchange="SMART",
                currency="USD",
                multiplier="100",
                tradingClass=sp.symbol,
            )
            if sp.short_con_id:
                s_opt.conId = sp.short_con_id
            if sp.long_con_id:
                l_opt.conId = sp.long_con_id
            q = self._ib.qualifyContracts(s_opt, l_opt)
            if not q or len(q) < 2 or q[0] is None or q[1] is None:
                continue
            tickers = self._ib.reqTickers(q[0], q[1])
            if not tickers or len(tickers) < 2:
                continue
            st, lt = tickers[0], tickers[1]
            s_bid, s_ask = _parse_ibkr_float(st.bid), _parse_ibkr_float(st.ask)
            l_bid, l_ask = _parse_ibkr_float(lt.bid), _parse_ibkr_float(lt.ask)
            if None in (s_bid, s_ask, l_bid, l_ask) or s_ask <= 0 or l_ask <= 0:
                continue
            s_mid = 0.5 * (s_bid + s_ask)
            l_mid = 0.5 * (l_bid + l_ask)
            mid_debit = max(0.01, s_mid - l_mid)
            nat_debit = max(0.01, s_ask - l_bid)
            marks[sp.spread_id] = (round(mid_debit, 2), round(nat_debit, 2))
        return marks

    def submit_order(self, intent: OrderIntent, regime: MarketRegime) -> ExecutionResult:
        if self._ib is None or not self._ib.isConnected():
            raise RuntimeError("IBKRBroker is not connected.")
        from ib_async import Bag, ComboLeg, LimitOrder, Option, Stock

        if intent.asset_class == AssetClass.EQUITY:
            contract = Stock(intent.symbol.upper(), "SMART", "USD")
            qualified = self._ib.qualifyContracts(contract)
            # Critic #2 Fix #11: Guard against [None] returned by ib_async on failure!
            if not qualified or qualified[0] is None or not getattr(qualified[0], "conId", None):
                return ExecutionResult(
                    idempotency_key=intent.idempotency_key,
                    status="REJECTED",
                    message=f"Failed to qualify IBKR Stock contract for {intent.symbol}.",
                )
            contract = qualified[0]
            base_lmt = round(float(intent.limit_price), 2)
            order = LimitOrder(
                action=intent.action,
                totalQuantity=int(intent.quantity),
                lmtPrice=base_lmt,
                tif="DAY",
                account=intent.account_id,
                orderRef=intent.idempotency_key,
            )
            raw_spec = {
                "symbol": contract.symbol,
                "conId": int(contract.conId),
                "action": intent.action,
                "quantity": int(intent.quantity),
                "lmtPrice": base_lmt,
                "account": intent.account_id,
            }
            return self._whatif_and_transmit(intent, contract, order, raw_spec)

        if intent.asset_class == AssetClass.OPTION_COMBO:
            spec = build_ibkr_combo_order_spec(intent)
            combo_legs: List[Any] = []
            qualified_con_ids: List[int] = []
            for leg in spec["legs"]:
                opt = Option(
                    symbol=leg["symbol"],
                    lastTradeDateOrContractMonth=leg["expiry"],
                    strike=leg["strike"],
                    right=leg["right"],
                    exchange="SMART",
                    currency="USD",
                    multiplier="100",
                    tradingClass=leg["trading_class"],
                )
                if leg.get("con_id"):
                    opt.conId = int(leg["con_id"])
                q_opts = self._ib.qualifyContracts(opt)
                if not q_opts or q_opts[0] is None or not getattr(q_opts[0], "conId", None):
                    return ExecutionResult(
                        idempotency_key=intent.idempotency_key,
                        status="REJECTED",
                        message=f"Failed to qualify IBKR option leg {leg}.",
                        raw_order_spec=spec,
                    )
                cid = int(q_opts[0].conId)
                qualified_con_ids.append(cid)
                combo_legs.append(
                    ComboLeg(
                        conId=cid,
                        ratio=int(leg["ratio"]),
                        action=str(leg["action"]),
                        exchange="SMART",
                    )
                )

            spec["short_con_id"] = qualified_con_ids[0]
            spec["long_con_id"] = qualified_con_ids[1]

            bag = Bag(
                symbol=spec["symbol"],
                exchange="SMART",
                currency="USD",
                comboLegs=combo_legs,
            )
            # smartComboRoutingParams intentionally omits NonGuaranteed="1" -> guaranteed book!
            order = LimitOrder(
                action=spec["order_action"],
                totalQuantity=spec["total_quantity"],
                lmtPrice=spec["signed_limit_price"],
                tif="DAY",
                account=spec["account"],
                orderRef=intent.idempotency_key,
            )
            return self._whatif_and_transmit(intent, bag, order, spec)

        return ExecutionResult(
            idempotency_key=intent.idempotency_key,
            status="REJECTED",
            message=f"Unsupported asset_class for IBKRBroker: {intent.asset_class}",
        )

    def _whatif_and_transmit(
        self, intent: OrderIntent, contract: Any, order: Any, raw_spec: Dict[str, Any]
    ) -> ExecutionResult:
        # 1. Critic #2 Fix #1: Strict fail-closed whatIfOrder validation
        try:
            whatif = self._ib.whatIfOrder(contract, order)
        except Exception as exc:
            return ExecutionResult(
                idempotency_key=intent.idempotency_key,
                status="REJECTED",
                message=f"IBKR whatIfOrder pre-check threw exception (fail-closed): {exc}",
                raw_order_spec=raw_spec,
            )

        ok, why, wf_meta = validate_whatif_order_state(whatif, is_risk_reducing=intent.is_risk_reducing)
        raw_spec.update(wf_meta)
        if not ok:
            return ExecutionResult(
                idempotency_key=intent.idempotency_key,
                status="REJECTED",
                message=why,
                raw_order_spec=raw_spec,
            )

        # 2. Critic #2 Fix #12: Transmit LimitOrder with HARD-CAPPED adaptive stepping + AssertionError safety
        step_bound = compute_adaptive_step_bound(intent, self.config)
        raw_spec["adaptive_step_bound"] = step_bound
        trade = self._ib.placeOrder(contract, order)
        step_wait = self.config.ibkr.max_order_wait_seconds / max(1, self.config.ibkr.adaptive_price_steps + 1)

        for step_idx in range(self.config.ibkr.adaptive_price_steps + 1):
            deadline = time.time() + step_wait
            while time.time() < deadline:
                self._ib.sleep(0.5)
                if trade.isDone():
                    break
            if trade.isDone():
                break
            if step_idx < self.config.ibkr.adaptive_price_steps:
                delta = self.config.ibkr.adaptive_step_dollars
                if intent.asset_class == AssetClass.EQUITY:
                    proposed = round(order.lmtPrice + (delta if intent.action == "BUY" else -delta), 2)
                    next_px = min(proposed, step_bound) if intent.action == "BUY" else max(proposed, step_bound)
                else:
                    # For BUY BAG: more aggressive = HIGHER (less negative for credit, more positive for debit)
                    proposed = round(order.lmtPrice + delta, 2)
                    next_px = min(proposed, step_bound)
                    if intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD and next_px >= 0.0:
                        next_px = order.lmtPrice  # NEVER cross 0.00 into a debit!

                if next_px != order.lmtPrice and not trade.isDone():
                    order.lmtPrice = next_px
                    try:
                        self._ib.placeOrder(contract, order)
                    except AssertionError:
                        # Async fill arrived right before modify -> exit loop cleanly and read final fill!
                        break

        # 3. Critic #2 Fix #2: Confirmed cancel polling — NEVER drop partial or racing fills!
        if not trade.isDone():
            self._ib.cancelOrder(order)
            cancel_deadline = time.time() + self.config.ibkr.cancel_confirm_timeout_seconds
            while time.time() < cancel_deadline and not trade.isDone():
                self._ib.sleep(0.25)

        filled = int(trade.orderStatus.filled or 0)
        raw_status = str(trade.orderStatus.status or "UNKNOWN").upper()

        # Critic #2 Fix #26: Wait briefly for async commissionReport callbacks when filled > 0
        if filled > 0:
            comm_deadline = time.time() + self.config.ibkr.commission_wait_seconds
            while time.time() < comm_deadline:
                if trade.fills and all(getattr(f.commissionReport, "execId", "") for f in trade.fills):
                    break
                self._ib.sleep(0.2)

        raw_avg_px = _parse_ibkr_float(trade.orderStatus.avgFillPrice) or 0.0
        if intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD and filled > 0:
            # Credit fill on BUY BAG MUST be negative! Never silently mask a debit with abs()!
            if raw_avg_px >= 0.0:
                return ExecutionResult(
                    idempotency_key=intent.idempotency_key,
                    status="ERROR",
                    broker_order_id=str(trade.order.orderId),
                    filled_qty=filled,
                    avg_fill_price=raw_avg_px,
                    message=f"CRITICAL SIGN ANOMALY: XSP OPEN filled with non-negative avgFillPrice={raw_avg_px}!",
                    raw_order_spec=raw_spec,
                )
            avg_px = round(-raw_avg_px, 4)
        else:
            avg_px = round(abs(raw_avg_px), 4)

        comm = 0.0
        for f in trade.fills:
            c = _parse_ibkr_float(getattr(getattr(f, "commissionReport", None), "commission", None))
            if c is not None and 0.0 <= c < 1e6:
                comm += c

        if filled >= intent.quantity and filled > 0:
            final_status = "FILLED"
        elif filled > 0:
            final_status = "PARTIALLY_FILLED"
        else:
            final_status = raw_status if raw_status in {"CANCELLED", "INACTIVE", "REJECTED"} else "CANCELLED"

        # Update in-cycle cash & position deltas so subsequent orders see real post-fill liquidity!
        if filled > 0:
            self._apply_cycle_fill_delta(intent, filled, avg_px, comm)

        mid = float(intent.estimated_mid_price or intent.limit_price)
        slip_bps = (abs(avg_px - mid) / max(0.01, mid)) * 10_000.0 if filled > 0 else 0.0
        return ExecutionResult(
            idempotency_key=intent.idempotency_key,
            status=final_status,
            broker_order_id=str(trade.order.orderId),
            filled_qty=filled,
            avg_fill_price=avg_px,
            commission_usd=round(comm, 2),
            slippage_bps=round(slip_bps, 2),
            message=f"IBKR order {trade.order.orderId} terminal={final_status} (raw={raw_status}), filled={filled}/{intent.quantity} @ {avg_px:.4f}",
            raw_order_spec=raw_spec,
        )

    def _apply_cycle_fill_delta(self, intent: OrderIntent, filled: int, avg_px: float, comm: float) -> None:
        acct = intent.account_id
        sym = intent.symbol.upper()
        if intent.asset_class == AssetClass.EQUITY:
            if intent.action == "BUY":
                self._cycle_cash_delta[acct] = self._cycle_cash_delta.get(acct, 0.0) - (filled * avg_px + comm)
                self._cycle_pos_delta.setdefault(acct, {})[sym] = self._cycle_pos_delta.get(acct, {}).get(sym, 0.0) + filled
            else:
                self._cycle_cash_delta[acct] = self._cycle_cash_delta.get(acct, 0.0) + (filled * avg_px - comm)
                self._cycle_pos_delta.setdefault(acct, {})[sym] = self._cycle_pos_delta.get(acct, {}).get(sym, 0.0) - filled
        elif intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD:
            self._cycle_cash_delta[acct] = self._cycle_cash_delta.get(acct, 0.0) + (filled * avg_px * 100.0 - comm)
        elif intent.intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD:
            self._cycle_cash_delta[acct] = self._cycle_cash_delta.get(acct, 0.0) - (filled * avg_px * 100.0 + comm)
