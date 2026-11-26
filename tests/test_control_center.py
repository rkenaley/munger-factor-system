"""Comprehensive test suite for Fidelity Control Center."""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from fidelity_control.sentiment_momentum import (
    compute_52w_drawdown_pct,
    classify_structural_drawdown,
    compute_residual_momentum,
    compute_zscore,
)
from fidelity_control.munger_gating import evaluate_munger_gating
from fidelity_control.valuation_dcf import calculate_owner_earnings_dcf
from fidelity_control.screener import run_extremizing_screener, FALLBACK_FUNDAMENTALS
from fidelity_control.portfolio_drift import evaluate_portfolio_drift, audit_tax_placement
from fidelity_control.daily_trades import generate_daily_trade_orders
from fidelity_control.config import load_config
from fidelity_control.control_center import run_pipeline


def test_52w_drawdown_calculation():
    # Flat prices -> 0% drawdown
    series = pd.Series([100.0] * 30)
    assert compute_52w_drawdown_pct(series) == 0.0

    # Peak 200, current 150 -> -25% drawdown
    drop_series = pd.Series([100.0] * 20 + [200.0] + [150.0])
    assert compute_52w_drawdown_pct(drop_series) == pytest.approx(-25.0)

    # Monotonically increasing -> 0% drawdown
    inc_series = pd.Series(range(1, 35), dtype=float)
    assert compute_52w_drawdown_pct(inc_series) == 0.0


def test_residual_momentum():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    stock = pd.Series(np.linspace(100, 200, 300), index=dates)
    bench = pd.Series(np.linspace(100, 150, 300), index=dates)
    mom = compute_residual_momentum(stock, bench)
    assert mom > 0.0 # stock outperformed benchmark


def test_structural_drawdown_classification():
    # Near/below 200w SMA -> deep structural discount
    assert classify_structural_drawdown(dd_52w_pct=-30.0, dist_100w_pct=-15.0, dist_200w_pct=2.0) == "DEEP_STRUCTURAL_DISCOUNT"

    # Pullback near 100w SMA -> pullback zone
    assert classify_structural_drawdown(dd_52w_pct=-18.0, dist_100w_pct=3.0, dist_200w_pct=22.0) == "PULLBACK_ZONE"

    # Stretched well above 100w SMA near 52W highs -> extended
    assert classify_structural_drawdown(dd_52w_pct=-1.0, dist_100w_pct=45.0, dist_200w_pct=65.0) == "EXTENDED"

    # Normal trend -> neutral
    assert classify_structural_drawdown(dd_52w_pct=-8.0, dist_100w_pct=15.0, dist_200w_pct=30.0) == "NEUTRAL"


def test_munger_gating():
    # When price is below 1.05 * 200w SMA, gating is open (Tier 2)
    weekly = pd.Series([100.0] * 210)
    res = evaluate_munger_gating(current_price=102.0, weekly_history=weekly)
    assert res['gating_open'] is True
    assert res['policy_status'] == "GATING_OPEN_BUY"
    assert res['gating_tier'] == "TIER2_BUY_100%"
    assert res['deploy_tranche_pct'] == 100

    # When price is extended 50% above 200w SMA, gating is closed
    res_ext = evaluate_munger_gating(current_price=150.0, weekly_history=weekly)
    assert res_ext['gating_open'] is False
    assert res_ext['policy_status'] == "EXTENDED_HOLD"
    assert res_ext['gating_tier'] == "HOLD_CASH"

    # Two-Tier sweet spot: SMA100w = 140, SMA200w ~ 118 -> price=142 hits TIER1_BUY_50%
    w_step = pd.Series([100.0] * 120 + [140.0] * 100)
    res_t1 = evaluate_munger_gating(current_price=142.0, weekly_history=w_step)
    assert res_t1['tier1_open'] is True
    assert res_t1['tier2_open'] is False
    assert res_t1['gating_tier'] == "TIER1_BUY_50%"
    assert res_t1['deploy_tranche_pct'] == 50

    # CSP / Jade Lizard zone: SMA50w = 160, SMA100w ~ 130 -> price=155 hits CSP_LIZARD_ZONE
    w_csp = pd.Series([100.0] * 170 + [160.0] * 50)
    res_csp = evaluate_munger_gating(current_price=155.0, weekly_history=w_csp)
    assert res_csp['csp_lizard_zone'] is True
    assert res_csp['gating_tier'] == "CSP_LIZARD_ZONE"


def test_valuation_dcf():
    dcf = calculate_owner_earnings_dcf(
        current_price=100.0,
        fcf_per_share=5.0,
        initial_growth_rate=0.15
    )
    assert dcf['intrinsic_value'] > 0.0
    assert dcf['buy_below'] < dcf['intrinsic_value']
    assert dcf['strong_buy_below'] < dcf['buy_below']
    assert dcf['trim_above'] > dcf['intrinsic_value']


def test_screener_execution():
    res = run_extremizing_screener(FALLBACK_FUNDAMENTALS)
    assert "stats" in res
    assert "passing_candidates" in res
    assert "failing_candidates" in res


def test_full_pipeline(tmp_path):
    res = run_pipeline(output_dir=tmp_path)
    assert res['orders_count'] > 0
    assert res['alerts_count'] > 0
    assert Path(res['orders_csv']).exists()
    assert Path(res['alerts_csv']).exists()
    assert Path(res['watchlist_csv']).exists()
    wl_header = Path(res['watchlist_csv']).read_text(encoding="utf-8").splitlines()[0]
    assert "52W Drawdown (%)" in wl_header
    assert "vs 100W SMA (%)" in wl_header
    assert "vs 200W SMA (%)" in wl_header
    assert "RSI" not in wl_header
    assert "Extremizing" not in wl_header
    assert Path(res['json_report']).exists()
