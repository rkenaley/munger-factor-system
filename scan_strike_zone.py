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
    classify_candidate_audit_status,
    compute_cycle_and_trend_diagnostics,
    compute_ivr_from_prices,
    compute_two_tier_sma_status,
    compute_vectorized_munger_scores,
    extract_ticker_cycle_snapshot,
    fetch_live_insider_activity,
    fetch_schedule_13d_catalysts,
    load_coi_excluded_tickers,
    load_or_generate_dataset,
    parse_fidelity_positions_csv,
    refresh_live_intraday_prices,
    sanitize_universe_fundamentals,
)


def run_strike_zone_scan(
    universe: str = "sp900",
    include_coi: bool = False,
    exclude_10b5_1: bool = True,
) -> pd.DataFrame:
    print("=" * 96)
    print(" MUNGER FACTOR SYSTEM: LIVE STRIKE-ZONE SCANNER & TAX-LOT RECONCILER (v2.4.0)")
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

    # 3. Raw vs. Value Trap & Anti-Crest Immunized Munger Scoring
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
        apply_anti_crest_guard=False,
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
        apply_anti_crest_guard=True,
    )

    cycle_diag = compute_cycle_and_trend_diagnostics(
        close_df=close_df,
        eval_dates=latest_dates,
        eps_dict=eps_dict,
        reporting_lag_days=45,
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

    coi_excluded = set() if include_coi else load_coi_excluded_tickers(ROOT_DIR / "data")
    active_coi_hits = sorted(t for t in close_df.columns if t in coi_excluded)
    if active_coi_hits:
        print(f"[COI FILTER] Excluded {len(active_coi_hits)} restricted Biotech/Pharma tickers ({', '.join(active_coi_hits[:8])}{'...' if len(active_coi_hits) > 8 else ''}).")

    # 4. Identify Candidates for SEC Form 4 Insider Analysis
    # Pick top conjunctive clean Strike-Zone candidates + overall top clean candidates + blocked traps
    clean_series = scores_clean.loc[dt_last].sort_values(ascending=False)
    med_series = scores_clean.loc[dt_last][med_clean.loc[dt_last]].sort_values(ascending=False)
    med_filtered = [t for t in med_series.index if t not in coi_excluded]
    clean_filtered = [t for t in clean_series.index if t not in coi_excluded]
    top_candidates = list(dict.fromkeys(med_filtered[:15] + clean_filtered[:10]))

    raw_series = scores_raw.loc[dt_last].sort_values(ascending=False)
    cyclical_traps_flagged = [
        t for t in raw_series.index
        if t not in coi_excluded and float(raw_series.get(t, 0.0)) >= 2.0 and float(clean_series.get(t, 0.0)) == 0.0
    ][:5]

    insider_query_tickers = list(dict.fromkeys(top_candidates + cyclical_traps_flagged))

    print(f"\n[SEC FORM 4] Querying live insider conviction for {len(insider_query_tickers)} active candidates (Rule 10b5-1 filter: {'ON' if exclude_10b5_1 else 'OFF'})...")
    insider_data = fetch_live_insider_activity(insider_query_tickers, exclude_10b5_1=exclude_10b5_1)

    active_universe_set = {t for t in close_df.columns if t not in coi_excluded}
    catalysts_13d = fetch_schedule_13d_catalysts(universe_tickers=active_universe_set, lookback_days=30)

    rows = []
    near_miss_rows = []
    conviction_scores: Dict[str, float] = {}
    passed_med_dict: Dict[str, bool] = {}

    for t in close_df.columns:
        if t in coi_excluded:
            continue
        raw_m = float(scores_raw.loc[dt_last, t]) if pd.notna(scores_raw.loc[dt_last, t]) else 0.0
        clean_m = float(scores_clean.loc[dt_last, t]) if pd.notna(scores_clean.loc[dt_last, t]) else 0.0

        clean_s5y = round(float(clean_fund.get(t, {}).get("sales5y", 0.0)), 1)
        roic = round(float(clean_fund.get(t, {}).get("roic_proxy", 0.0)) * 100.0, 1)
        pe = round(float(clean_fund.get(t, {}).get("pe", 25.0)), 1)
        pb = round(float(clean_fund.get(t, {}).get("pb", 0.0)), 2)
        nd_ebitda = round(float(clean_fund.get(t, {}).get("net_debt_to_ebitda", 1.2)), 2)

        cyc = extract_ticker_cycle_snapshot(cycle_diag, dt_last, t)
        eng_med = bool(med_clean.loc[dt_last, t]) if t in med_clean.columns else False

        pass_g = bool(clean_s5y >= med_g)
        pass_r = bool(roic >= med_r)
        pass_y = bool(0.0 < pe <= med_y)
        pass_s = bool(nd_ebitda <= med_nd and nd_ebitda <= 2.5)
        passed_count = int(pass_g) + int(pass_r) + int(pass_y) + int(pass_s)
        failed_pillars = [
            lbl for lbl, ok in [("Growth", pass_g), ("ROIC", pass_r), ("P/E", pass_y), ("Solvency", pass_s)] if not ok
        ]

        passed_med = bool(eng_med and clean_m > 0.0 and pass_g and pass_r and pass_y and pass_s)
        passed_med_dict[t] = passed_med

        insider_info = insider_data.get(t, {"multiplier": 1.0, "signal": "NEUTRAL", "notes": ""})
        insider_mult = float(insider_info.get("multiplier", 1.0))
        conviction_m = round(clean_m * insider_mult, 2)
        conviction_scores[t] = conviction_m

        is_strike_zone = bool(clean_m >= 2.0 and passed_med)
        status, is_trap = classify_candidate_audit_status(
            raw_m=raw_m,
            clean_m=clean_m,
            is_strike_zone=is_strike_zone,
            insider_mult=insider_mult,
            cyc=cyc,
        )

        gate = compute_two_tier_sma_status(close_df[t])
        ivr_info = compute_ivr_from_prices(close_df[t])
        ivr_val = ivr_info.get("ivr_1y")
        act_13d = catalysts_13d.get(t, [])
        tag_13d = f"13D({len(act_13d)})" if act_13d else "--"

        row_dict = {
            "Ticker": t,
            "Spot ($)": round(float(close_df.loc[dt_last, t]), 2),
            "Sales5Y_%": clean_s5y,
            "ROIC_%": roic,
            "P/E": pe,
            "ND/EBITDA": nd_ebitda,
            "Raw_M": round(raw_m, 2),
            "Crest_Mult": cyc["anti_crest_mult"],
            "Trap_Filtered_M": round(clean_m, 2),
            "Insider_Mult": insider_mult,
            "Conviction_M": conviction_m,
            "Gate_Tier": gate["gating_tier"],
            "vs100W_%": gate["dist_100w_pct"],
            "vs200W_%": gate["dist_200w_pct"],
            "IVR_%": round(ivr_val, 1) if ivr_val is not None else None,
            "13D": tag_13d,
            "Mom12_1_%": cyc["mom_12_1_pct"],
            "DD52W_%": cyc["dd_52w_pct"],
            "vs200D_%": cyc["vs_sma200_pct"],
            "TTM_5YMd": cyc["windfall_ratio"],
            "Q_YoY_%": cyc["q_eps_yoy_pct"],
            "All_Med": passed_med,
            "Cycle_Flags": cyc["cycle_label"],
            "Audit_Status": status,
        }
        rows.append(row_dict)

        if passed_count == 3 and not is_trap and clean_m > 0.0:
            near_miss_rows.append({
                "Ticker": t,
                "Spot ($)": row_dict["Spot ($)"],
                "Missed_Pillar": ",".join(failed_pillars),
                "Sales5Y_%": clean_s5y,
                "ROIC_%": roic,
                "P/E": pe,
                "ND/EBITDA": nd_ebitda,
                "Clean_M": round(clean_m, 2),
                "Conviction_M": conviction_m,
                "Gate_Tier": gate["gating_tier"],
                "vs100W_%": gate["dist_100w_pct"],
                "vs200W_%": gate["dist_200w_pct"],
                "IVR_%": row_dict["IVR_%"],
                "13D": tag_13d,
                "_pe_miss_rank": 0 if failed_pillars == ["P/E"] else 1,
            })

    df = pd.DataFrame(rows).sort_values(
        by=["All_Med", "Conviction_M", "Trap_Filtered_M", "Raw_M"],
        ascending=[False, False, False, False],
    )

    print(f"\nEvaluation Date: {dt_last.strftime('%Y-%m-%d')} | Top 15 Candidates by Conviction Munger Score:")
    print(df.head(15).to_string(index=False))

    # Display 3/4 Near-Miss Pullback Watchlist
    if near_miss_rows:
        nm_df = pd.DataFrame(near_miss_rows).sort_values(
            by=["_pe_miss_rank", "Conviction_M", "Clean_M"],
            ascending=[True, False, False],
        ).drop(columns=["_pe_miss_rank"])
        print("\n" + "=" * 96)
        print("[3/4 NEAR-MISS PULLBACK WATCHLIST — 1 PILLAR AWAY] (Valuation Misses Prioritized)")
        print("=" * 96)
        print(nm_df.head(15).to_string(index=False))

    # Display Blocked Cyclical & Crest/EPS Traps
    traps = df[df["Audit_Status"].isin(["CYCLICAL TRAP BLOCKED", "CREST/EPS TRAP BLOCKED"])]
    if not traps.empty:
        print("\n" + "=" * 96)
        print("[VALUE TRAP & CREST GUARD ALERT] Blocked Cyclical / Broken-EPS Downtrend Traps:")
        print("=" * 96)
        print(traps.to_string(index=False))

    # Display Schedule 13D Activist Catalysts matched inside active universe
    if catalysts_13d:
        print("\n" + "=" * 96)
        print("[SEC SCHEDULE 13D ACTIVIST CATALYSTS IN ACTIVE UNIVERSE (30D)]")
        print("=" * 96)
        for tk in sorted(catalysts_13d.keys()):
            for cat in catalysts_13d[tk][:3]:
                print(f" * {tk:5s} | Filed: {cat['file_date']} | {cat['form']} | Filer: {cat['filer'][:48]}")

    # Display Form 4 Breakdown for flagged names
    print("\n" + "=" * 96)
    print(f"[SEC FORM 4 INSIDER CONVICTION ENGINE: SIGNAL AUDIT] (10b5-1 Exclusion: {'ON' if exclude_10b5_1 else 'OFF'})")
    print("=" * 96)
    flagged_insiders = [
        t for t in insider_query_tickers
        if insider_data.get(t, {}).get("multiplier", 1.0) != 1.0
        or insider_data.get(t, {}).get("planned_10b5_csuite_sales_usd", 0.0) > 0.0
        or insider_data.get(t, {}).get("csuite_buy_hit", False)
        or insider_data.get(t, {}).get("fresh_buy_window") in ("FRESH_5D", "RECENT_30D")
    ]
    if flagged_insiders:
        for t in flagged_insiders:
            info = insider_data[t]
            fresh_lbl = f" [{info.get('fresh_buy_window', 'NONE')}]" if info.get("fresh_buy_window", "NONE") != "NONE" else ""
            print(f" * {t:5s} | Signal: {info['signal']:28s} | Mult: {info['multiplier']:.2f}x{fresh_lbl} | {info['notes']}")
    else:
        print(" * All screened candidates exhibited balanced discretionary open-market insider profiles (1.00x).")

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
    parser.add_argument(
        "--include-coi",
        action="store_true",
        help="Include restricted physician conflict-of-interest Biotech/Pharma tickers in screener output"
    )
    parser.add_argument(
        "--include-10b5-1",
        action="store_true",
        help="Include Rule 10b5-1 scheduled insider trading plans instead of isolating discretionary trades"
    )
    args = parser.parse_args()
    run_strike_zone_scan(
        universe=args.universe,
        include_coi=args.include_coi,
        exclude_10b5_1=not args.include_10b5_1,
    )
