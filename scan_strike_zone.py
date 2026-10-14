#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR SYSTEM: LIVE STRIKE-ZONE SCANNER & TAX-LOT RECONCILER (v2.2)
================================================================================
Production Strike-Zone Screener integrating:
  1. Staleness Circuit Breaker: Automatically detects data lag > 18h against
     the market clock and fetches live intraday prices via multi-threaded yfinance.
  2. Admiral Ackbar Cyclical-Trap Immunization: Cross-sectional winsorization,
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

from pathlib import Path
from typing import Dict, List
import pandas as pd

from munger_engine import (
    check_data_freshness,
    compute_vectorized_munger_scores,
    fetch_live_insider_activity,
    load_or_generate_dataset,
    parse_fidelity_positions_csv,
    refresh_live_intraday_prices,
    sanitize_universe_fundamentals,
)


def run_strike_zone_scan() -> pd.DataFrame:
    print("=" * 96)
    print(" MUNGER FACTOR SYSTEM: LIVE STRIKE-ZONE SCANNER & TAX-LOT RECONCILER (v2.2)")
    print("=" * 96)

    # 1. Load Dataset
    close_df, gspc_close, fund_dict, eps_dict, is_synthetic = load_or_generate_dataset("data")

    # 2. Staleness Circuit Breaker
    is_stale, lag_hours, latest_dt = check_data_freshness(close_df)
    if is_stale:
        print(f"\n[CIRCUIT BREAKER ALERT] Price cache is stale ({lag_hours:.1f} hours lag since {latest_dt.strftime('%Y-%m-%d %H:%M')}).")
        close_df, gspc_close = refresh_live_intraday_prices(
            close_df=close_df,
            gspc_close=gspc_close,
            save_path="data/historical_prices_1970_2026.pkl.xz",
        )
    else:
        print(f"[CIRCUIT BREAKER] Price cache is fresh ({lag_hours:.1f}h lag; latest bar: {latest_dt.strftime('%Y-%m-%d')}).")

    latest_dates = close_df.index[-2:]
    dt_last = latest_dates[-1]

    # 3. Raw vs. Admiral Ackbar Immunized Munger Scoring
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

    # 4. Identify Candidates for SEC Form 4 Insider Analysis
    # Pick top 15 clean candidates + any high raw score constituents blocked as cyclical traps
    clean_series = scores_clean.loc[dt_last].sort_values(ascending=False)
    top_candidates = list(clean_series.head(15).index)

    raw_series = scores_raw.loc[dt_last]
    cyclical_traps_flagged = [
        t for t in close_df.columns
        if float(raw_series.get(t, 0.0)) >= 2.0 and float(clean_series.get(t, 0.0)) == 0.0
    ]

    insider_query_tickers = list(dict.fromkeys(top_candidates + cyclical_traps_flagged))

    print(f"\n[SEC FORM 4] Querying live insider conviction for {len(insider_query_tickers)} active candidates...")
    insider_data = fetch_live_insider_activity(insider_query_tickers)

    rows = []
    conviction_scores: Dict[str, float] = {}
    passed_med_dict: Dict[str, bool] = {}

    for t in close_df.columns:
        raw_m = float(scores_raw.loc[dt_last, t]) if pd.notna(scores_raw.loc[dt_last, t]) else 0.0
        clean_m = float(scores_clean.loc[dt_last, t]) if pd.notna(scores_clean.loc[dt_last, t]) else 0.0
        passed_med = bool(med_clean.loc[dt_last, t])
        passed_med_dict[t] = passed_med

        insider_info = insider_data.get(t, {"multiplier": 1.0, "signal": "NEUTRAL", "notes": ""})
        insider_mult = float(insider_info.get("multiplier", 1.0))
        conviction_m = round(clean_m * insider_mult, 2)
        conviction_scores[t] = conviction_m

        clean_s5y = float(clean_fund.get(t, {}).get("sales5y", 0.0))
        roic = float(fund_dict.get(t, {}).get("roic_proxy", 0.0)) * 100.0
        pb = float(fund_dict.get(t, {}).get("pb", 0.0))

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
            "Sales5Y_%": round(clean_s5y, 1),
            "ROIC_%": round(roic, 1),
            "P/B": round(pb, 2),
            "Raw_M": round(raw_m, 2),
            "Ackbar_M": round(clean_m, 2),
            "Insider_Mult": insider_mult,
            "Conviction_M": conviction_m,
            "All_Med": passed_med,
            "Audit_Status": status,
        })

    df = pd.DataFrame(rows).sort_values(by=["Conviction_M", "Ackbar_M", "Raw_M"], ascending=False)

    print(f"\nEvaluation Date: {dt_last.strftime('%Y-%m-%d')} | Top 15 Candidates by Conviction Munger Score:")
    print(df.head(15).to_string(index=False))

    # Display Blocked Cyclical Traps
    traps = df[df["Audit_Status"] == "CYCLICAL TRAP BLOCKED"]
    if not traps.empty:
        print("\n" + "=" * 96)
        print("[ADMIRAL ACKBAR ALERT] Blocked Boom-Bust Cyclical / Outlier Traps:")
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

    live_prices_s = close_df.loc[dt_last]
    fidelity_df = parse_fidelity_positions_csv(
        live_prices=live_prices_s,
        eval_date=dt_last,
        munger_scores=conviction_scores,
        immunized_flags=passed_med_dict,
    )

    if not fidelity_df.empty:
        cols_to_print = [
            "Symbol", "Shares", "Cost_Basis", "Live_Price", "Live_Value",
            "Unrealized_Gain", "Days_Held", "LTCG_Status", "Action", "Rationale"
        ]
        print(fidelity_df[cols_to_print].to_string(index=False))

        total_val = fidelity_df["Live_Value"].sum()
        total_basis = fidelity_df["Cost_Basis"].sum()
        total_gain = total_val - total_basis
        actions_summary = fidelity_df["Action"].value_counts().to_dict()

        print("\n[PORTFOLIO SUMMARY]")
        print(f" Total Live Securities Value: ${total_val:,.2f}")
        print(f" Total Cost Basis:           ${total_basis:,.2f}")
        print(f" Total Unrealized Gain/Loss: ${total_gain:,.2f} ({(total_gain / total_basis * 100):.2f}%)")
        print(" Execution Directives Breakdown:")
        for act, cnt in actions_summary.items():
            print(f"   - {act}: {cnt} lots")
    else:
        print("[FIDELITY RECONCILER] No 'Portfolio_Positions_*.csv' found in workspace root or data/.")
        print("To reconcile personal tax lots, export your Fidelity positions CSV and drop it into:")
        print("  c:\\AGProj\\Tabs\\fidelity_control_center\\data\\Portfolio_Positions_*.csv")

    print("\n" + "=" * 96)
    print(" SCAN & AUDIT COMPLETE")
    print("=" * 96)
    return df


if __name__ == "__main__":
    run_strike_zone_scan()
