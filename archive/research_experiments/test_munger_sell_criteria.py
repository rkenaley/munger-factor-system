#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR STRATEGY: ABLATION STUDY - SELL DISCIPLINE vs MECHANICAL HOLDS
================================================================================
Refactored to:
  1. Replace O(N) `list(common_dates).index(dt)` inside nested loops with
     pre-indexed 2D NumPy matrices and O(1) index lookups
  2. Directly compare fixed mechanical holding horizons (1Y, 2Y, 3Y) against
     dynamic quality-decay Munger exit rules and pure buy-and-hold
  3. Apply historical LTCG tax brackets to quantify real turnover tax drag
================================================================================
"""

from __future__ import annotations

from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from munger_engine import (
    BacktestConfig,
    compute_vectorized_munger_scores,
    load_or_generate_dataset,
    run_portfolio_simulation,
)


def main() -> None:
    print("=" * 88)
    print(" ABLATION STUDY: DYNAMIC MUNGER SELL CRITERIA VS. MECHANICAL HOLDING PERIODS")
    print("=" * 88)

    close_df, gspc_close, fund_dict, eps_dict, _ = load_or_generate_dataset("data")
    eval_dates = close_df.index[::21]

    # Pre-compute vectorized factor matrix ONCE outside the variant loop
    scores_df, above_med_df, _ = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
        formula="four_factor_z",
    )

    # Tuple format: (label, min_hold_days, max_hold_days, m_sell_threshold, require_above_median)
    variants = [
        ("1-Year Mechanical Rebalance (Force Exit @ 365d)", 365, 365, None, False),
        ("2-Year Mechanical Rebalance (Force Exit @ 730d)", 365, 730, None, False),
        ("3-Year Mechanical Rebalance (Force Exit @ 1095d)", 365, 1095, None, False),
        ("Dynamic Munger Strict (>365d, Exit if Any Factor < Median)", 365, None, None, True),
        ("Dynamic Munger Patient (>365d, Exit Only if Score M < 1.35)", 365, None, 1.35, False),
        ("Pure Buy-and-Hold Quality (Never Sell Except Delisting)", 365 * 100, None, None, False),
    ]

    rows = []
    for label, min_days, max_days, m_sell, req_med in variants:
        cfg = BacktestConfig(
            max_pos_pct=0.15,
            min_hold_days=min_days,
            max_hold_days=max_days,
            m_buy_threshold=2.0,
            m_sell_threshold=m_sell,
            require_above_median=req_med,
            slippage_bps=5.0,
            use_historical_tax_regimes=True,
        )
        res = run_portfolio_simulation(close_df, gspc_close, eval_dates, scores_df, above_med_df, cfg)
        pre_cagr = res["metrics_pre"]["cagr"] * 100.0
        post_cagr = res["metrics_post"]["cagr"] * 100.0
        rows.append({
            "Sell Criteria Variant": label,
            "Pre-Tax CAGR": f"{pre_cagr:.2f}%",
            "Post-Tax CAGR": f"{post_cagr:.2f}%",
            "Tax Drag (bps)": f"{(pre_cagr - post_cagr)*100:.0f}",
            "Post-Tax Sharpe": f"{res['metrics_post']['sharpe']:.2f}",
            "Max Drawdown": f"{res['metrics_post']['max_drawdown']*100:.2f}%",
            "Total Trades": res["trade_count"],
        })

    df_res = pd.DataFrame(rows)
    print("\nSell Criteria Ablation Results (Vectorized O(1) Lookup Engine):")
    print(df_res.to_string(index=False))

    fig_dir = Path("docs/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = [r["Sell Criteria Variant"].split("(")[0].strip() for r in rows]
    pre_vals = [float(r["Pre-Tax CAGR"].rstrip("%")) for r in rows]
    post_vals = [float(r["Post-Tax CAGR"].rstrip("%")) for r in rows]
    x = range(len(labels))
    ax.bar([i - 0.18 for i in x], pre_vals, width=0.35, label="Pre-Tax CAGR (%)")
    ax.bar([i + 0.18 for i in x], post_vals, width=0.35, label="Post-Tax CAGR (%)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Annualized CAGR (%)")
    ax.set_title("Munger Sell Criteria Optimization: Pre-Tax vs. Post-Tax Compounding")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "munger_sell_criteria_optimization.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
