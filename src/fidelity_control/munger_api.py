"""
================================================================================
MUNGER FACTOR SYSTEM: DEDICATED WEB & REST API CONTROLLER
================================================================================
Provides high-performance, vectorized backend endpoints for:
  1. Market clock & data freshness circuit breaker status
  2. Live intraday price refresh via multithreaded yfinance
  3. Interactive Munger factor screener with dynamic sliders & checkboxes
  4. SEC Form 4 / Finviz insider conviction engine
  5. Fidelity tax-lot reconciliation with dynamic LTCG tax-deferral analysis
  6. Automated Fidelity order generation, copyable tickets, and deep-links
  7. 80/20 Options collar recommendations
================================================================================
"""

from __future__ import annotations

import io
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Ensure repository root is on sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from munger_engine import (
    check_data_freshness,
    compute_vectorized_munger_scores,
    fetch_live_insider_activity,
    load_or_generate_dataset,
    parse_fidelity_positions_csv,
    refresh_live_intraday_prices,
    sanitize_universe_fundamentals,
    bsm_call,
    implied_vol_with_skew,
)
from .fidelity_connector import generate_fidelity_deeplink, generate_fidelity_research_link

def bsm_put(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.01) -> Dict[str, float]:
    """Closed-form European Put price via BSM Put-Call Parity."""
    call_res = bsm_call(S, K, T, r, sigma, q)
    put_price = max(0.0, call_res["price"] - S * math.exp(-q * T) + K * math.exp(-r * T))
    return {
        "price": put_price,
        "delta": call_res["delta"] - 1.0,
    }

# Global In-Memory Caches for instantaneous UI responsiveness
_DATASET_CACHE: Dict[str, Any] = {}
_INSIDER_CACHE: Dict[str, Any] = {}
_LAST_SCAN_CACHE: Dict[str, Any] = {}


def _get_dataset():
    """Retrieves or loads cached dataset."""
    global _DATASET_CACHE
    if not _DATASET_CACHE or "close_df" not in _DATASET_CACHE:
        data_dir = ROOT_DIR / "data"
        close_df, gspc_close, fund_dict, eps_dict, is_synthetic = load_or_generate_dataset(data_dir)
        clean_fund = sanitize_universe_fundamentals(fund_dict, eps_dict)
        _DATASET_CACHE = {
            "close_df": close_df,
            "gspc_close": gspc_close,
            "fund_dict": fund_dict,
            "clean_fund": clean_fund,
            "eps_dict": eps_dict,
            "is_synthetic": is_synthetic,
            "loaded_at": time.time(),
        }
    return _DATASET_CACHE


def get_munger_status_api() -> Dict[str, Any]:
    """Returns dataset freshness, constituent counts, and market session state."""
    ds = _get_dataset()
    close_df = ds["close_df"]
    is_stale, lag_hours, latest_dt = check_data_freshness(close_df)

    # Determine market clock status
    now_utc = datetime.now(timezone.utc)
    # Eastern approx UTC-4
    nyse_hour = (now_utc.hour - 4) % 24
    weekday = now_utc.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    if weekday in [5, 6]:
        session_state = "WEEKEND_CLOSED"
        session_desc = "Market closed for the weekend."
    elif 9 <= nyse_hour < 16:
        session_state = "REGULAR_MARKET_OPEN"
        session_desc = "Regular trading session active (9:30 AM - 4:00 PM EST)."
    elif 16 <= nyse_hour < 20:
        session_state = "POST_MARKET"
        session_desc = "Post-market session active (4:00 PM - 8:00 PM EST)."
    else:
        session_state = "PRE_MARKET_CLOSED"
        session_desc = "Market closed until 9:30 AM EST."

    return {
        "status": "ONLINE",
        "is_stale": bool(is_stale),
        "lag_hours": round(float(lag_hours), 1),
        "latest_bar_date": latest_dt.strftime("%Y-%m-%d"),
        "latest_bar_time": latest_dt.strftime("%Y-%m-%d %H:%M EST"),
        "total_constituents": len(close_df.columns),
        "session_state": session_state,
        "session_desc": session_desc,
        "server_time_iso": datetime.now().isoformat(),
    }


def refresh_prices_api() -> Dict[str, Any]:
    """Refreshes live prices via multithreaded yfinance and updates the local cache."""
    global _DATASET_CACHE
    ds = _get_dataset()
    close_df = ds["close_df"]
    gspc_close = ds["gspc_close"]
    save_path = ROOT_DIR / "data" / "historical_prices_1970_2026.pkl.xz"

    t0 = time.time()
    updated_close, updated_gspc = refresh_live_intraday_prices(
        close_df=close_df,
        gspc_close=gspc_close,
        save_path=save_path,
    )
    elapsed = round(time.time() - t0, 2)

    # Invalidate cache so it re-reads
    _DATASET_CACHE["close_df"] = updated_close
    _DATASET_CACHE["gspc_close"] = updated_gspc
    _DATASET_CACHE["loaded_at"] = time.time()

    latest_dt = updated_close.index[-1]
    is_stale, lag_hours, _ = check_data_freshness(updated_close)

    return {
        "status": "SUCCESS",
        "elapsed_seconds": elapsed,
        "latest_bar_date": latest_dt.strftime("%Y-%m-%d"),
        "latest_bar_time": latest_dt.strftime("%Y-%m-%d %H:%M EST"),
        "is_stale": bool(is_stale),
        "lag_hours": round(float(lag_hours), 1),
        "total_bars": len(updated_close),
    }


def run_munger_scan_api(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Executes live Munger factor screening based on user sliders and checkboxes.
    """
    params = params or {}
    min_munger = float(params.get("min_munger", 2.0))
    enforce_conjunctive = bool(params.get("conjunctive", True))
    min_sales5y = float(params.get("min_sales5y", 0.0))
    min_roic = float(params.get("min_roic", 0.0))
    max_pe = float(params.get("max_pe", 80.0))
    max_de = float(params.get("max_de", 250.0))
    winsorize = bool(params.get("winsorize", True))
    enforce_full_cycle = bool(params.get("enforce_full_cycle", True))
    use_cyclically_adjusted_ey = bool(params.get("use_cyclically_adjusted_ey", True))
    include_insider = bool(params.get("include_insider", True))
    cluster_boost_enabled = bool(params.get("cluster_boost", True))
    csuite_haircut_enabled = bool(params.get("csuite_haircut", True))
    min_insider_buys = float(params.get("min_insider_buys", 0.0))
    gating_200w_enabled = bool(params.get("gating_200w", False))

    ds = _get_dataset()
    close_df = ds["close_df"]
    fund_dict = ds["fund_dict"]
    clean_fund = ds["clean_fund"]
    eps_dict = ds["eps_dict"]

    latest_dates = close_df.index[-2:]
    dt_last = latest_dates[-1]

    # Compute raw legacy scores
    scores_raw, med_raw, ey_raw = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=latest_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=0,
        allow_legacy_lookahead=True,
        winsorize_cross_section=False,
        enforce_full_cycle_profitability=False,
        use_cyclically_adjusted_ey=False,
    )

    # Compute clean Value Trap-immunized scores with user parameters
    scores_clean, med_clean, ey_clean = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=latest_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
        winsorize_cross_section=winsorize,
        enforce_full_cycle_profitability=enforce_full_cycle,
        use_cyclically_adjusted_ey=use_cyclically_adjusted_ey,
    )

    # Identify candidate tickers for insider data
    clean_series = scores_clean.loc[dt_last].sort_values(ascending=False)
    top_candidates = list(clean_series.head(20).index)
    raw_series = scores_raw.loc[dt_last]
    cyclical_traps_flagged = [
        t for t in close_df.columns
        if float(raw_series.get(t, 0.0)) >= 2.0 and float(clean_series.get(t, 0.0)) == 0.0
    ]

    insider_query_tickers = list(dict.fromkeys(top_candidates + cyclical_traps_flagged + ["NVDA", "AAPL", "MU", "LLY", "FTNT", "MSFT", "GOOGL"]))

    global _INSIDER_CACHE
    tickers_to_query = [t for t in insider_query_tickers if t not in _INSIDER_CACHE]
    if tickers_to_query and include_insider:
        try:
            new_insider = fetch_live_insider_activity(tickers_to_query)
            _INSIDER_CACHE.update(new_insider)
        except Exception:
            pass

    # Build cross-sectional percentile rankings for the radar view
    growth_vals = {t: float(clean_fund.get(t, {}).get("sales5y", 0.0)) for t in close_df.columns}
    roic_vals = {t: float(fund_dict.get(t, {}).get("roic_proxy", 0.0)) * 100.0 for t in close_df.columns}
    pe_vals = {t: float(fund_dict.get(t, {}).get("pe", 25.0)) for t in close_df.columns}
    de_vals = {t: float(fund_dict.get(t, {}).get("de", 50.0)) for t in close_df.columns}
    nd_ebitda_vals = {t: float(fund_dict.get(t, {}).get("net_debt_to_ebitda", 1.2)) for t in close_df.columns}

    growth_s = pd.Series(growth_vals)
    roic_s = pd.Series(roic_vals)
    pe_s = pd.Series(pe_vals)
    de_s = pd.Series(de_vals)
    nd_ebitda_s = pd.Series(nd_ebitda_vals)

    growth_pct = (growth_s.rank(pct=True) * 100.0).to_dict()
    roic_pct = (roic_s.rank(pct=True) * 100.0).to_dict()
    # For PE and Net Debt / EBITDA, lower is better valuation/solvency
    pe_pct = ((1.0 - pe_s.rank(pct=True)) * 100.0).to_dict()
    de_pct = ((1.0 - de_s.rank(pct=True)) * 100.0).to_dict()
    solvency_pct = ((1.0 - nd_ebitda_s.rank(pct=True)) * 100.0).to_dict()

    # Calculate 200-week SMA for pullback gating if requested
    sma_200w_dist = {}
    if gating_200w_enabled or True:  # always precompute for detail modal
        for t in close_df.columns:
            s_close = close_df[t].dropna()
            if len(s_close) >= 200 * 5:
                # 200 weeks approx 1000 daily trading bars
                sma_200w = s_close.iloc[-1000:].mean()
                cur_p = float(s_close.iloc[-1])
                dist = ((cur_p / sma_200w) - 1.0) * 100.0
                sma_200w_dist[t] = round(dist, 1)
            else:
                sma_200w_dist[t] = 0.0

    candidates = []
    strike_zone_count = 0
    cyclical_traps_count = 0
    cluster_buys_count = 0
    csuite_haircuts_count = 0

    for t in close_df.columns:
        meta = fund_dict.get(t, {})
        name = meta.get("name", t)
        sector = meta.get("sector", "General")
        theme = meta.get("theme", sector)

        raw_m = float(scores_raw.loc[dt_last, t]) if pd.notna(scores_raw.loc[dt_last, t]) else 0.0
        clean_m = float(scores_clean.loc[dt_last, t]) if pd.notna(scores_clean.loc[dt_last, t]) else 0.0
        passed_all_med = bool(med_clean.loc[dt_last, t])

        spot_price = round(float(close_df.loc[dt_last, t]), 2)
        sales5y = round(float(clean_fund.get(t, {}).get("sales5y", 0.0)), 1)
        roic = round(float(meta.get("roic_proxy", 0.0)) * 100.0, 1)
        pe = round(float(meta.get("pe", 25.0)), 1)
        pb = round(float(meta.get("pb", 3.0)), 2)
        de = round(float(meta.get("de", 50.0)), 1)
        nd_ebitda = round(float(meta.get("net_debt_to_ebitda", 1.2)), 2)

        # Check insider conviction
        insider_info = _INSIDER_CACHE.get(t, {"multiplier": 1.0, "signal": "NEUTRAL", "notes": "", "buys_usd": 0.0, "sales_usd": 0.0})
        insider_mult = float(insider_info.get("multiplier", 1.0))

        # Adjust multiplier based on user toggles
        if not cluster_boost_enabled and insider_mult > 1.0:
            insider_mult = 1.0
        if not csuite_haircut_enabled and insider_mult < 1.0:
            insider_mult = 1.0
        if not include_insider:
            insider_mult = 1.0

        if insider_mult > 1.0:
            cluster_buys_count += 1
        elif insider_mult < 1.0:
            csuite_haircuts_count += 1

        conviction_m = round(clean_m * insider_mult, 2)

        # Slider threshold checks
        passes_sliders = True
        if sales5y < min_sales5y:
            passes_sliders = False
        if roic < min_roic:
            passes_sliders = False
        if pe > max_pe:
            passes_sliders = False
        # Solvency hurdle: Net Debt / EBITDA (if slider <= 15.0) or legacy D/E
        if max_de <= 15.0:
            if nd_ebitda > max_de:
                passes_sliders = False
        else:
            if de > max_de:
                passes_sliders = False

        if enforce_conjunctive and not passed_all_med:
            passes_sliders = False
        if gating_200w_enabled and sma_200w_dist.get(t, 0.0) > 25.0:
            passes_sliders = False
        if min_insider_buys > 0.0 and float(insider_info.get("buys_usd", 0.0)) < min_insider_buys:
            passes_sliders = False

        # Status assignment
        is_trap = (raw_m >= 2.0 and clean_m == 0.0)
        is_strike_zone = (clean_m >= min_munger and (passed_all_med or not enforce_conjunctive) and passes_sliders)

        if is_strike_zone:
            strike_zone_count += 1
            if insider_mult > 1.0:
                audit_status = f"STRIKE ZONE (CLUSTER BUY {insider_mult:.2f}x)"
            elif insider_mult < 1.0:
                audit_status = f"STRIKE ZONE (C-SUITE HAIRCUT {insider_mult:.2f}x)"
            else:
                audit_status = "STRIKE ZONE"
        elif is_trap:
            cyclical_traps_count += 1
            audit_status = "CYCLICAL TRAP BLOCKED"
        elif clean_m >= 1.0 or passes_sliders:
            audit_status = "Watchlist (High Moat)"
        else:
            audit_status = "Neutral / Low Hurdle"

        # Pillar-by-pillar pass evaluation against cross-sectional median and user hurdles
        med_g = float(growth_s.median())
        med_r = float(roic_s.median())
        med_y = float(pe_s.median())
        med_s = float(de_s.median())
        med_nd = float(nd_ebitda_s.median())

        pass_g = bool(sales5y >= med_g and sales5y >= min_sales5y)
        pass_r = bool(roic >= med_r and roic >= min_roic)
        pass_y = bool(pe <= med_y and pe <= max_pe and pe > 0)
        if max_de <= 15.0:
            pass_s = bool(nd_ebitda <= max_de)
        else:
            pass_s = bool(de <= med_s and de <= max_de)
        passed_count = int(pass_g) + int(pass_r) + int(pass_y) + int(pass_s)
        failed_pillars = [p for p, ok in [("Growth", pass_g), ("ROIC", pass_r), ("Valuation", pass_y), ("Solvency", pass_s)] if not ok]

        candidates.append({
            "ticker": t,
            "name": name,
            "sector": sector,
            "theme": theme,
            "spot": spot_price,
            "sales5y": sales5y,
            "roic": roic,
            "pe": pe,
            "pb": pb,
            "de": de,
            "nd_ebitda": nd_ebitda,
            "raw_m": round(raw_m, 2),
            "clean_m": round(clean_m, 2),
            "insider_mult": insider_mult,
            "conviction_m": conviction_m,
            "all_med": passed_all_med,
            "pillar_pass": {
                "growth": pass_g,
                "roic": pass_r,
                "pe": pass_y,
                "de": pass_s,
            },
            "passed_count": passed_count,
            "failed_pillars": failed_pillars,
            "audit_status": audit_status,
            "is_strike_zone": is_strike_zone,
            "is_trap": is_trap,
            "passes_sliders": passes_sliders,
            "dist_200w_pct": sma_200w_dist.get(t, 0.0),
            "insider_signal": insider_info.get("signal", "NEUTRAL"),
            "insider_notes": insider_info.get("notes", "No open-market filings flagged."),
            "insider_buys_usd": float(insider_info.get("buys_usd", 0.0)),
            "insider_sales_usd": float(insider_info.get("sales_usd", 0.0)),
            "radar": {
                "growth": round(growth_pct.get(t, 50.0), 1),
                "roic": round(roic_pct.get(t, 50.0), 1),
                "valuation": round(pe_pct.get(t, 50.0), 1),
                "solvency": round(solvency_pct.get(t, 50.0), 1),
            },
            "fidelity_deeplink": generate_fidelity_deeplink(t, "BUY"),
            "fidelity_research": generate_fidelity_research_link(t),
        })

    # Sort primarily by Conviction Score descending, then Clean Munger Score
    candidates.sort(key=lambda x: (x["is_strike_zone"], x["conviction_m"], x["clean_m"], x["raw_m"]), reverse=True)

    result = {
        "status": "SUCCESS",
        "eval_date": dt_last.strftime("%Y-%m-%d"),
        "total_screened": len(candidates),
        "strike_zone_count": strike_zone_count,
        "cyclical_traps_count": cyclical_traps_count,
        "cluster_buys_count": cluster_buys_count,
        "csuite_haircuts_count": csuite_haircuts_count,
        "medians": {
            "sales5y": round(float(growth_s.median()), 1),
            "roic": round(float(roic_s.median()), 1),
            "pe": round(float(pe_s.median()), 1),
            "de": round(float(de_s.median()), 1),
            "nd_ebitda": round(float(nd_ebitda_s.median()), 2),
        },
        "parameters_applied": {
            "min_munger": min_munger,
            "conjunctive": enforce_conjunctive,
            "min_sales5y": min_sales5y,
            "min_roic": min_roic,
            "max_pe": max_pe,
            "max_de": max_de,
            "winsorize": winsorize,
            "enforce_full_cycle": enforce_full_cycle,
            "use_cyclically_adjusted_ey": use_cyclically_adjusted_ey,
            "include_insider": include_insider,
            "cluster_boost": cluster_boost_enabled,
            "csuite_haircut": csuite_haircut_enabled,
        },
        "candidates": candidates,
    }

    global _LAST_SCAN_CACHE
    _LAST_SCAN_CACHE = result
    return result


def reconcile_fidelity_api(csv_text: Optional[str] = None) -> Dict[str, Any]:
    """
    Parses Fidelity CSV content (or defaults to sample file), overlays live prices,
    tests the 365-day LTCG threshold, and returns actionable orders.
    """
    ds = _get_dataset()
    close_df = ds["close_df"]
    dt_last = close_df.index[-1]
    live_prices_s = close_df.loc[dt_last]

    # Get cached conviction scores or run quick default scan
    global _LAST_SCAN_CACHE
    if not _LAST_SCAN_CACHE:
        run_munger_scan_api()

    conviction_scores = {c["ticker"]: c["conviction_m"] for c in _LAST_SCAN_CACHE.get("candidates", [])}
    immunized_flags = {c["ticker"]: c["all_med"] for c in _LAST_SCAN_CACHE.get("candidates", [])}

    csv_path = None
    temp_file = None
    if csv_text and csv_text.strip():
        temp_file = ROOT_DIR / "data" / "_temp_uploaded_portfolio.csv"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(csv_text)
        csv_path = temp_file

    try:
        fidelity_df = parse_fidelity_positions_csv(
            csv_path=csv_path,
            live_prices=live_prices_s,
            eval_date=dt_last,
            munger_scores=conviction_scores,
            immunized_flags=immunized_flags,
        )
    finally:
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

    if fidelity_df.empty:
        return {
            "status": "EMPTY",
            "message": "No valid positions found in Fidelity CSV.",
            "lots": [],
            "summary": {},
        }

    # Clean NaN / Infs
    fidelity_df = fidelity_df.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    lots = []
    potential_tax_savings = 0.0

    for idx, row in fidelity_df.iterrows():
        sym = str(row.get("Symbol", "")).strip().upper()
        shares = float(row.get("Shares", 0.0))
        cost_basis = float(row.get("Cost_Basis", 0.0))
        live_price = float(row.get("Live_Price", 0.0))
        live_val = float(row.get("Live_Value", 0.0))
        gain = float(row.get("Unrealized_Gain", 0.0))
        gain_pct = round((gain / cost_basis * 100.0), 2) if cost_basis > 0 else 0.0
        days_held = int(row.get("Days_Held", 0))
        ltcg_status = str(row.get("LTCG_Status", "STCG"))
        action = str(row.get("Action", "HOLD"))
        rationale = str(row.get("Rationale", ""))

        days_to_ltcg = max(0, 365 - days_held)
        # Tax drag savings estimation: ordinary income (approx 37-40.8%) vs LTCG (20%) = ~17-20.8% savings
        est_tax_savings = 0.0
        if "HOLD FOR TAX DEFERRAL" in action and gain > 0:
            est_tax_savings = round(gain * 0.208, 2)
            potential_tax_savings += est_tax_savings

        lots.append({
            "lot_id": idx + 1,
            "symbol": sym,
            "shares": shares,
            "cost_basis": round(cost_basis, 2),
            "live_price": round(live_price, 2),
            "live_value": round(live_val, 2),
            "unrealized_gain": round(gain, 2),
            "unrealized_gain_pct": gain_pct,
            "days_held": days_held,
            "days_to_ltcg": days_to_ltcg,
            "ltcg_status": ltcg_status,
            "action": action,
            "rationale": rationale,
            "est_tax_savings": est_tax_savings,
            "fidelity_deeplink": generate_fidelity_deeplink(sym, "SELL" if "SELL" in action else "BUY"),
        })

    total_val = float(fidelity_df["Live_Value"].sum())
    total_basis = float(fidelity_df["Cost_Basis"].sum())
    total_gain = total_val - total_basis
    total_gain_pct = round((total_gain / total_basis * 100.0), 2) if total_basis > 0 else 0.0
    action_counts = fidelity_df["Action"].value_counts().to_dict()

    return {
        "status": "SUCCESS",
        "eval_date": dt_last.strftime("%Y-%m-%d"),
        "lots": lots,
        "summary": {
            "total_value": round(total_val, 2),
            "total_basis": round(total_basis, 2),
            "total_gain": round(total_gain, 2),
            "total_gain_pct": total_gain_pct,
            "potential_tax_savings": round(potential_tax_savings, 2),
            "action_counts": action_counts,
            "total_lots": len(lots),
        },
    }


def generate_fidelity_orders_api(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes rebalancing order tickets, share quantities, MOC trade strings,
    and direct Fidelity links based on selected strike-zone stocks and existing portfolio.
    """
    selected_buys = params.get("selected_buys", [])  # list of tickers e.g. ["LLY", "FTNT", "COF"]
    selected_sells = params.get("selected_sells", [])  # list of tickers or lot_ids
    available_cash = float(params.get("available_cash", 100000.0))
    total_portfolio_value = float(params.get("total_portfolio_value", 500000.0))
    max_alloc_pct = float(params.get("max_alloc_pct", 15.0)) / 100.0
    cash_buffer_pct = float(params.get("cash_buffer_pct", 5.0)) / 100.0

    ds = _get_dataset()
    close_df = ds["close_df"]
    dt_last = close_df.index[-1]

    # Max dollars per position
    target_pos_size = total_portfolio_value * max_alloc_pct
    cash_reserve = total_portfolio_value * cash_buffer_pct
    deployable_cash = max(0.0, available_cash - cash_reserve)

    orders = []
    total_buy_val = 0.0
    total_sell_val = 0.0

    # 1. Generate SELL Orders (from lots flagged for exit)
    lots_to_sell = params.get("lots_to_sell", [])
    for lot in lots_to_sell:
        sym = lot.get("symbol", "").upper().strip()
        shares = float(lot.get("shares", 0.0))
        spot = float(lot.get("live_price", close_df.loc[dt_last, sym] if sym in close_df else 100.0))
        est_val = round(shares * spot, 2)
        total_sell_val += est_val

        order_str = f"SELL {sym} {int(shares)} SHARES MOC DAY"
        orders.append({
            "symbol": sym,
            "action": "SELL",
            "order_type": "MOC",
            "shares": int(shares),
            "limit_price": spot,
            "est_value": est_val,
            "rationale": lot.get("rationale", "Munger factor decay / tax-loss harvest."),
            "fidelity_order_string": order_str,
            "fidelity_deeplink": generate_fidelity_deeplink(sym, "SELL"),
        })

    # Available cash increases with proceeds from sells
    effective_cash_for_buys = deployable_cash + total_sell_val

    # 2. Generate BUY Orders
    if selected_buys:
        dollars_per_buy = min(target_pos_size, effective_cash_for_buys / len(selected_buys)) if effective_cash_for_buys > 0 else 0.0

        for t in selected_buys:
            sym = t.upper().strip()
            spot = float(close_df.loc[dt_last, sym]) if sym in close_df else 100.0
            if spot <= 0:
                continue

            shares = int(math.floor(dollars_per_buy / spot))
            if shares <= 0:
                continue

            trade_val = round(shares * spot, 2)
            total_buy_val += trade_val
            order_str = f"BUY {sym} {shares} SHARES MOC DAY"

            orders.append({
                "symbol": sym,
                "action": "BUY",
                "order_type": "MOC",
                "shares": shares,
                "limit_price": spot,
                "est_value": trade_val,
                "rationale": f"Munger Strike Zone entry (Target {max_alloc_pct*100:.0f}% cap).",
                "fidelity_order_string": order_str,
                "fidelity_deeplink": generate_fidelity_deeplink(sym, "BUY"),
            })

    # Build CSV export format
    csv_rows = ["Action,Symbol,Quantity,Order_Type,Time_In_Force,Limit_Price,Estimated_Value,Rationale"]
    for o in orders:
        csv_rows.append(f"{o['action']},{o['symbol']},{o['shares']},{o['order_type']},DAY,{o['limit_price']},{o['est_value']},\"{o['rationale']}\"")
    orders_csv = "\n".join(csv_rows)

    return {
        "status": "SUCCESS",
        "eval_date": dt_last.strftime("%Y-%m-%d"),
        "orders": orders,
        "orders_csv": orders_csv,
        "summary": {
            "total_orders": len(orders),
            "total_buy_orders": len([o for o in orders if o["action"] == "BUY"]),
            "total_sell_orders": len([o for o in orders if o["action"] == "SELL"]),
            "total_buy_value": round(total_buy_val, 2),
            "total_sell_value": round(total_sell_val, 2),
            "net_cash_impact": round(total_sell_val - total_buy_val, 2),
            "target_position_cap": round(target_pos_size, 2),
        },
    }


def get_options_collars_api(holdings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Generates 180-365 DTE protective collar recommendations for high-gain holdings.
    """
    ds = _get_dataset()
    close_df = ds["close_df"]
    dt_last = close_df.index[-1]

    target_holdings = holdings or [
        {"symbol": "NVDA", "shares": 100, "unrealized_gain": 85000.0},
        {"symbol": "AAPL", "shares": 200, "unrealized_gain": 32000.0},
        {"symbol": "MSFT", "shares": 100, "unrealized_gain": 22000.0},
    ]

    collars = []
    for h in target_holdings:
        sym = h.get("symbol", "").upper().strip()
        shares = int(h.get("shares", 100))
        contracts = max(1, shares // 100)

        spot = float(close_df.loc[dt_last, sym]) if sym in close_df else 150.0
        T = 0.75  # 270 DTE (~9 months)
        r = 0.045
        base_sigma = 0.35

        # 10% OTM protective put
        put_strike = round(spot * 0.90, 1)
        put_sigma = implied_vol_with_skew(delta=0.35, T=T, base_sigma=base_sigma)
        put_metrics = bsm_put(S=spot, K=put_strike, T=T, r=r, sigma=put_sigma)
        put_price = put_metrics["price"]

        # 15% OTM covered call
        call_strike = round(spot * 1.15, 1)
        call_sigma = implied_vol_with_skew(delta=0.30, T=T, base_sigma=base_sigma)
        call_metrics = bsm_call(S=spot, K=call_strike, T=T, r=r, sigma=call_sigma)
        call_price = call_metrics["price"]

        net_premium_per_share = round(call_price - put_price, 2)
        net_credit_debit = "CREDIT" if net_premium_per_share >= 0 else "DEBIT"
        total_net_cash = round(abs(net_premium_per_share) * 100 * contracts, 2)

        collar_ticket = (
            f"COLLAR {sym} x{contracts}: BUY {put_strike} PUT / SELL {call_strike} CALL "
            f"EXP ~9M ({net_credit_debit} ${abs(net_premium_per_share):.2f}/sh)"
        )

        collars.append({
            "symbol": sym,
            "spot": spot,
            "contracts": contracts,
            "shares_protected": contracts * 100,
            "dte": 270,
            "put_strike": put_strike,
            "put_price": round(put_price, 2),
            "call_strike": call_strike,
            "call_price": round(call_price, 2),
            "net_premium_per_share": net_premium_per_share,
            "net_type": net_credit_debit,
            "total_cash_impact": total_net_cash,
            "downside_floor": round(put_strike, 2),
            "downside_floor_pct": "-10.0%",
            "upside_cap": round(call_strike, 2),
            "upside_cap_pct": "+15.0%",
            "collar_ticket": collar_ticket,
            "fidelity_deeplink": generate_fidelity_deeplink(sym, "BUY"),
        })

    return {
        "status": "SUCCESS",
        "collars": collars,
        "total_protected_value": round(sum(c["spot"] * c["shares_protected"] for c in collars), 2),
    }
