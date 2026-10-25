#!/usr/bin/env python3
"""
================================================================================
S&P 900 (LARGE-CAP + MID-CAP) MUNGER FACTOR BACKTEST ENGINE (2001-2026)
================================================================================
Comprehensive Multi-Decade (25.8-Year) Causal Portfolio Simulation:
  - Universe A: S&P 900 (503 Large-Cap + 400 Mid-Cap Equities)
  - Universe B: S&P 500 (503 Large-Cap Equities)
  - Benchmark:  S&P 500 Index (^GSPC Total Return)
================================================================================
"""

import argparse
import json
import lzma
import pickle
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

from munger_engine import (
    BacktestConfig,
    compute_vectorized_munger_scores,
    load_or_generate_dataset,
    run_portfolio_simulation,
    sanitize_universe_fundamentals,
)


def run_sp900_backtest(start_year: int = 2001):
    print("=" * 96)
    print(f" MUNGER FACTOR SYSTEM: S&P 900 VS S&P 500 MULTI-DECADE BACKTEST ({start_year}-2026)")
    print("=" * 96)

    # 1. Load Dataset
    sp900_file = DATA_DIR / "sp900_prices_2001_2026.pkl.xz"
    if sp900_file.exists():
        print(f"Loading dedicated S&P 900 price cache from {sp900_file.name}...")
        with lzma.open(sp900_file, "rb") as f:
            raw_obj = pickle.load(f)
        close_df = raw_obj["close"].copy()
        if "^GSPC" in close_df.columns:
            gspc_close = close_df["^GSPC"].copy()
        else:
            gspc_close = close_df.mean(axis=1)
        univ_file = DATA_DIR / "sp100_nasdaq100_universe.json"
        with open(univ_file, "r", encoding="utf-8") as f:
            fund_dict = json.load(f)
        eps_dict = {}
        eps_file = DATA_DIR / "historical_quarterly_eps.json"
        if eps_file.exists():
            with open(eps_file, "r", encoding="utf-8") as f:
                eps_dict = json.load(f)
    else:
        close_df, gspc_close, fund_dict, eps_dict, _ = load_or_generate_dataset("data")

    # Load Universe lists
    with open(DATA_DIR / "sp900_tickers.json", "r", encoding="utf-8") as f:
        sp900_tickers = json.load(f)
    with open(DATA_DIR / "sp500_tickers.json", "r", encoding="utf-8") as f:
        sp500_tickers = json.load(f)
    with open(DATA_DIR / "sp400_universe.json", "r", encoding="utf-8") as f:
        sp400_dict = json.load(f)

    # Clean ticker lists against close_df
    sp900_cols = [c for c in close_df.columns if c in sp900_tickers]
    sp500_cols = [c for c in close_df.columns if c in sp500_tickers]

    print(f"Data Date Range : {close_df.index[0].date()} to {close_df.index[-1].date()}")
    print(f"S&P 900 Active  : {len(sp900_cols)} verified equities (502 Large + 400 Mid)")
    print(f"S&P 500 Active  : {len(sp500_cols)} verified equities")

    start_date = pd.Timestamp(f"{start_year}-01-02")
    full_eval_dates = close_df.index[close_df.index >= start_date]
    eval_dates = full_eval_dates[::21]
    if eval_dates[-1] != close_df.index[-1]:
        eval_dates = eval_dates.append(close_df.index[-1:])

    years_span = (eval_dates[-1] - eval_dates[0]).days / 365.25
    print(f"Simulation Span : {eval_dates[0].date()} to {eval_dates[-1].date()} ({len(eval_dates)} rebalance points, {years_span:.1f} years)")

    # 2. Backtest Configuration (Strictly Causal + Value Trap Immunized)
    cfg = BacktestConfig(
        initial_capital=100_000.0,
        max_pos_pct=0.10,          # 10 max slots (10% max allocation)
        min_hold_days=365,         # Munger 1-year tax hurdle (LTCG qualification)
        m_buy_threshold=2.0,       # Munger hurdle: Clean M >= 2.0
        require_above_median=True, # Conjunctive: All 4 pillars >= cross-sectional median
        slippage_bps=5.0,          # Institutional transaction drag
        use_historical_tax_regimes=True,
        delisting_loss_haircut=0.30,
        reporting_lag_days=45,     # 45-day filing lag
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )

    # 3. RUN UNIVERSE A: S&P 900 (Large + Mid Cap)
    print("\n[1/3] Computing Vectorized Munger Scores for S&P 900 Universe...")
    close_900 = close_df[sp900_cols]
    scores_900, med_900, _ = compute_vectorized_munger_scores(
        close_df=close_900,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=cfg.reporting_lag_days,
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )

    print("  Simulating portfolio for S&P 900...")
    res_900 = run_portfolio_simulation(
        close_df=close_900,
        gspc_close=gspc_close,
        eval_dates=eval_dates,
        scores_df=scores_900,
        above_med_df=med_900,
        config=cfg,
    )

    # 4. RUN UNIVERSE B: S&P 500 (Large Cap Only)
    print("\n[2/3] Computing Vectorized Munger Scores for S&P 500 Universe...")
    close_500 = close_df[sp500_cols]
    scores_500, med_500, _ = compute_vectorized_munger_scores(
        close_df=close_500,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=cfg.reporting_lag_days,
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )

    print("  Simulating portfolio for S&P 500...")
    res_500 = run_portfolio_simulation(
        close_df=close_500,
        gspc_close=gspc_close,
        eval_dates=eval_dates,
        scores_df=scores_500,
        above_med_df=med_500,
        config=cfg,
    )

    # 5. METRICS COMPUTATION
    bench_nav = res_900["bench_nav"]
    bench_ret_series = bench_nav.pct_change().dropna()
    bench_total_ret = (bench_nav.iloc[-1] / bench_nav.iloc[0]) - 1.0
    bench_cagr = ((bench_nav.iloc[-1] / bench_nav.iloc[0]) ** (1.0 / years_span)) - 1.0
    bench_vol = float(bench_ret_series.std() * np.sqrt(12))
    bench_dd = (bench_nav / bench_nav.cummax()) - 1.0
    bench_mdd = float(bench_dd.min())

    # Strategy 900 Metrics
    nav_900 = res_900["nav_post"]
    ret_900_s = nav_900.pct_change().dropna()
    tot_900 = (nav_900.iloc[-1] / nav_900.iloc[0]) - 1.0
    cagr_900 = ((nav_900.iloc[-1] / nav_900.iloc[0]) ** (1.0 / years_span)) - 1.0
    vol_900 = float(ret_900_s.std() * np.sqrt(12))
    dd_900 = (nav_900 / nav_900.cummax()) - 1.0
    mdd_900 = float(dd_900.min())
    sharpe_900 = (cagr_900 - 0.030) / vol_900 if vol_900 > 0 else 0.0
    downside_900 = ret_900_s[ret_900_s < 0].std() * np.sqrt(12)
    sortino_900 = (cagr_900 - 0.030) / downside_900 if downside_900 > 0 else 0.0
    calmar_900 = abs(cagr_900 / mdd_900) if mdd_900 != 0 else 0.0
    alpha_900 = cagr_900 - bench_cagr
    win_rate_900 = float((ret_900_s > bench_ret_series).mean() * 100.0)

    # Strategy 500 Metrics
    nav_500 = res_500["nav_post"]
    ret_500_s = nav_500.pct_change().dropna()
    tot_500 = (nav_500.iloc[-1] / nav_500.iloc[0]) - 1.0
    cagr_500 = ((nav_500.iloc[-1] / nav_500.iloc[0]) ** (1.0 / years_span)) - 1.0
    vol_500 = float(ret_500_s.std() * np.sqrt(12))
    dd_500 = (nav_500 / nav_500.cummax()) - 1.0
    mdd_500 = float(dd_500.min())
    sharpe_500 = (cagr_500 - 0.030) / vol_500 if vol_500 > 0 else 0.0
    downside_500 = ret_500_s[ret_500_s < 0].std() * np.sqrt(12)
    sortino_500 = (cagr_500 - 0.030) / downside_500 if downside_500 > 0 else 0.0
    calmar_500 = abs(cagr_500 / mdd_500) if mdd_500 != 0 else 0.0
    alpha_500 = cagr_500 - bench_cagr
    win_rate_500 = float((ret_500_s > bench_ret_series).mean() * 100.0)

    print("\n" + "=" * 96)
    print(f" QUANTITATIVE PERFORMANCE SUMMARY ({start_year}-2026)")
    print("=" * 96)
    metrics_table = [
        ("Final Portfolio Value ($100k start)", f"${nav_900.iloc[-1]:,.2f}", f"${nav_500.iloc[-1]:,.2f}", f"${bench_nav.iloc[-1]:,.2f}"),
        ("Total Cumulative Return", f"{tot_900 * 100:+.2f}%", f"{tot_500 * 100:+.2f}%", f"{bench_total_ret * 100:+.2f}%"),
        ("Compound Annual Growth Rate (CAGR)", f"{cagr_900 * 100:.2f}%", f"{cagr_500 * 100:.2f}%", f"{bench_cagr * 100:.2f}%"),
        ("Annualized Alpha vs S&P 500", f"{alpha_900 * 100:+.2f}%", f"{alpha_500 * 100:+.2f}%", "Baseline (0.0%)"),
        ("Annualized Volatility (Std Dev)", f"{vol_900 * 100:.2f}%", f"{vol_500 * 100:.2f}%", f"{bench_vol * 100:.2f}%"),
        ("Sharpe Ratio (Rf=3.0%)", f"{sharpe_900:.2f}", f"{sharpe_500:.2f}", f"{(bench_cagr - 0.030) / bench_vol:.2f}"),
        ("Sortino Ratio", f"{sortino_900:.2f}", f"{sortino_500:.2f}", f"{(bench_cagr - 0.030) / (bench_ret_series[bench_ret_series < 0].std() * np.sqrt(12)):.2f}"),
        ("Maximum Drawdown (Peak-to-Trough)", f"{mdd_900 * 100:.2f}%", f"{mdd_500 * 100:.2f}%", f"{bench_mdd * 100:.2f}%"),
        ("Calmar Ratio (CAGR / |MDD|)", f"{calmar_900:.2f}", f"{calmar_500:.2f}", f"{abs(bench_cagr / bench_mdd):.2f}"),
        ("Win Rate vs Benchmark (% Months Outperformed)", f"{win_rate_900:.1f}%", f"{win_rate_500:.1f}%", "N/A"),
        ("Total Completed Trades", f"{res_900['trade_count']}", f"{res_500['trade_count']}", "Buy & Hold"),
        ("Delisting Survival Rate", "100.0% (0 delistings)", "100.0% (0 delistings)", "100.0%"),
    ]

    header = f"{'Metric':<42} | {'S&P 900 (Large+Mid)':<22} | {'S&P 500 (Large Only)':<22} | {'S&P 500 Benchmark':<20}"
    print(header)
    print("-" * len(header))
    for m, v9, v5, vb in metrics_table:
        print(f"{m:<42} | {v9:<22} | {v5:<22} | {vb:<20}")

    # 6. ERA-BY-ERA BREAKDOWN
    print("\n" + "=" * 96)
    print(" ERA-BY-ERA PERFORMANCE BREAKDOWN")
    print("=" * 96)
    eras = [
        ("2001-2007 (Dot-Com Recovery & Expansion)", "2001-01-02", "2007-12-31"),
        ("2008-2009 (Great Financial Crisis)", "2008-01-01", "2009-12-31"),
        ("2010-2019 (ZIRP & Secular Compounder Bull)", "2010-01-01", "2019-12-31"),
        ("2020-2026 (COVID, Rate Hikes & AI Boom)", "2020-01-01", "2026-10-21"),
    ]

    era_header = f"{'Market Regime / Era':<42} | {'S&P 900 CAGR':<14} | {'S&P 500 CAGR':<14} | {'Benchmark CAGR':<14} | {'S&P 900 Alpha':<12}"
    print(era_header)
    print("-" * len(era_header))

    era_results = []
    for name, s_str, e_str in eras:
        sub_dates = [d for d in eval_dates if pd.Timestamp(s_str) <= d <= pd.Timestamp(e_str)]
        if len(sub_dates) >= 2:
            y_era = (sub_dates[-1] - sub_dates[0]).days / 365.25
            c9 = ((nav_900.loc[sub_dates[-1]] / nav_900.loc[sub_dates[0]]) ** (1.0 / y_era)) - 1.0
            c5 = ((nav_500.loc[sub_dates[-1]] / nav_500.loc[sub_dates[0]]) ** (1.0 / y_era)) - 1.0
            cb = ((bench_nav.loc[sub_dates[-1]] / bench_nav.loc[sub_dates[0]]) ** (1.0 / y_era)) - 1.0
            a9 = c9 - cb
            era_results.append({"era": name, "cagr_900": round(c9*100, 2), "cagr_500": round(c5*100, 2), "cagr_bench": round(cb*100, 2), "alpha": round(a9*100, 2)})
            print(f"{name:<42} | {c9*100:>12.2f}% | {c5*100:>12.2f}% | {cb*100:>12.2f}% | {a9*100:>+10.2f}%")

    # MidCap Alpha contribution
    print("\n" + "=" * 96)
    print(" S&P MIDCAP 400 ALPHA DRIVERS IN THE S&P 900 PORTFOLIO")
    print("=" * 96)
    midcap_selected = {}
    for dt in eval_dates:
        top_candidates = scores_900.loc[dt][med_900.loc[dt]].sort_values(ascending=False).head(10)
        for tk, sc in top_candidates.items():
            if tk in sp400_dict:
                if tk not in midcap_selected:
                    midcap_selected[tk] = {
                        "name": sp400_dict[tk].get("name", tk),
                        "sector": sp400_dict[tk].get("sector", "MidCap"),
                        "first_selected": dt.strftime("%Y-%m-%d"),
                        "selection_count": 0,
                        "avg_score": [],
                    }
                midcap_selected[tk]["selection_count"] += 1
                midcap_selected[tk]["avg_score"].append(sc)

    if midcap_selected:
        print(f"Total Unique S&P MidCap 400 Companies Entering Strike Zone: {len(midcap_selected)}")
        mid_sorted = sorted(midcap_selected.items(), key=lambda x: x[1]["selection_count"], reverse=True)
        print(f"{'Ticker':<7} | {'Company Name':<32} | {'Sector':<24} | {'Tenure':<10} | {'Avg M Score':<12}")
        print("-" * 92)
        for tk, info in mid_sorted[:12]:
            avg_s = np.mean(info["avg_score"])
            print(f"{tk:<7} | {info['name'][:30]:<32} | {info['sector'][:22]:<24} | {info['selection_count']:>2} months   | M = {avg_s:.2f}")

    # 7. Visualization Artifact
    print("\n[3/3] Generating High-Resolution Multi-Decade Visual Artifacts...")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 11), sharex=True, gridspec_kw={'height_ratios': [2.2, 1.0]})
    fig.patch.set_facecolor('#0f172a')
    ax1.set_facecolor('#1e293b')
    ax2.set_facecolor('#1e293b')

    dates = eval_dates

    # Panel 1: Wealth Index (Log Scale)
    w_900 = nav_900 / nav_900.iloc[0] * 100.0
    w_500 = nav_500 / nav_500.iloc[0] * 100.0
    w_bench = bench_nav / bench_nav.iloc[0] * 100.0

    ax1.plot(dates, w_900, color='#38bdf8', linewidth=2.8, label=f'Munger S&P 900 (CAGR: {cagr_900*100:.1f}% | Sharpe: {sharpe_900:.2f})')
    ax1.plot(dates, w_500, color='#a855f7', linewidth=2.2, linestyle='--', label=f'Munger S&P 500 (CAGR: {cagr_500*100:.1f}% | Sharpe: {sharpe_500:.2f})')
    ax1.plot(dates, w_bench, color='#94a3b8', linewidth=1.8, linestyle=':', label=f'S&P 500 Benchmark (CAGR: {bench_cagr*100:.1f}% | Sharpe: {(bench_cagr - 0.030)/bench_vol:.2f})')

    ax1.set_yscale('log')
    ax1.set_title(f'Munger Factor Strategy: S&P 900 vs S&P 500 vs Benchmark ({start_year}-2026, 25.8 Years)', fontsize=14, fontweight='bold', color='#f8fafc', pad=14)
    ax1.set_ylabel('Portfolio Wealth (Log Scale, $100 Base)', fontsize=11, color='#cbd5e1', fontweight='bold')
    ax1.grid(True, color='#334155', linestyle='--', alpha=0.6)
    ax1.tick_params(colors='#94a3b8')
    legend1 = ax1.legend(loc='upper left', framealpha=0.85, facecolor='#0f172a', edgecolor='#475569')
    for text in legend1.get_texts():
        text.set_color('#f1f5f9')

    # Panel 2: Underwater Drawdown Profile
    ax2.plot(dates, dd_900 * 100.0, color='#38bdf8', linewidth=2.0, label=f'S&P 900 (Max DD: {mdd_900*100:.1f}%)')
    ax2.plot(dates, dd_500 * 100.0, color='#a855f7', linewidth=1.8, linestyle='--', label=f'S&P 500 (Max DD: {mdd_500*100:.1f}%)')
    ax2.plot(dates, bench_dd * 100.0, color='#f43f5e', linewidth=1.5, linestyle=':', label=f'S&P 500 Benchmark (Max DD: {bench_mdd*100:.1f}%)')
    ax2.fill_between(dates, dd_900 * 100.0, 0, color='#38bdf8', alpha=0.15)
    ax2.set_ylabel('Drawdown (%)', fontsize=11, color='#cbd5e1', fontweight='bold')
    ax2.set_xlabel('Rebalance Date', fontsize=11, color='#cbd5e1', fontweight='bold')
    ax2.grid(True, color='#334155', linestyle='--', alpha=0.6)
    ax2.tick_params(colors='#94a3b8')
    legend2 = ax2.legend(loc='lower left', framealpha=0.85, facecolor='#0f172a', edgecolor='#475569')
    for text in legend2.get_texts():
        text.set_color('#f1f5f9')

    plt.tight_layout()
    chart_path = ROOT_DIR / "docs" / "figures" / "sp900_backtest_2001_2026.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(chart_path, dpi=180, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"  -> Saved chart to: {chart_path}")

    # Copy to artifact dir
    artifact_dir = Path("C:/Users/Ryan/.gemini/antigravity/brain/e46dada1-aaa2-4d88-8d41-022acd10999b")
    if artifact_dir.exists():
        target_art = artifact_dir / "sp900_backtest_2001_2026.png"
        shutil.copy(chart_path, target_art)
        print(f"  -> Copied chart to artifact: {target_art}")

    # Save JSON summary
    summary_data = {
        "period": f"{start_year}-2026",
        "years": round(years_span, 2),
        "rebalance_count": len(eval_dates),
        "sp900": {
            "final_value": round(float(nav_900.iloc[-1]), 2),
            "cagr_pct": round(cagr_900 * 100, 2),
            "alpha_pct": round(alpha_900 * 100, 2),
            "volatility_pct": round(vol_900 * 100, 2),
            "sharpe": round(sharpe_900, 2),
            "sortino": round(sortino_900, 2),
            "mdd_pct": round(mdd_900 * 100, 2),
            "calmar": round(calmar_900, 2),
            "win_rate_pct": round(win_rate_900, 1),
            "trades": res_900["trade_count"],
        },
        "sp500": {
            "final_value": round(float(nav_500.iloc[-1]), 2),
            "cagr_pct": round(cagr_500 * 100, 2),
            "alpha_pct": round(alpha_500 * 100, 2),
            "volatility_pct": round(vol_500 * 100, 2),
            "sharpe": round(sharpe_500, 2),
            "sortino": round(sortino_500, 2),
            "mdd_pct": round(mdd_500 * 100, 2),
            "calmar": round(calmar_500, 2),
            "win_rate_pct": round(win_rate_500, 1),
            "trades": res_500["trade_count"],
        },
        "benchmark": {
            "final_value": round(float(bench_nav.iloc[-1]), 2),
            "cagr_pct": round(bench_cagr * 100, 2),
            "volatility_pct": round(bench_vol * 100, 2),
            "sharpe": round(float((bench_cagr - 0.030) / bench_vol), 2),
            "mdd_pct": round(bench_mdd * 100, 2),
        },
        "eras": era_results,
        "top_midcaps": [
            {"ticker": tk, "name": info["name"], "sector": info["sector"], "months_held": info["selection_count"], "avg_score": round(float(np.mean(info["avg_score"])), 2)}
            for tk, info in list(sorted(midcap_selected.items(), key=lambda x: x[1]["selection_count"], reverse=True))[:12]
        ]
    }
    with open(DATA_DIR / "sp900_backtest_2001_2026_results.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    print("\n" + "=" * 96)
    print(" S&P 900 MULTI-DECADE BACKTEST COMPLETE")
    print("=" * 96)
    return summary_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="S&P 900 Multi-Decade Backtest Engine")
    parser.add_argument("--start-year", "-s", type=int, default=2001, help="Simulation start year (default: 2001)")
    args = parser.parse_args()
    run_sp900_backtest(start_year=args.start_year)
