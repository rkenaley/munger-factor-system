#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR STRATEGY: IN-SAMPLE (1971-2008) & OUT-OF-SAMPLE (2009-2026)
THRESHOLD OPTIMIZATION
================================================================================
Refactored to:
  1. Pre-compute the causal [dates x tickers] Munger score matrix ONCE outside
     the `for th in thresholds:` loop (eliminating redundant triple-nested
     EPS & Z-score recalculations)
  2. Evaluate annual equal-weighted cohorts of all active constituents meeting
     `M >= th` across In-Sample (1971-2008) and Out-of-Sample (2009-2026)
================================================================================
"""

from __future__ import annotations

from pathlib import Path
import time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from munger_engine import (
    compute_performance_metrics,
    compute_vectorized_munger_scores,
    get_historical_ltcg_tax_rate,
    get_historical_rf_rate,
    load_or_generate_dataset,
)


def evaluate_annual_threshold_cohorts(
    close_df: pd.DataFrame,
    cycle_dates: pd.DatetimeIndex,
    scores_df: pd.DataFrame,
    thresholds: list[float],
    slippage_bps: float = 5.0,
) -> pd.DataFrame:
    """
    Vectorized annual cohort evaluator across `cycle_dates`:
    At each annual rebalance date `dt_s`, selects all active stocks with `score >= th`
    (or holds cash at the historical T-bill rate if 0 stocks qualify), computes
    pre-tax and post-tax annual cohort returns net of slippage, and reports metrics.
    """
    prices_cycle = close_df.reindex(cycle_dates, method="ffill").to_numpy(dtype=float)
    scores_cycle = scores_df.reindex(cycle_dates).to_numpy(dtype=float)
    round_trip_cost = 2.0 * (slippage_bps / 10_000.0)

    rows = []
    for th in thresholds:
        nav_pre = [100_000.0]
        nav_post = [100_000.0]
        wins = 0
        active_cycles = 0
        total_positions = 0

        for c_idx in range(len(cycle_dates) - 1):
            dt_s = cycle_dates[c_idx]
            dt_e = cycle_dates[c_idx + 1]
            p_start = prices_cycle[c_idx]
            p_end = prices_cycle[c_idx + 1]
            s_row = scores_cycle[c_idx]

            eligible = np.isfinite(s_row) & (s_row >= th) & np.isfinite(p_start) & (p_start > 0) & np.isfinite(p_end) & (p_end > 0)
            n_elig = int(np.sum(eligible))

            if n_elig > 0:
                stock_rets = (p_end[eligible] / p_start[eligible]) - 1.0 - round_trip_cost
                port_ret_pre = float(np.mean(stock_rets))
                tax_rate = get_historical_ltcg_tax_rate(int(dt_e.year), use_historical=True)
                port_ret_post = port_ret_pre * (1.0 - tax_rate) if port_ret_pre > 0 else port_ret_pre
                if port_ret_pre > 0:
                    wins += 1
                active_cycles += 1
                total_positions += n_elig
            else:
                rf = get_historical_rf_rate(int(dt_s.year))
                port_ret_pre = rf
                port_ret_post = rf * (1.0 - 0.25)

            nav_pre.append(nav_pre[-1] * (1.0 + max(-0.95, port_ret_pre)))
            nav_post.append(nav_post[-1] * (1.0 + max(-0.95, port_ret_post)))

        s_pre = pd.Series(nav_pre, index=cycle_dates)
        s_post = pd.Series(nav_post, index=cycle_dates)
        m_pre = compute_performance_metrics(s_pre)
        m_post = compute_performance_metrics(s_post)
        hit_rate = (wins / active_cycles * 100.0) if active_cycles > 0 else 0.0
        avg_holdings = (total_positions / active_cycles) if active_cycles > 0 else 0.0

        rows.append({
            "Threshold": th,
            "Avg_Holdings": avg_holdings,
            "Hit_Rate_%": hit_rate,
            "Pre_CAGR_%": m_pre["cagr"] * 100.0,
            "Post_CAGR_%": m_post["cagr"] * 100.0,
            "Pre_Sharpe": m_pre["sharpe"],
            "Post_Sharpe": m_post["sharpe"],
            "Max_DD_%": m_post["max_drawdown"] * 100.0,
        })

    return pd.DataFrame(rows)


def main() -> None:
    print("=" * 88)
    print(" WALK-FORWARD THRESHOLD OPTIMIZATION: IN-SAMPLE (1971-2008) -> OOS (2009-2026)")
    print("=" * 88)

    t0 = time.perf_counter()
    close_df, _, fund_dict, eps_dict, _ = load_or_generate_dataset("data")
    # Annual evaluation cycles (~252 trading days per step)
    cycle_dates_all = close_df.index[::252]

    # Single vectorized matrix pre-computation across all dates (replaces triple loop)
    scores_all, _, _ = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=cycle_dates_all,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
    )

    is_dates = cycle_dates_all[cycle_dates_all <= pd.Timestamp("2008-12-31")]
    oos_dates = cycle_dates_all[cycle_dates_all >= pd.Timestamp("2008-01-01")]

    thresholds = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

    df_is = evaluate_annual_threshold_cohorts(close_df, is_dates, scores_all, thresholds)
    df_oos = evaluate_annual_threshold_cohorts(close_df, oos_dates, scores_all, thresholds)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    best_idx = int(df_is["Post_Sharpe"].idxmax())
    best_th = float(df_is.loc[best_idx, "Threshold"])
    oos_row = df_oos.loc[df_oos["Threshold"] == best_th].iloc[0]

    print(f"\nPre-Computed Vectorized Grid Search Completed in {elapsed_ms:.1f} ms")
    print("\n1. In-Sample Training Grid (1971 - 2008):")
    print(df_is.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    print("\n2. Out-of-Sample Validation Grid (2009 - 2026):")
    print(df_oos.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    print(f"\nLocked In-Sample Optimal Threshold: M >= {best_th:.2f}")
    print(
        f"  -> Out-of-Sample (2009-2026) Post-Tax CAGR: {oos_row['Post_CAGR_%']:.2f}% | "
        f"Post-Tax Sharpe: {oos_row['Post_Sharpe']:.2f} | Max DD: {oos_row['Max_DD_%']:.2f}%"
    )

    fig_dir = Path("docs/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(df_is["Threshold"], df_is["Post_Sharpe"], marker="o", linewidth=2, label="In-Sample Post-Tax Sharpe (1971-2008)")
    ax.plot(df_oos["Threshold"], df_oos["Post_Sharpe"], marker="s", linewidth=2, label="Out-of-Sample Post-Tax Sharpe (2009-2026)")
    ax.axvline(best_th, color="red", linestyle="--", alpha=0.7, label=f"Selected IS Optimum (M={best_th:.2f})")
    ax.set_xlabel("Composite Munger Multiplier Threshold (M)")
    ax.set_ylabel("Post-Tax Sharpe Ratio")
    ax.set_title("Walk-Forward Factor Threshold Optimization (Zero Lookahead)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "munger_1y_threshold_optimization.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
