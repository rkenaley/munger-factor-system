#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR STRATEGY: SURVIVORSHIP-FREE MULTI-DECADE BACKTEST (1971-2026)
================================================================================
Refactored v2.1:
  - Uses $1,000 initial capital so Figure 1 Y-axis (Log10 NAV starting at 10^3)
    matches Whitepaper Table 6 and Figure 1 captions exactly
  - Incorporates Cross-Sectional Winsorization, 5-Year Full-Cycle Profitability
    Gating, and Cyclically Adjusted Earnings Yield ("Admiral Ackbar Filter")
  - Enforces 45-day SEC reporting lag, -5 bps per-trade slippage, and 30%
    terminal delisting haircut
================================================================================
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from munger_engine import (
    BacktestConfig,
    compute_vectorized_munger_scores,
    load_or_generate_dataset,
    run_portfolio_simulation,
)


def main() -> None:
    print("=" * 88)
    print(" MUNGER FACTOR SYSTEM: CAUSAL SURVIVORSHIP-AWARE BACKTEST (1971-2026)")
    print("=" * 88)

    close_df, gspc_close, fund_dict, eps_dict, is_synthetic = load_or_generate_dataset("data")
    mode_label = "SYNTHETIC CI FIXTURE" if is_synthetic else "REPOSITORY HISTORICAL CACHE"
    print(f"Dataset Source : {mode_label}")
    print(f"Active Universe: {len(close_df.columns)} verified constituents ({close_df.index[0].date()} -> {close_df.index[-1].date()})")

    eval_dates = close_df.index[::21]

    # 1. Strictly Causal + Cyclical-Trap Immunized Run ($1,000 start to match Whitepaper Table 6)
    causal_cfg = BacktestConfig(
        initial_capital=1_000.0,
        max_pos_pct=0.15,
        min_hold_days=365,
        m_buy_threshold=2.0,
        require_above_median=True,
        slippage_bps=5.0,
        use_historical_tax_regimes=True,
        delisting_loss_haircut=0.30,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )
    scores_causal, med_causal, _ = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=causal_cfg.reporting_lag_days,
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )
    res_causal = run_portfolio_simulation(
        close_df=close_df,
        gspc_close=gspc_close,
        eval_dates=eval_dates,
        scores_df=scores_causal,
        above_med_df=med_causal,
        config=causal_cfg,
    )

    # 2. Legacy Lookahead Run ($1,000 start for side-by-side audit comparison)
    legacy_cfg = BacktestConfig(
        initial_capital=1_000.0,
        max_pos_pct=0.15,
        min_hold_days=365,
        m_buy_threshold=2.0,
        require_above_median=True,
        slippage_bps=-14.0,
        use_historical_tax_regimes=False,
        flat_ltcg_tax_rate=0.238,
        delisting_loss_haircut=0.0,
        reporting_lag_days=0,
        allow_legacy_lookahead=True,
        winsorize_cross_section=False,
        enforce_full_cycle_profitability=False,
        use_cyclically_adjusted_ey=False,
    )
    scores_legacy, med_legacy, _ = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=0,
        allow_legacy_lookahead=True,
        winsorize_cross_section=False,
        enforce_full_cycle_profitability=False,
        use_cyclically_adjusted_ey=False,
    )
    res_legacy = run_portfolio_simulation(
        close_df=close_df,
        gspc_close=gspc_close,
        eval_dates=eval_dates,
        scores_df=scores_legacy,
        above_med_df=med_legacy,
        config=legacy_cfg,
    )

    summary_rows = [
        {
            "Regime / Model": "Audited Causal Munger (Pre-Tax, -5bps, Trap-Immunized)",
            "Start ($)": "$1,000",
            "Terminal ($)": f"${res_causal['nav_pre'].iloc[-1]:,.0f}",
            "CAGR": f"{res_causal['metrics_pre']['cagr']*100:.2f}%",
            "Sharpe": f"{res_causal['metrics_pre']['sharpe']:.2f}",
            "Max Drawdown": f"{res_causal['metrics_pre']['max_drawdown']*100:.2f}%",
            "Trades": res_causal["trade_count"],
        },
        {
            "Regime / Model": "Audited Causal Munger (Post-Tax, Historical Brackets)",
            "Start ($)": "$1,000",
            "Terminal ($)": f"${res_causal['nav_post'].iloc[-1]:,.0f}",
            "CAGR": f"{res_causal['metrics_post']['cagr']*100:.2f}%",
            "Sharpe": f"{res_causal['metrics_post']['sharpe']:.2f}",
            "Max Drawdown": f"{res_causal['metrics_post']['max_drawdown']*100:.2f}%",
            "Trades": res_causal["trade_count"],
        },
        {
            "Regime / Model": "Legacy Unaudited (2026 EPS Lookahead + 14bps Credit)",
            "Start ($)": "$1,000",
            "Terminal ($)": f"${res_legacy['nav_pre'].iloc[-1]:,.0f}",
            "CAGR": f"{res_legacy['metrics_pre']['cagr']*100:.2f}%",
            "Sharpe": f"{res_legacy['metrics_pre']['sharpe']:.2f}",
            "Max Drawdown": f"{res_legacy['metrics_pre']['max_drawdown']*100:.2f}%",
            "Trades": res_legacy["trade_count"],
        },
        {
            "Regime / Model": "S&P 500 Total Return Benchmark",
            "Start ($)": "$1,000",
            "Terminal ($)": f"${res_causal['bench_nav'].iloc[-1]:,.0f}",
            "CAGR": f"{res_causal['metrics_bench']['cagr']*100:.2f}%",
            "Sharpe": f"{res_causal['metrics_bench']['sharpe']:.2f}",
            "Max Drawdown": f"{res_causal['metrics_bench']['max_drawdown']*100:.2f}%",
            "Trades": 1,
        },
    ]

    df_summary = pd.DataFrame(summary_rows)
    print("\nMulti-Decade Audit Comparison ($1,000 Initial Capital):")
    print(df_summary.to_string(index=False))

    data_dir = Path("data")
    if data_dir.exists():
        out_json = data_dir / "survivorship_free_alpha_uplift_results.json"
        payload = {
            "dataset_mode": mode_label,
            "initial_capital": 1000.0,
            "causal_pre_tax": res_causal["metrics_pre"],
            "causal_post_tax": res_causal["metrics_post"],
            "legacy_unadjusted_pre_tax": res_legacy["metrics_pre"],
            "sp500_benchmark": res_causal["metrics_bench"],
            "lookahead_and_moc_inflation_bps": round(
                (res_legacy["metrics_pre"]["cagr"] - res_causal["metrics_pre"]["cagr"]) * 10_000, 1
            ),
        }
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    fig_dir = Path("docs/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(res_causal["nav_pre"].index, np.log10(res_causal["nav_pre"]), label="Causal Munger (Pre-Tax, $1k Start)", linewidth=2.0)
    ax.plot(res_causal["nav_post"].index, np.log10(res_causal["nav_post"]), label="Causal Munger (Post-Tax, $1k Start)", linewidth=1.8)
    ax.plot(res_legacy["nav_pre"].index, np.log10(res_legacy["nav_pre"]), label="Legacy Lookahead (+14bps Credit)", linestyle="--", alpha=0.8)
    ax.plot(res_causal["bench_nav"].index, np.log10(res_causal["bench_nav"]), label="S&P 500 Total Return ($1k Start)", color="black", alpha=0.7)
    ax.set_title("Munger Factor System: Audited Causal vs. Legacy Compounding ($1,000 Initial Capital, Log10 NAV)")
    ax.set_ylabel("Log10 Portfolio Value ($) [3.0 = $1,000]")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "munger_survivorship_free_alpha_uplift.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
