#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR SYSTEM: LIVE STRIKE-ZONE SCANNER & TAX-LOT RECONCILER (v2.3.0)
================================================================================
Production Strike-Zone Screener integrating:
  1. Staleness Circuit Breaker: Automatically detects data lag > 18h against
     the market clock and fetches live intraday prices via multi-threaded yfinance.
  2. Value Trap Filter: Cross-sectional winsorization,
     5-year full-cycle profitability gating, and cyclically-adjusted earnings yield.
  3. SEC Form 4 / Finviz Insider Conviction Engine: Live querying of Form 4 XML
     filings, filtering strictly for open-market Code P buys vs Code S sales,
     applying 1.15x Cluster Buying Boost or 0.70x Heavy C-Suite Distribution Haircut.
  4. Fidelity Tax-Lot Reconciler: Ingests Portfolio_Positions_*.csv, strips cash
     sweeps (SPAXX**, FDRXX**), overlays live quotes, tests 365-day LTCG hurdles,
     and emits exact STRONG HOLD / ADD, HOLD FOR TAX DEFERRAL, or SELL (MOC) orders.
================================================================================
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent

from munger_engine import (
    check_data_freshness,
    compute_vectorized_munger_scores,
    fetch_live_insider_activity,
    load_or_generate_dataset,
    parse_fidelity_positions_csv,
    refresh_live_intraday_prices,
    sanitize_universe_fundamentals,
)


def run_strike_zone_scan(universe: str = "sp900") -> pd.DataFrame:
    print("=" * 96)
    print(" MUNGER FACTOR SYSTEM: LIVE STRIKE-ZONE SCANNER & TAX-LOT RECONCILER (v2.3.0)")
    print("=" * 96)

    # 1. Load Dataset (full master cache)
    close_df_full, gspc_close, fund_dict, eps_dict, is_synthetic = load_or_generate_dataset("data")

    # 2. Staleness Circuit Breaker (run on full dataset BEFORE universe slicing to preserve all columns)
    is_stale, lag_hours, latest_dt = check_data_freshness(close_df_full)
    if is_stale:
        print(f"\n[CIRCUIT BREAKER ALERT] Price cache is stale ({lag_hours:.1f} hours lag since {latest_dt.strftime('%Y-%m-%d %H:%M')}).")
        close_df_full, gspc_close = refresh_live_intraday_prices(
            close_df=close_df_full,
            gspc_close=gspc_close,
            save_path="data/historical_prices_1970_2026.pkl.xz",
        )
    else:
        print(f"[CIRCUIT BREAKER] Price cache is fresh ({lag_hours:.1f}h lag; latest bar: {latest_dt.strftime('%Y-%m-%d')}).")

    close_df = close_df_full

    # Universe Selection
    univ_choice = str(universe).lower().strip()
    if univ_choice in ["sp900", "s&p900", "s&p 900"]:
        sp900_file = ROOT_DIR / "data" / "sp900_tickers.json"
        if sp900_file.exists():
            import json
            with open(sp900_file, "r", encoding="utf-8") as f:
                sp900_set = set(json.load(f))
            valid_cols = [c for c in close_df.columns if c in sp900_set]
            if valid_cols:
                close_df = close_df[valid_cols]
                print(f"[UNIVERSE] Active Universe: S&P 900 ({len(close_df.columns)} verified constituents)")
    elif univ_choice in ["sp500", "s&p500", "s&p 500"]:
        sp500_file = ROOT_DIR / "data" / "sp500_tickers.json"
        if sp500_file.exists():
            import json
            with open(sp500_file, "r", encoding="utf-8") as f:
                sp500_set = set(json.load(f))
            valid_cols = [c for c in close_df.columns if c in sp500_set]
            if valid_cols:
                close_df = close_df[valid_cols]
                print(f"[UNIVERSE] Active Universe: S&P 500 ({len(close_df.columns)} verified constituents)")
    elif univ_choice in ["composite", "combined"]:
        comp_file = ROOT_DIR / "data" / "composite_176_tickers.json"
        if comp_file.exists():
            import json
            with open(comp_file, "r", encoding="utf-8") as f:
                comp_set = set(json.load(f))
            valid_cols = [c for c in close_df.columns if c in comp_set]
            if valid_cols:
                close_df = close_df[valid_cols]
                print(f"[UNIVERSE] Active Universe: S&P 100 + NASDAQ 100 Composite ({len(close_df.columns)} constituents)")
    else:
        print(f"[UNIVERSE] Active Universe: All Available Equities ({len(close_df.columns)} constituents)")

    latest_dates = close_df.index[-2:]
    dt_last = latest_dates[-1]

    # 3. Raw vs. Value Trap Immunized Munger Scoring
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

    scores_clean, med_clean, ey_clean = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=latest_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )

    clean_fund = sanitize_universe_fundamentals(fund_dict, eps_dict)

    # Cross-sectional medians across all 4 Munger pillars (aligned with Web Control Center)
    growth_s = pd.Series({t: float(clean_fund.get(t, {}).get("sales5y", 0.0)) for t in close_df.columns})
    roic_s = pd.Series({t: float(clean_fund.get(t, {}).get("roic_proxy", 0.0)) * 100.0 for t in close_df.columns})
    pe_s = pd.Series({t: float(clean_fund.get(t, {}).get("pe", 25.0)) for t in close_df.columns})
    nd_ebitda_s = pd.Series({t: float(clean_fund.get(t, {}).get("net_debt_to_ebitda", 1.2)) for t in close_df.columns})

    med_g = float(growth_s.median()) if not growth_s.empty else 10.0
    med_r = float(roic_s.median()) if not roic_s.empty else 15.0
    med_y = float(pe_s.median()) if not pe_s.empty else 25.0
    med_nd = float(nd_ebitda_s.median()) if not nd_ebitda_s.empty else 1.5

    # 4. Identify Candidates for SEC Form 4 Insider Analysis
    # Pick top 15 clean candidates + any high raw score constituents blocked as cyclical traps
    clean_series = scores_clean.loc[dt_last].sort_values(ascending=False)
    top_candidates = list(clean_series.head(15).index)

    raw_series = scores_raw.loc[dt_last].sort_values(ascending=False)
    cyclical_traps_flagged = [
        t for t in raw_series.index
        if float(raw_series.get(t, 0.0)) >= 2.0 and float(clean_series.get(t, 0.0)) == 0.0
    ][:5]

    insider_query_tickers = list(dict.fromkeys(top_candidates + cyclical_traps_flagged))

    print(f"\n[SEC FORM 4] Querying live insider conviction for {len(insider_query_tickers)} active candidates...")
    insider_data = fetch_live_insider_activity(insider_query_tickers)

    rows = []
    conviction_scores: Dict[str, float] = {}
    passed_med_dict: Dict[str, bool] = {}

    for t in close_df.columns:
        raw_m = float(scores_raw.loc[dt_last, t]) if pd.notna(scores_raw.loc[dt_last, t]) else 0.0
        clean_m = float(scores_clean.loc[dt_last, t]) if pd.notna(scores_clean.loc[dt_last, t]) else 0.0

        clean_s5y = round(float(clean_fund.get(t, {}).get("sales5y", 0.0)), 1)
        roic = round(float(clean_fund.get(t, {}).get("roic_proxy", 0.0)) * 100.0, 1)
        pe = round(float(clean_fund.get(t, {}).get("pe", 25.0)), 1)
        pb = round(float(clean_fund.get(t, {}).get("pb", 0.0)), 2)
        nd_ebitda = round(float(clean_fund.get(t, {}).get("net_debt_to_ebitda", 1.2)), 2)

        passed_med = bool(
            clean_m > 0.0
            and clean_s5y >= med_g
            and roic >= med_r
            and (0.0 < pe <= med_y)
            and (nd_ebitda <= med_nd and nd_ebitda <= 2.5)
        )
        passed_med_dict[t] = passed_med

        insider_info = insider_data.get(t, {"multiplier": 1.0, "signal": "NEUTRAL", "notes": ""})
        insider_mult = float(insider_info.get("multiplier", 1.0))
        conviction_m = round(clean_m * insider_mult, 2)
        conviction_scores[t] = conviction_m

        if clean_m >= 2.0 and passed_med:
            if insider_mult > 1.0:
                status = f"STRIKE ZONE (CLUSTER BUY {insider_mult:.2f}x)"
            elif insider_mult < 1.0:
                status = f"STRIKE ZONE (C-SUITE HAIRCUT {insider_mult:.2f}x)"
            else:
                status = "STRIKE ZONE"
        elif raw_m >= 2.0 and clean_m == 0.0:
            status = "CYCLICAL TRAP BLOCKED"
        else:
            status = "Watchlist"

        rows.append({
            "Ticker": t,
            "Spot ($)": round(float(close_df.loc[dt_last, t]), 2),
            "Sales5Y_%": clean_s5y,
            "ROIC_%": roic,
            "P/E": pe,
            "ND/EBITDA": nd_ebitda,
            "P/B": pb,
            "Raw_M": round(raw_m, 2),
            "Trap_Filtered_M": round(clean_m, 2),
            "Insider_Mult": insider_mult,
            "Conviction_M": conviction_m,
            "All_Med": passed_med,
            "Audit_Status": status,
        })

    df = pd.DataFrame(rows).sort_values(
        by=["All_Med", "Conviction_M", "Trap_Filtered_M", "Raw_M"],
        ascending=[False, False, False, False],
    )

    print(f"\nEvaluation Date: {dt_last.strftime('%Y-%m-%d')} | Top 15 Candidates by Conviction Munger Score:")
    print(df.head(15).to_string(index=False))

    # Display Blocked Cyclical Traps
    traps = df[df["Audit_Status"] == "CYCLICAL TRAP BLOCKED"]
    if not traps.empty:
        print("\n" + "=" * 96)
        print("[VALUE TRAP FILTER ALERT] Blocked Boom-Bust Cyclical / Outlier Traps:")
        print("=" * 96)
        print(traps.to_string(index=False))

    # Display Form 4 Breakdown for flagged names
    print("\n" + "=" * 96)
    print("[SEC FORM 4 INSIDER CONVICTION ENGINE: SIGNAL AUDIT]")
    print("=" * 96)
    flagged_insiders = [t for t in insider_query_tickers if insider_data.get(t, {}).get("multiplier", 1.0) != 1.0]
    if flagged_insiders:
        for t in flagged_insiders:
            info = insider_data[t]
            print(f" * {t:5s} | Signal: {info['signal']:28s} | Multiplier: {info['multiplier']:.2f}x | {info['notes']}")
    else:
        print(" * All screened candidates exhibited balanced open-market insider profiles (neutral multiplier: 1.00x).")

    # 5. Fidelity Tax-Lot Reconciler
    print("\n" + "=" * 96)
    print(" FIDELITY TAX-LOT RECONCILER: EXECUTION DIRECTIVES & LOT STATUS")
    print("=" * 96)

    live_prices_s = close_df_full.loc[dt_last]
    fidelity_df = parse_fidelity_positions_csv(
        live_prices=live_prices_s,
        eval_date=dt_last,
        munger_scores=conviction_scores,
        immunized_flags=passed_med_dict,
    )

    if not fidelity_df.empty:
        cols_to_print = [
            "Account", "Symbol", "Shares", "Cost_Basis", "Live_Price", "Live_Value",
            "Unrealized_Gain", "Days_Held", "LTCG_Status", "Action", "Rationale"
        ]
        cols_to_print = [c for c in cols_to_print if c in fidelity_df.columns]
        print(fidelity_df[cols_to_print].to_string(index=False))

        total_val = fidelity_df["Live_Value"].sum()
        total_basis = fidelity_df["Cost_Basis"].sum()
        total_gain = total_val - total_basis
        actions_summary = fidelity_df["Action"].value_counts().to_dict()

        print("\n[PORTFOLIO SUMMARY]")
        print(f" Total Live Securities Value: ${total_val:,.2f}")
        print(f" Total Cost Basis:           ${total_basis:,.2f}")
        gain_pct = (total_gain / total_basis * 100) if total_basis > 0 else 0.0
        print(f" Total Unrealized Gain/Loss: ${total_gain:,.2f} ({gain_pct:.2f}%)")
        print(" Execution Directives Breakdown:")
        for act, cnt in actions_summary.items():
            print(f"   - {act}: {cnt} lots")
    else:
        print("[FIDELITY RECONCILER] No 'Portfolio_Positions_*.csv' found in workspace root or data/.")
        print("To reconcile personal tax lots, export your Fidelity positions CSV and drop it into:")
        print(f"  {os.path.join(str(ROOT_DIR), 'data', 'Portfolio_Positions_*.csv')}")

    print("\n" + "=" * 96)
    print(" SCAN & AUDIT COMPLETE")
    print("=" * 96)
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Live Munger Factor Strike-Zone Scanner (v2.3.0)")
    parser.add_argument(
        "--universe", "-u",
        choices=["sp900", "sp500", "composite", "all"],
        default="sp900",
        help="Target constituent universe (default: sp900 [903 equities], sp500 [503 equities], composite [176 equities], all [927 equities])"
    )
    args = parser.parse_args()
    run_strike_zone_scan(universe=args.universe)
