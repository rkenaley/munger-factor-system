"""Live market regime ingestion, dual-tranche signal generation, and advisory Munger screener."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from .config import AccountType, BotConfig, ExecutionMode, TrancheConfig
from .ledger import StateLedger
from .models import (
    AccountSnapshot,
    AssetClass,
    IntentType,
    MarketRegime,
    OptionLegSpec,
    OrderIntent,
    is_irs_long_term,
)
from .risk import is_clean_option_strike

NY_TZ = ZoneInfo("America/New_York")


def current_ny_date() -> date:
    """Critic #2 Fix #21: Always derive trading date in US/Eastern (America/New_York)."""
    return datetime.now(NY_TZ).date()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _extract_price_frame(raw: pd.DataFrame, field: str, symbols: List[str]) -> pd.DataFrame:
    """Critic #2 Fix #18: Safely handles empty, single-ticker, and MultiIndex yf.download frames."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if field in raw.columns.get_level_values(0):
            df = raw[field]
        elif "Close" in raw.columns.get_level_values(0):
            df = raw["Close"]
        else:
            return pd.DataFrame()
        return df.to_frame() if isinstance(df, pd.Series) else df
    # Single-level DataFrame (1 ticker downloaded)
    col = field if field in raw.columns else ("Close" if "Close" in raw.columns else None)
    if col is None:
        return pd.DataFrame()
    name = symbols[0] if len(symbols) == 1 else col
    return raw[[col]].rename(columns={col: name})


def fetch_live_market_regime(config: BotConfig) -> MarketRegime:
    """
    Critic #2 Fixes #15, #18, #22:
      - Computes SPY RV21 strictly on dividend/split-adjusted `Adj Close` (never distorted by SPY ex-divs!).
      - Computes nominal spot prices on unadjusted `Close`.
      - Uses US/Eastern timezone (`America/New_York`) and intraday 5m SPY bar timestamps for true quote age.
      - Strictly FAIL-CLOSED when `^VIX`, `^VIX3M`, `^XSP`, or `SPY` history is missing!
    """
    etfs: List[str] = ["SPY", "^VIX", "^VIX3M", "^XSP"]
    for tr in config.tranches:
        etfs.extend(tr.core_equity_targets.keys())
        etfs.append(tr.cash_sweep_etf)
        for ring in tr.tlh_rings:
            etfs.extend(ring)
    uniq = sorted(set(etfs))

    raw = yf.download(uniq, period="3mo", auto_adjust=False, progress=False)
    close_df = _extract_price_frame(raw, "Close", uniq)
    adj_df = _extract_price_frame(raw, "Adj Close", uniq)
    if not close_df.empty:
        close_df.index = pd.to_datetime(close_df.index).tz_localize(None)
    if not adj_df.empty:
        adj_df.index = pd.to_datetime(adj_df.index).tz_localize(None)

    prices: Dict[str, float] = {}
    for sym in uniq:
        if sym in close_df.columns:
            s = close_df[sym].dropna()
            if len(s) > 0:
                px = _safe_float(s.iloc[-1], 0.0)
                if px > 0.0:
                    prices[sym] = px

    spy_nom = close_df["SPY"].dropna() if "SPY" in close_df.columns else pd.Series(dtype=float)
    spy_adj = adj_df["SPY"].dropna() if "SPY" in adj_df.columns else pd.Series(dtype=float)

    spy_spot = _safe_float(spy_nom.iloc[-1], 0.0) if len(spy_nom) > 0 else 0.0
    # Critic #2 Fix #22: NEVER silently substitute SPY for missing ^XSP (dividend basis mismatch!)
    xsp_spot = prices.get("^XSP", 0.0)
    vix = prices.get("^VIX", 0.0)
    # Fail-CLOSED if ^VIX3M missing (0.0 -> vix_term_ratio = inf -> blocks XSP opens!)
    vix3m = prices.get("^VIX3M", 0.0)

    # Fail-CLOSED if SPY adjusted history insufficient (inf -> vrp = -inf -> blocks XSP opens!)
    rv21_pct = float("inf")
    if len(spy_adj) >= 22:
        log_r = np.log(spy_adj / spy_adj.shift(1)).dropna()
        if len(log_r) >= 21:
            rv = _safe_float(log_r.tail(21).std() * math.sqrt(252.0) * 100.0, float("inf"))
            if rv > 0.0:
                rv21_pct = round(rv, 2)

    # Critic #2 Fix #15: True intraday quote age using US/Eastern clock (fail-CLOSED on empty!)
    age_s = float("inf")
    if len(spy_nom) > 0:
        try:
            intra = yf.download("SPY", period="5d", interval="5m", auto_adjust=False, progress=False)
            if intra is not None and not intra.empty:
                last_ts = pd.Timestamp(intra.index[-1])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.tz_localize(timezone.utc)
                now_utc = datetime.now(timezone.utc)
                age_s = max(0.0, (now_utc - last_ts.to_pydatetime().astimezone(timezone.utc)).total_seconds())
            else:
                last_bar_d = spy_nom.index[-1].date()
                ny_now = datetime.now(NY_TZ)
                if last_bar_d >= ny_now.date():
                    age_s = 0.0
                else:
                    age_s = float((ny_now.date() - last_bar_d).days * 86400)
        except Exception:
            age_s = float("inf")

    clean_prices = {k.lstrip("^"): v for k, v in prices.items()}
    if xsp_spot > 0.0:
        clean_prices["XSP"] = xsp_spot

    return MarketRegime(
        as_of=datetime.now(timezone.utc),
        spy_spot=spy_spot,
        xsp_spot=xsp_spot,
        vix=vix,
        vix3m=vix3m,
        rv21_spy_pct=rv21_pct,
        xsp_atm_iv30_pct=None,
        tbill_yield_pct=4.20,
        quote_age_seconds=age_s,
        prices=clean_prices,
    )


def fetch_xsp_put_chain(
    config: BotConfig, regime: MarketRegime, trade_date: date
) -> Tuple[Optional[str], Optional[int], Optional[pd.DataFrame], bool]:
    """
    Critic #2 Fix #19:
      - Fetches live European `^XSP` put chain inside [min_dte, max_dte].
      - Rejects crossed quotes (`ask < bid`), non-standard strikes, non-100 multipliers.
      - Pre-filters liquid strikes (`openInterest >= min_open_interest` & `ask - bid <= max_leg_bid_ask_usd`)
        BEFORE strike selection so illiquid intermediate strikes don't block liquid $5/$10 strikes.
      - Sanity-bounds ATM IV against ^VIX before updating `xsp_atm_iv30_pct`.
      - Proxy `SPY` fallback permitted STRICTLY in DRY_RUN mode (blocked in PAPER/LIVE by RiskFirewall).
    """
    candidates = ["^XSP"]
    if config.mode == ExecutionMode.DRY_RUN and config.allow_proxy_chain_in_dry_run:
        candidates.append("SPY")

    for idx, chain_sym in enumerate(candidates):
        is_proxy = idx > 0
        try:
            tk = yf.Ticker(chain_sym)
            exps = list(tk.options or [])
        except Exception:
            continue
        valid_exps: List[Tuple[int, str, int]] = []
        for e in exps:
            try:
                ed = datetime.strptime(e, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (ed - trade_date).days
            if config.xsp.min_dte <= dte <= config.xsp.max_dte:
                valid_exps.append((abs(dte - config.xsp.ideal_dte), e, dte))
        if not valid_exps:
            continue
        valid_exps.sort(key=lambda x: x[0])
        _, chosen_exp, chosen_dte = valid_exps[0]
        try:
            chain = tk.option_chain(chosen_exp)
            puts = chain.puts.copy()
        except Exception:
            continue
        if puts is None or puts.empty:
            continue

        puts = puts[
            puts["strike"].apply(lambda k: is_clean_option_strike(_safe_float(k, -1.0)))
            & (puts["bid"].fillna(0.0) > 0.0)
            & (puts["ask"].fillna(0.0) > 0.0)
            & (puts["ask"].fillna(0.0) >= puts["bid"].fillna(0.0))
        ].copy()
        if "contractSize" in puts.columns:
            puts = puts[puts["contractSize"].astype(str).str.upper().isin({"REGULAR", "100", ""})]
        if puts.empty:
            continue

        # Sanity-bounded ATM IV extraction (prevents single corrupted Yahoo IV from spoofing VRP!)
        spot = regime.xsp_spot if (not is_proxy and regime.xsp_spot > 0.0) else regime.spy_spot
        if spot > 0.0 and "impliedVolatility" in puts.columns:
            puts["_dist"] = (puts["strike"] - spot).abs()
            atm_rows = puts.sort_values("_dist").head(2)
            atm_iv = _safe_float(atm_rows["impliedVolatility"].mean(), 0.0) * 100.0
            if regime.vix > 0.0 and (0.60 * regime.vix <= atm_iv <= 1.50 * regime.vix):
                regime.xsp_atm_iv30_pct = round(atm_iv, 2)

        # Pre-filter liquid strikes before returning for strike selection
        oi_col = puts["openInterest"].fillna(0.0) if "openInterest" in puts.columns else pd.Series(0.0, index=puts.index)
        ba_col = puts["ask"] - puts["bid"]
        liquid = puts[(oi_col >= config.xsp.min_open_interest) & (ba_col <= config.xsp.max_leg_bid_ask_usd)].copy()
        return chosen_exp, chosen_dte, (liquid if not liquid.empty else puts), is_proxy

    return None, None, None, False


def _estimate_put_delta(spot: float, strike: float, dte_days: int, iv: float, r: float = 0.042, q: float = 0.015) -> float:
    T = max(1.0 / 365.0, dte_days / 365.0)
    sigma = max(0.08, iv)
    d1 = (math.log(max(spot, 0.01) / max(strike, 0.01)) + (r - q + 0.5 * sigma * sigma) * T) / (
        sigma * math.sqrt(T)
    )
    cdf_d1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return abs(math.exp(-q * T) * (cdf_d1 - 1.0))


def _bsm_put_px(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.015) -> float:
    if T <= 1e-6 or sigma <= 1e-6:
        return max(0.0, K - S)
    st = math.sqrt(T)
    d1 = (math.log(max(S, 0.01) / max(K, 0.01)) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * st)
    d2 = d1 - sigma * st
    n_md1 = 0.5 * (1.0 + math.erf(-d1 / math.sqrt(2.0)))
    n_md2 = 0.5 * (1.0 + math.erf(-d2 / math.sqrt(2.0)))
    return max(0.0, K * math.exp(-r * T) * n_md2 - S * math.exp(-q * T) * n_md1)


def generate_spread_exit_intents(
    config: BotConfig,
    tranche: TrancheConfig,
    snapshot: AccountSnapshot,
    regime: MarketRegime,
    trade_date: date,
    live_spread_marks: Optional[Dict[str, Tuple[float, float]]] = None,
) -> List[OrderIntent]:
    """
    Critic #2 Fix #3: Generates risk-reducing CLOSE intents for open XSP credit spreads hitting:
      1. >= 50% Take Profit,
      2. <= 21 DTE Time Exit,
      3. <= -2.0x Credit Stop Loss.
    HARD-BLOCKS synthetic BSM fallback marks in PAPER and LIVE modes!
    """
    intents: List[OrderIntent] = []
    for sp in snapshot.open_spreads:
        try:
            exp_dt = date.fromisoformat(sp.expiry)
        except ValueError:
            continue
        dte = (exp_dt - trade_date).days
        if dte <= 0:
            # Expired spreads are reconciled out of SQLite by engine; never submit close order on expired option!
            continue

        if live_spread_marks and sp.spread_id in live_spread_marks:
            mid_debit, nat_debit = live_spread_marks[sp.spread_id]
        elif config.mode == ExecutionMode.DRY_RUN:
            # Analytical BSM fallback ONLY in offline dry-run
            spot = regime.xsp_spot if regime.xsp_spot > 0.0 else regime.spy_spot
            if spot <= 0.0:
                continue
            T = max(1.0 / 365.0, dte / 365.0)
            iv = max(0.08, regime.effective_iv30_pct / 100.0)
            r = regime.tbill_yield_pct / 100.0
            s_px = _bsm_put_px(spot, sp.short_strike, T, r, iv * 1.12)
            l_px = _bsm_put_px(spot, sp.long_strike, T, r, iv * 1.22)
            mid_debit = max(0.05, s_px - l_px)
            nat_debit = mid_debit + 0.04
        else:
            # PAPER / LIVE FAIL-CLOSED: Never fabricate BSM exit limits without live IBKR marks!
            continue

        pnl_ps = sp.entry_credit - mid_debit
        hit_tp = pnl_ps >= config.xsp.take_profit_pct * sp.entry_credit
        hit_dte = dte <= config.xsp.time_exit_dte
        hit_sl = pnl_ps <= -config.xsp.stop_loss_credit_mult * sp.entry_credit

        if not (hit_tp or hit_dte or hit_sl):
            continue

        trigger = "50%_TP" if hit_tp else ("21_DTE_EXIT" if hit_dte else "2X_STOP_LOSS")
        width = max(0.50, sp.short_strike - sp.long_strike)
        max_debit = round(max(0.05, min(width - 0.05, nat_debit)), 2)
        legs = [
            OptionLegSpec(
                symbol=sp.symbol,
                expiry=sp.expiry,
                strike=sp.short_strike,
                right="P",
                action="BUY",
                con_id=sp.short_con_id,
                is_european=True,
            ),
            OptionLegSpec(
                symbol=sp.symbol,
                expiry=sp.expiry,
                strike=sp.long_strike,
                right="P",
                action="SELL",
                con_id=sp.long_con_id,
                is_european=True,
            ),
        ]
        intents.append(
            OrderIntent(
                account_id=tranche.account_id,
                account_type=tranche.account_type,
                intent_type=IntentType.XSP_CLOSE_CREDIT_SPREAD,
                asset_class=AssetClass.OPTION_COMBO,
                symbol=sp.symbol,
                action="BUY",
                quantity=sp.contracts,
                limit_price=max_debit,
                estimated_mid_price=round(mid_debit, 2),
                max_step_limit_price=round(width - 0.05, 2),
                rationale=f"Risk-reducing XSP exit ({trigger}): DTE={dte}, est PnL=${pnl_ps:+.2f}/sh.",
                trade_date=trade_date,
                is_risk_reducing=True,
                legs=legs,
                spread_id=sp.spread_id,
                dte_days=dte,
                metadata={"trigger": trigger, "entry_credit": sp.entry_credit},
            )
        )
    return intents


def generate_taxable_tlh_sell_intents(
    config: BotConfig,
    tranche: TrancheConfig,
    snapshot: AccountSnapshot,
    regime: MarketRegime,
    ledger: StateLedger,
    trade_date: date,
) -> List[OrderIntent]:
    """
    Critic #2 Fixes #8, #9, #10, #17:
      - Emits ONLY the `TLH_SELL` leg first (each keyed by exact `lot_id`).
      - Engine dynamically constructs the paired `TLH_BUY` immediately AFTER `TLH_SELL`
        fills, sized strictly from *actual* filled sell proceeds (eliminating margin overdraft,
        stale sizing, and multi-lot key collisions!).
      - Enforces BOTH backward 30-day (`get_recent_acquisition`) AND forward 30-day wash-sale checks.
      - Uses exact IRS calendar-year LTCG rule (`is_irs_long_term`).
    """
    if tranche.account_type != AccountType.TAXABLE or not tranche.tlh_rings:
        return []

    intents: List[OrderIntent] = []
    ring_lookup: Dict[str, List[str]] = {}
    for ring in tranche.tlh_rings:
        for sym in ring:
            ring_lookup[sym.upper()] = [r.upper() for r in ring if r.upper() != sym.upper()]

    for lot in snapshot.lots:
        sym = lot.symbol.upper()
        if sym not in ring_lookup:
            continue
        px = regime.prices.get(sym, 0.0)
        if px <= 0.0 or lot.quantity < 1.0:
            continue
        days_held = lot.days_held(trade_date)
        if days_held < config.risk.tlh_min_hold_days:
            continue
        unreal_ret = lot.unrealized_return(px)
        unreal_pnl = lot.unrealized_pnl(px)
        if unreal_ret > config.risk.tlh_loss_trigger_pct or abs(unreal_pnl) < config.risk.tlh_min_loss_usd:
            continue

        # Backward 30-day cross-account wash-sale check BEFORE harvesting!
        if ledger.get_recent_acquisition(sym, trade_date, window_days=30, exclude_lot_id=lot.lot_id) is not None:
            continue

        # Ensure clean non-identical replacement ETF exists with zero forward wash-sale block
        replacement: Optional[str] = None
        for cand in ring_lookup[sym]:
            if ledger.get_active_wash_sale_block(cand, trade_date) is None and regime.prices.get(cand, 0.0) > 0.0:
                replacement = cand
                break
        if replacement is None:
            continue

        sell_qty = int(math.floor(lot.quantity))
        if sell_qty < 1:
            continue

        tax_rate = config.tax.combined_ltcg_rate if is_irs_long_term(lot.acquired_at, trade_date) else config.tax.combined_stcg_rate
        est_tax_saved = abs(unreal_pnl) * tax_rate

        intents.append(
            OrderIntent(
                account_id=tranche.account_id,
                account_type=tranche.account_type,
                intent_type=IntentType.TLH_SELL,
                asset_class=AssetClass.EQUITY,
                symbol=sym,
                action="SELL",
                quantity=sell_qty,
                limit_price=round(px * 0.997, 2),
                estimated_mid_price=round(px, 2),
                rationale=(
                    f"TLH harvest {sym} lot {lot.lot_id} ({unreal_ret*100:.1f}%, "
                    f"${unreal_pnl:.0f} loss -> ~${est_tax_saved:.0f} tax credit); swap -> {replacement}."
                ),
                trade_date=trade_date,
                is_risk_reducing=True,
                lot_id=lot.lot_id,
                lot_acquired_date=lot.acquired_at,
                lot_unrealized_pnl_usd=unreal_pnl,
                linked_tlh_symbol=replacement,
            )
        )
    return intents


def build_paired_tlh_buy_intent(
    sell_intent: OrderIntent,
    filled_proceeds_usd: float,
    regime: MarketRegime,
    trade_date: date,
) -> Optional[OrderIntent]:
    """Builds the exact replacement `TLH_BUY` keyed by source `lot_id` from actual filled sell proceeds."""
    replacement = sell_intent.linked_tlh_symbol
    if not replacement:
        return None
    rep_px = regime.prices.get(replacement.upper(), 0.0)
    if rep_px <= 0.0 or filled_proceeds_usd <= 0.0:
        return None
    lmt_px = round(rep_px * 1.003, 2)
    buy_qty = int(math.floor((filled_proceeds_usd * 0.995) / lmt_px))
    if buy_qty < 1:
        return None
    return OrderIntent(
        account_id=sell_intent.account_id,
        account_type=sell_intent.account_type,
        intent_type=IntentType.TLH_BUY,
        asset_class=AssetClass.EQUITY,
        symbol=replacement.upper(),
        action="BUY",
        quantity=buy_qty,
        limit_price=lmt_px,
        estimated_mid_price=round(rep_px, 2),
        max_step_limit_price=round(rep_px * 1.005, 2),
        rationale=(
            f"Paired TLH replacement buy into non-identical index ETF {replacement.upper()} "
            f"(from filled {sell_intent.symbol} lot {sell_intent.lot_id}, proceeds=${filled_proceeds_usd:,.2f})."
        ),
        trade_date=trade_date,
        is_risk_reducing=False,
        linked_tlh_symbol=sell_intent.symbol.upper(),
        linked_tlh_lot_id=sell_intent.lot_id,
        linked_tlh_proceeds_usd=round(filled_proceeds_usd, 2),
    )


def generate_roth_rebalance_sell_intents(
    config: BotConfig,
    tranche: TrancheConfig,
    snapshot: AccountSnapshot,
    regime: MarketRegime,
    trade_date: date,
) -> List[OrderIntent]:
    """Critic #2 Fix #20: Runs Roth 0%-tax drift trims BEFORE core buys so cash is truly realized first."""
    if tranche.account_type != AccountType.ROTH_IRA or snapshot.nav_usd <= 0.0:
        return []
    intents: List[OrderIntent] = []
    for sym, target_w in tranche.core_equity_targets.items():
        px = regime.prices.get(sym, 0.0)
        if px <= 0.0:
            continue
        cur_w = (snapshot.positions.get(sym, 0.0) * px) / snapshot.nav_usd
        if (cur_w - target_w) >= tranche.rebalance_drift_band:
            excess_usd = (cur_w - target_w) * snapshot.nav_usd
            sell_qty = min(int(math.floor(excess_usd / px)), int(math.floor(snapshot.positions.get(sym, 0.0))))
            if sell_qty >= 1 and (sell_qty * px) >= config.risk.min_order_notional_usd:
                intents.append(
                    OrderIntent(
                        account_id=tranche.account_id,
                        account_type=tranche.account_type,
                        intent_type=IntentType.CORE_REBALANCE_SELL,
                        asset_class=AssetClass.EQUITY,
                        symbol=sym,
                        action="SELL",
                        quantity=sell_qty,
                        limit_price=round(px * 0.997, 2),
                        estimated_mid_price=round(px, 2),
                        rationale=(
                            f"Roth 0%-tax drift trim: {sym} weight {cur_w*100:.1f}% > "
                            f"target {target_w*100:.1f}% (+{(cur_w-target_w)*100:.1f}%)."
                        ),
                        trade_date=trade_date,
                        is_risk_reducing=True,
                    )
                )
    return intents


def generate_core_buy_intents(
    config: BotConfig,
    tranche: TrancheConfig,
    snapshot: AccountSnapshot,
    regime: MarketRegime,
    ledger: StateLedger,
    trade_date: date,
) -> List[OrderIntent]:
    """Deploys actual post-sell available cash into underweight core ETFs, respecting TLH wash-sale locks."""
    intents: List[OrderIntent] = []
    nav = snapshot.nav_usd
    if nav <= 0.0:
        return intents

    ring_by_primary: Dict[str, List[str]] = {}
    for ring in tranche.tlh_rings:
        if ring:
            ring_by_primary[ring[0].upper()] = [s.upper() for s in ring]

    sleeve_vals: Dict[str, float] = {}
    for primary in tranche.core_equity_targets:
        members = ring_by_primary.get(primary.upper(), [primary.upper()])
        sleeve_vals[primary] = sum(
            snapshot.positions.get(m, 0.0) * regime.prices.get(m, 0.0) for m in members
        )

    sweep_sym = tranche.cash_sweep_etf.upper()
    sweep_val = snapshot.positions.get(sweep_sym, 0.0) * regime.prices.get(sweep_sym, 0.0)
    deployable_cash = max(0.0, snapshot.effective_cash_usd - tranche.min_cash_buffer_usd)

    for primary, target_w in tranche.core_equity_targets.items():
        target_usd = nav * target_w
        deficit_usd = target_usd - sleeve_vals.get(primary, 0.0)
        if deficit_usd < config.risk.min_order_notional_usd:
            continue
        members = ring_by_primary.get(primary.upper(), [primary.upper()])
        chosen_sym = None
        for cand in members:
            if ledger.get_active_wash_sale_block(cand, trade_date) is None and regime.prices.get(cand, 0.0) > 0.0:
                chosen_sym = cand
                break
        if chosen_sym is None:
            continue
        px = regime.prices[chosen_sym]
        lmt_px = round(px * 1.003, 2)
        needed_reserve_cash = max(0.0, nav * tranche.target_cash_reserve_pct - sweep_val)
        cash_for_eq = max(0.0, deployable_cash - needed_reserve_cash)
        # Respect max_single_order_pct_nav cap when account already holds positions (tranche cleanly!)
        single_order_cap = (
            nav * config.risk.max_single_order_pct_nav - 5.0
            if len(snapshot.positions) > 0
            else float("inf")
        )
        # Reserve $5 commission/rounding cushion per buy
        alloc_usd = min(deficit_usd, max(0.0, cash_for_eq - 5.0), max(0.0, single_order_cap))
        qty = int(math.floor(alloc_usd / lmt_px))
        if qty >= 1 and (qty * lmt_px) >= config.risk.min_order_notional_usd:
            deployable_cash = max(0.0, deployable_cash - (qty * lmt_px + 2.0))
            intents.append(
                OrderIntent(
                    account_id=tranche.account_id,
                    account_type=tranche.account_type,
                    intent_type=IntentType.CORE_BUY,
                    asset_class=AssetClass.EQUITY,
                    symbol=chosen_sym,
                    action="BUY",
                    quantity=qty,
                    limit_price=lmt_px,
                    estimated_mid_price=round(px, 2),
                    max_step_limit_price=round(px * 1.005, 2),
                    rationale=(
                        f"Deploy idle cash into core sleeve {chosen_sym} "
                        f"(target {target_w*100:.0f}%, deficit ${deficit_usd:,.0f})."
                    ),
                    trade_date=trade_date,
                    is_risk_reducing=False,
                )
            )
    return intents


def generate_cash_sweep_intent(
    config: BotConfig,
    tranche: TrancheConfig,
    snapshot: AccountSnapshot,
    regime: MarketRegime,
    ledger: StateLedger,
    trade_date: date,
) -> Optional[OrderIntent]:
    """
    Critic #2 Fix #20: Sized strictly from ACTUAL post-CORE_BUY cash (leaving min_cash_buffer_usd + $5 fee cushion)
    so rounding/commissions never trip the cash buffer check!
    """
    sweep_sym = tranche.cash_sweep_etf.upper()
    sweep_px = regime.prices.get(sweep_sym, 0.0)
    if sweep_px <= 0.0:
        return None
    if ledger.get_active_wash_sale_block(sweep_sym, trade_date) is not None:
        return None
    deployable = max(0.0, snapshot.effective_cash_usd - tranche.min_cash_buffer_usd - 5.0)
    if deployable < config.risk.min_order_notional_usd:
        return None
    lmt_px = round(sweep_px * 1.002, 2)
    qty = int(math.floor(deployable / lmt_px))
    if qty < 1:
        return None
    return OrderIntent(
        account_id=tranche.account_id,
        account_type=tranche.account_type,
        intent_type=IntentType.CASH_SWEEP_BUY,
        asset_class=AssetClass.EQUITY,
        symbol=sweep_sym,
        action="BUY",
        quantity=qty,
        limit_price=lmt_px,
        estimated_mid_price=round(sweep_px, 2),
        max_step_limit_price=round(sweep_px * 1.003, 2),
        rationale=(
            f"Sweep residual cash into state-exempt Treasury ETF {sweep_sym} "
            f"(eliminates IBKR <$100k cash drag & serves as 95% collateral)."
        ),
        trade_date=trade_date,
        is_risk_reducing=False,
    )


def generate_xsp_overlay_intent(
    config: BotConfig,
    tranche: TrancheConfig,
    snapshot: AccountSnapshot,
    regime: MarketRegime,
    trade_date: date,
    xsp_chain_tuple: Tuple[Optional[str], Optional[int], Optional[pd.DataFrame], bool],
) -> Tuple[Optional[OrderIntent], str]:
    """
    Critic #2 Fixes #4, #12, #19:
      - Strictly deterministic `spread_id` (ZERO random UUID!).
      - Guards Yahoo `IV ~ 1e-5` collapse (falls back to VIX/100 whenever IV < 0.05 or > 1.50).
      - Hard caps wing width `min_spread_width <= width <= max_spread_width`.
      - Computes exact limit credit `mid - 0.25 * (mid - natural)` and embeds hard stepping floor!
    """
    cfg = config.xsp
    if not tranche.allow_xsp_overlay:
        return None, "XSP overlay disabled on tranche."
    if not math.isfinite(regime.vix) or regime.vix < cfg.min_vix:
        return None, f"Stand down: VIX {regime.vix:.2f} < {cfg.min_vix:.1f} threshold."
    if not math.isfinite(regime.vrp_vol_pts) or regime.vrp_vol_pts < cfg.min_vrp_vol_pts:
        return None, (
            f"Stand down: Live VRP {regime.vrp_vol_pts:+.2f} pts "
            f"(IV30={regime.effective_iv30_pct:.1f}% - RV21={regime.rv21_spy_pct:.1f}%) < +{cfg.min_vrp_vol_pts:.1f} pts."
        )
    if not math.isfinite(regime.vix_term_ratio) or regime.vix_term_ratio > cfg.max_vix_term_ratio:
        return None, f"Stand down: VIX/VIX3M {regime.vix_term_ratio:.3f} > {cfg.max_vix_term_ratio:.2f} (backwardation/missing)."

    exp_str, dte, puts_df, is_proxy = xsp_chain_tuple
    if exp_str is None or dte is None or puts_df is None or puts_df.empty:
        return None, "Stand down: No valid 35-55 DTE XSP option chain available."

    spot = regime.xsp_spot if (not is_proxy and regime.xsp_spot > 0.0) else (regime.spy_spot if is_proxy else 0.0)
    if spot <= 0.0:
        return None, "Stand down: Missing live XSP spot price."

    fallback_iv = max(0.08, regime.effective_iv30_pct / 100.0)
    work = puts_df[puts_df["strike"] < spot].copy()
    if work.empty:
        return None, "Stand down: No OTM XSP puts available."

    def _clean_leg_iv(val: Any) -> float:
        iv = _safe_float(val, 0.0)
        return iv if (0.05 <= iv <= 1.50) else fallback_iv

    work["clean_iv"] = work["impliedVolatility"].apply(_clean_leg_iv) if "impliedVolatility" in work.columns else fallback_iv
    work["est_delta"] = work.apply(
        lambda row: _estimate_put_delta(spot, _safe_float(row["strike"]), dte, float(row["clean_iv"])),
        axis=1,
    )
    work = work[work["est_delta"] <= cfg.max_short_delta].copy()
    if work.empty:
        return None, f"Stand down: No OTM XSP puts with |delta| <= {cfg.max_short_delta:.2f}."

    # Keep only short strikes that actually have a valid protective wing within [min_spread_width, max_spread_width]
    avail_strikes = set(work["strike"].astype(float).tolist())
    short_pool = work[
        work["strike"].apply(
            lambda ks: any(
                (ks - cfg.max_spread_width) <= kl <= (ks - cfg.min_spread_width)
                for kl in avail_strikes
            )
        )
    ].copy()
    if short_pool.empty:
        return None, f"Stand down: No XSP put pairs with width in [${cfg.min_spread_width:.0f}, ${cfg.max_spread_width:.0f}]."

    short_pool["delta_err"] = (short_pool["est_delta"] - cfg.target_short_delta).abs()
    short_row = short_pool.sort_values("delta_err").iloc[0]
    k_short = _safe_float(short_row["strike"])
    target_k_long = k_short - cfg.spread_width
    # Hard width cap: [min_spread_width, max_spread_width]
    long_cands = work[
        (work["strike"] <= k_short - cfg.min_spread_width)
        & (work["strike"] >= k_short - cfg.max_spread_width)
    ].copy()
    if long_cands.empty:
        return None, f"Stand down: No valid protective put wing within [${cfg.min_spread_width:.0f}, ${cfg.max_spread_width:.0f}]."
    long_cands["k_err"] = (long_cands["strike"] - target_k_long).abs()
    long_row = long_cands.sort_values("k_err").iloc[0]
    k_long = _safe_float(long_row["strike"])
    width = k_short - k_long

    s_bid, s_ask = _safe_float(short_row["bid"]), _safe_float(short_row["ask"])
    l_bid, l_ask = _safe_float(long_row["bid"]), _safe_float(long_row["ask"])
    s_mid, l_mid = 0.5 * (s_bid + s_ask), 0.5 * (l_bid + l_ask)
    mid_credit = max(0.0, s_mid - l_mid)
    nat_credit = max(0.0, s_bid - l_ask)
    # 25% of the way from mid toward natural credit
    limit_credit = round(max(0.01, mid_credit - 0.25 * max(0.0, mid_credit - nat_credit)), 2)
    max_loss_per_c = max(0.50, width - limit_credit) * 100.0

    avail_trade = snapshot.nav_usd * cfg.max_risk_per_trade_pct
    avail_total = max(0.0, snapshot.nav_usd * cfg.max_total_options_risk_pct - snapshot.total_open_options_risk_usd)
    qty = int(math.floor(min(avail_trade, avail_total) / max_loss_per_c))
    if qty < 1:
        return None, (
            f"Sized to 0 contracts: 1x ${width:.0f}-wide XSP spread max loss (${max_loss_per_c:,.0f}) "
            f"exceeds 2.5% NAV cap (${avail_trade:,.0f}) on {tranche.account_type.value} NAV ${snapshot.nav_usd:,.0f}."
        )

    # Hard stepping floor: adaptive stepping can NEVER reduce credit below min_net_credit OR breach 2.5% NAV cap!
    cap_floor_credit = max(cfg.min_net_credit, width - (avail_trade / (100.0 * qty)))
    cap_floor_credit = round(min(limit_credit, max(cap_floor_credit, cfg.min_net_credit)), 2)

    legs = [
        OptionLegSpec(
            symbol="XSP",
            expiry=exp_str,
            strike=k_short,
            right="P",
            action="SELL",
            bid=s_bid,
            ask=s_ask,
            open_interest=int(_safe_float(short_row.get("openInterest"), 0)),
            implied_vol=float(short_row["clean_iv"]),
            delta=-_safe_float(short_row["est_delta"]),
            is_european=True,
        ),
        OptionLegSpec(
            symbol="XSP",
            expiry=exp_str,
            strike=k_long,
            right="P",
            action="BUY",
            bid=l_bid,
            ask=l_ask,
            open_interest=int(_safe_float(long_row.get("openInterest"), 0)),
            implied_vol=float(long_row["clean_iv"]),
            delta=-_safe_float(long_row["est_delta"]),
            is_european=True,
        ),
    ]
    # Critic #2 Fix #4: Strictly deterministic spread_id (ZERO random UUID, preserves .50 strikes!)
    spread_id = f"XSP-{exp_str}-{k_short:.2f}P-{k_long:.2f}P-{trade_date.isoformat()}"
    intent = OrderIntent(
        account_id=tranche.account_id,
        account_type=tranche.account_type,
        intent_type=IntentType.XSP_OPEN_CREDIT_SPREAD,
        asset_class=AssetClass.OPTION_COMBO,
        symbol="XSP",
        action="BUY",
        quantity=qty,
        limit_price=limit_credit,
        estimated_mid_price=round(mid_credit, 2),
        max_step_limit_price=cap_floor_credit,
        rationale=(
            f"Rich-VRP European XSP {exp_str} ({dte} DTE) {k_short:.1f}/{k_long:.1f}P credit spread "
            f"@ ${limit_credit:.2f} cr (VIX={regime.vix:.1f}, VRP={regime.vrp_vol_pts:+.1f} pts, "
            f"Term={regime.vix_term_ratio:.2f})."
        ),
        trade_date=trade_date,
        is_risk_reducing=False,
        legs=legs,
        spread_id=spread_id,
        max_loss_total_usd=round(max_loss_per_c * qty, 2),
        net_credit_per_share=limit_credit,
        dte_days=dte,
        is_proxy_chain=is_proxy,
    )
    return intent, "Candidate XSP spread built."


def build_advisory_munger_digest(
    config: BotConfig,
    coi_blocked: Set[str],
    max_rows: int = 5,
) -> List[Dict[str, Any]]:
    """
    Critic #2 Fix #27: Read-only advisory research feed filtering against `RiskFirewall.coi_blocked`
    (including MedTech review tickers when `block_medtech_review=True`). Zero automatic orders.
    """
    try:
        wl_path = config.project_root / "data" / "curated_watchlist.json"
        if not wl_path.exists():
            return []
        with open(wl_path, "r", encoding="utf-8") as f:
            wl = json.load(f)
        items = wl.get("watchlist", wl) if isinstance(wl, dict) else wl
        out: List[Dict[str, Any]] = []
        if isinstance(items, dict):
            for sym, meta in items.items():
                su = str(sym).upper()
                if su in coi_blocked:
                    continue
                if isinstance(meta, dict):
                    out.append({
                        "symbol": su,
                        "status": meta.get("category", meta.get("status", "WATCHLIST")),
                        "note": str(meta.get("thesis", meta.get("note", "")))[:120],
                        "execution_policy": "ADVISORY_ONLY_NO_AUTO_CHURN",
                    })
        elif isinstance(items, list):
            for rec in items:
                if not isinstance(rec, dict):
                    continue
                su = str(rec.get("ticker", rec.get("symbol", ""))).upper()
                if not su or su in coi_blocked:
                    continue
                out.append({
                    "symbol": su,
                    "status": rec.get("category", rec.get("status", "WATCHLIST")),
                    "note": str(rec.get("thesis", rec.get("note", "")))[:120],
                    "execution_policy": "ADVISORY_ONLY_NO_AUTO_CHURN",
                })
        return out[:max_rows]
    except Exception:
        return []
