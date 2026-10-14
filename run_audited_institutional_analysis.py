#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR STRATEGY: AUDITED INSTITUTIONAL PERFORMANCE & ALPHA BRIDGE
================================================================================
Refactored institutional audit script:
  - Enforces 15% max position cap (7 concurrent positions max)
  - Uses exact calendar days elapsed between evaluation dates for risk-free cash
    and benchmark dividend compounding (replacing hard-coded 21-day assumption)
  - Applies historical US federal Long-Term Capital Gains tax brackets by era
  - Quantifies the exact alpha bridge across factor gates and execution drags
================================================================================
"""

from __future__ import annotations

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
    print(" AUDITED INSTITUTIONAL PERFORMANCE & STEP-BY-STEP ALPHA ATTRIBUTION BRIDGE")
    print("=" * 88)

    close_df, gspc_close, fund_dict, eps_dict, is_synthetic = load_or_generate_dataset("data")
    eval_dates = close_df.index[::21]

    scores_df, above_med_df, _ = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
    )

    # 1. Baseline Equal-Weight Universe (Low threshold M >= 1.0, annually rebalanced)
    cfg_universe = BacktestConfig(
        max_pos_pct=0.05,
        min_hold_days=365,
        m_buy_threshold=1.0,
        require_above_median=False,
        slippage_bps=5.0,
    )
    res_univ = run_portfolio_simulation(close_df, gspc_close, eval_dates, scores_df, above_med_df, cfg_universe)

    # 2. Quality + Valuation Gated (M >= 2.0, 15% Max Position Cap -> 7 positions)
    cfg_gated = BacktestConfig(
        max_pos_pct=0.15,
        min_hold_days=365,
        m_buy_threshold=2.0,
        require_above_median=False,
        slippage_bps=5.0,
    )
    res_gated = run_portfolio_simulation(close_df, gspc_close, eval_dates, scores_df, above_med_df, cfg_gated)

    # 3. Full Audited Institutional Munger System (M >= 2.0 + Median Exit Discipline + Delisting Haircut)
    cfg_inst = BacktestConfig(
        max_pos_pct=0.15,
        min_hold_days=365,
        m_buy_threshold=2.0,
        require_above_median=True,
        slippage_bps=5.0,
        use_historical_tax_regimes=True,
        delisting_loss_haircut=0.30,
    )
    res_inst = run_portfolio_simulation(close_df, gspc_close, eval_dates, scores_df, above_med_df, cfg_inst)

    bench_cagr = res_inst["metrics_bench"]["cagr"] * 100.0
    univ_cagr = res_univ["metrics_pre"]["cagr"] * 100.0
    gated_cagr = res_gated["metrics_pre"]["cagr"] * 100.0
    inst_pre_cagr = res_inst["metrics_pre"]["cagr"] * 100.0
    inst_post_cagr = res_inst["metrics_post"]["cagr"] * 100.0
    tax_drag = inst_pre_cagr - inst_post_cagr

    bridge_rows = [
        {"Component": "1. S&P 500 Total Return Benchmark (Era Dividend Yield)", "Annualized Impact": f"{bench_cagr:.2f}%"},
        {"Component": "2. Broad Universe Equal-Weight Spread vs. Benchmark", "Annualized Impact": f"{univ_cagr - bench_cagr:+.2f}%"},
        {"Component": "3. 4-Factor Multiplicative Quality Gate (M >= 2.0, 15% Cap)", "Annualized Impact": f"{gated_cagr - univ_cagr:+.2f}%"},
        {"Component": "4. Tax-Aware >365d Hold & Median-Breach Exit Discipline", "Annualized Impact": f"{inst_pre_cagr - gated_cagr:+.2f}%"},
        {"Component": "5. Audited Pre-Tax Institutional CAGR (Net of 5bps Slippage)", "Annualized Impact": f"{inst_pre_cagr:.2f}%"},
        {"Component": "6. Realized Historical LTCG Tax Drag", "Annualized Impact": f"{-tax_drag:+.2f}%"},
        {"Component": "7. Audited Post-Tax Institutional Net CAGR", "Annualized Impact": f"{inst_post_cagr:.2f}%"},
    ]

    print("\nInstitutional Alpha Attribution Bridge:")
    print(pd.DataFrame(bridge_rows).to_string(index=False))

    print("\nKey Risk & Concentration Metrics (7-Position Cap / 15% Max Weight):")
    print(f"  -> Pre-Tax Sharpe Ratio : {res_inst['metrics_pre']['sharpe']:.2f} (vs. S&P 500 {res_inst['metrics_bench']['sharpe']:.2f})")
    print(f"  -> Post-Tax Sharpe Ratio: {res_inst['metrics_post']['sharpe']:.2f}")
    print(f"  -> Max Drawdown (Pre)   : {res_inst['metrics_pre']['max_drawdown']*100:.2f}% (vs. S&P 500 {res_inst['metrics_bench']['max_drawdown']*100:.2f}%)")
    print(f"  -> Annualized Volatility: {res_inst['metrics_pre']['vol']*100:.2f}%")

    fig_dir = Path("docs/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(res_inst["nav_pre"].index, np.log10(res_inst["nav_pre"]), label="Institutional Munger (Pre-Tax)", linewidth=2.0)
    ax.plot(res_inst["nav_post"].index, np.log10(res_inst["nav_post"]), label="Institutional Munger (Post-Tax)", linewidth=1.8)
    ax.plot(res_inst["bench_nav"].index, np.log10(res_inst["bench_nav"]), label="S&P 500 Total Return", color="black", alpha=0.75)
    ax.set_title("Audited Institutional Compounding Curve (15% Position Cap, Exact Day Cash Yield)")
    ax.set_ylabel("Log10 Portfolio NAV ($)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "munger_audited_compounding_curve.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
