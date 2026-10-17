#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR STRATEGY: CALL TENOR & STRIKE OPTIMIZATION
================================================================================
Refactored to:
  1. Replace the 50-step binary search (`find_strike_by_delta`) with exact O(1)
     closed-form inverse normal CDF strike inversion (`find_strike_by_delta_analytical`)
  2. Incorporate equity implied volatility skew and term structure (`implied_vol_with_skew`)
     so ITM long calls (0.65 delta) carry realistically higher IV than OTM short calls (0.30 delta)
  3. Evaluate expected return on capital across a lognormal underlying distribution
     rather than a single deterministic point return
================================================================================
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd

from munger_engine import (
    bsm_call,
    find_strike_by_delta_analytical,
    implied_vol_with_skew,
)


def find_strike_by_delta_legacy_binary(
    S: float,
    T: float,
    r: float,
    sigma: float,
    target_delta: float,
    q: float = 0.01,
    iterations: int = 50,
) -> float:
    """Legacy 50-iteration binary search preserved strictly for parity benchmarking."""
    low_K = S * 0.25
    high_K = S * 3.00
    for _ in range(iterations):
        mid_K = 0.5 * (low_K + high_K)
        c = bsm_call(S, mid_K, T, r, sigma, q)
        if c["delta"] > target_delta:
            low_K = mid_K
        else:
            high_K = mid_K
    return mid_K


def optimize_tenors(
    S0: float = 100.0,
    r: float = 0.045,
    q: float = 0.01,
    base_sigma: float = 0.32,
    expected_annual_drift: float = 0.145,
    target_long_delta: float = 0.65,
    target_short_delta: float = 0.30,
    roll_fee_per_leg: float = 0.15,
) -> pd.DataFrame:
    print("=" * 92)
    print(" MUNGER OPTIONS OVERLAY: ANALYTICAL O(1) DELTA STRIKE & SKEW-ADJUSTED TENOR OPTIMIZATION")
    print("=" * 92)

    tenors_days = [30, 60, 90, 120, 180, 365]
    # Gauss-Hermite / quantile grid for lognormal terminal stock price integration
    z_grid = np.linspace(-3.0, 3.0, 241)
    dz = z_grid[1] - z_grid[0]
    weights = np.exp(-0.5 * z_grid**2) / np.sqrt(2.0 * np.pi) * dz

    rows = []
    for dte in tenors_days:
        T = dte / 365.0
        periods_per_year = 365.0 / dte

        # Skew-adjusted implied volatilities for 65-delta ITM long leg vs 30-delta OTM short leg
        sigma_long = implied_vol_with_skew(target_long_delta, T, base_sigma=base_sigma)
        sigma_short = implied_vol_with_skew(target_short_delta, T, base_sigma=base_sigma)

        K_long = round(find_strike_by_delta_analytical(S0, T, r, sigma_long, target_long_delta, q), 2)
        K_short = round(find_strike_by_delta_analytical(S0, T, r, sigma_short, target_short_delta, q), 2)

        c_long_skew = bsm_call(S0, K_long, T, r, sigma_long, q)
        c_short_skew = bsm_call(S0, K_short, T, r, sigma_short, q)
        net_debit_skew = (c_long_skew["price"] - c_short_skew["price"]) + 2.0 * roll_fee_per_leg

        # Compare against flat-vol assumption
        c_long_flat = bsm_call(S0, K_long, T, r, base_sigma, q)
        c_short_flat = bsm_call(S0, K_short, T, r, base_sigma, q)
        net_debit_flat = (c_long_flat["price"] - c_short_flat["price"]) + 2.0 * roll_fee_per_leg

        # Lognormal terminal underlying distribution at expiration T
        realized_vol = base_sigma * 0.90
        ST_grid = S0 * np.exp((expected_annual_drift - q - 0.5 * realized_vol**2) * T + realized_vol * np.sqrt(T) * z_grid)
        payoff_grid = np.clip(ST_grid - K_long, 0.0, K_short - K_long)
        expected_payoff = float(np.sum(weights * payoff_grid))

        exp_roc_skew = (expected_payoff - net_debit_skew) / net_debit_skew
        exp_roc_flat = (expected_payoff - net_debit_flat) / net_debit_flat

        # Fractional Kelly / Compounding allocation (20% overlay budget) to avoid full-capital ruin bias
        overlay_weight = 0.20
        blended_period_ret = overlay_weight * exp_roc_skew + (1.0 - overlay_weight) * (r * T)
        annualized_blended_cagr = ((1.0 + max(-0.95, blended_period_ret)) ** periods_per_year - 1.0) * 100.0

        rows.append({
            "DTE": dte,
            "K_Long (65d)": K_long,
            "K_Short (30d)": K_short,
            "IV_Long / IV_Short": f"{sigma_long*100:.1f}% / {sigma_short*100:.1f}%",
            "Debit (Skew)": round(net_debit_skew, 2),
            "Debit (Flat IV)": round(net_debit_flat, 2),
            "Cap_Efficiency": f"{S0 / net_debit_skew:.2f}x",
            "Net_Delta": round(c_long_skew["delta"] - c_short_skew["delta"], 3),
            "Net_Theta": round(c_long_skew["theta"] - c_short_skew["theta"], 4),
            "E[ROC/Roll] (Skew)": f"{exp_roc_skew*100:+.2f}%",
            "E[ROC/Roll] (Flat)": f"{exp_roc_flat*100:+.2f}%",
            "20%_Overlay_CAGR": f"{annualized_blended_cagr:+.2f}%",
        })

    df = pd.DataFrame(rows)
    print("\nTenor & Skew-Adjusted Bull Call Spread Optimization Results:")
    print(df.to_string(index=False))
    return df


if __name__ == "__main__":
    optimize_tenors()
