"""Comprehensive test suite for Fidelity Control Center."""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from fidelity_control.sentiment_momentum import (
    compute_rsi,
    compute_residual_momentum,
    compute_zscore,
    compute_extremizing_index
)
from fidelity_control.munger_gating import evaluate_munger_gating
from fidelity_control.valuation_dcf import calculate_owner_earnings_dcf
from fidelity_control.screener import run_extremizing_screener, FALLBACK_FUNDAMENTALS
from fidelity_control.portfolio_drift import evaluate_portfolio_drift, audit_tax_placement
from fidelity_control.daily_trades import generate_daily_trade_orders
from fidelity_control.config import load_config
from fidelity_control.control_center import run_pipeline


def test_rsi_calculation():
    # Constant prices -> RSI 50
    series = pd.Series([100.0] * 30)
    assert compute_rsi(series) == 50.0
    
    # Monotonically increasing -> RSI 100
    inc_series = pd.Series(range(1, 35), dtype=float)
    assert compute_rsi(inc_series) > 85.0
    
    # Monotonically decreasing -> RSI near 0
    dec_series = pd.Series(range(35, 1, -1), dtype=float)
    assert compute_rsi(dec_series) < 15.0


def test_residual_momentum():
    dates = pd.date_range("2025-01-01", periods=300, freq="B")
    stock = pd.Series(np.linspace(100, 200, 300), index=dates)
    bench = pd.Series(np.linspace(100, 150, 300), index=dates)
    mom = compute_residual_momentum(stock, bench)
    assert mom > 0.0 # stock outperformed benchmark


def test_extremizing_index():
    # Extreme oversold inputs: low RSI, negative zscore, near 200w SMA
    idx, status = compute_extremizing_index(
        rsi14=22.0,
        zscore_50d=-2.5,
        dist_200d_pct=-18.0,
        residual_mom=-0.10,
        dist_200w_pct=2.0
    )
    assert idx <= -50.0
    assert status == "EXTREME_OVERSOLD"
    
    # Extreme overbought inputs: high RSI, positive zscore, extended from 200d
    idx_high, status_high = compute_extremizing_index(
        rsi14=82.0,
        zscore_50d=2.8,
        dist_200d_pct=35.0,
        residual_mom=0.45,
        dist_200w_pct=40.0
    )
    assert idx_high >= 60.0
    assert status_high in ["OVERBOUGHT", "EXTREME_OVERBOUGHT"]


def test_munger_gating():
    # When price is below 1.05 * 200w SMA, gating is open
    weekly = pd.Series([100.0] * 210)
    res = evaluate_munger_gating(current_price=102.0, weekly_history=weekly)
    assert res['gating_open'] is True
    assert res['policy_status'] == "GATING_OPEN_BUY"
    
    # When price is extended 50% above 200w SMA, gating is closed
    res_ext = evaluate_munger_gating(current_price=150.0, weekly_history=weekly)
    assert res_ext['gating_open'] is False
    assert res_ext['policy_status'] == "EXTENDED_HOLD"


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
    assert Path(res['json_report']).exists()
