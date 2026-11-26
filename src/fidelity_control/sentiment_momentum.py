"""Structural Drawdown and Cross-Sectional Momentum Metrics (RSI/ExtIdx retired)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_52w_drawdown_pct(prices: pd.Series, window: int = 252) -> float:
    """Computes trailing 52-week drawdown percentage from rolling high (<= 0.0)."""
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    if clean.empty:
        return 0.0
    tail = clean.iloc[-window:] if len(clean) >= window else clean
    peak = float(tail.max())
    if peak <= 1e-9:
        return 0.0
    current = float(tail.iloc[-1])
    return float(min(0.0, ((current / peak) - 1.0) * 100.0))


def classify_structural_drawdown(
    dd_52w_pct: float,
    dist_100w_pct: float,
    dist_200w_pct: float,
) -> str:
    """
    Classifies structural price position using 52-week drawdown and distance to
    100-week / 200-week Munger secular moving averages (replaces legacy RSI/ExtIdx).
    """
    if dist_200w_pct <= 5.0 or (dist_100w_pct <= -10.0 and dd_52w_pct <= -25.0):
        return "DEEP_STRUCTURAL_DISCOUNT"
    if dist_100w_pct <= 5.0 or dd_52w_pct <= -15.0:
        return "PULLBACK_ZONE"
    if dist_100w_pct >= 35.0 and dd_52w_pct >= -5.0:
        return "EXTENDED"
    return "NEUTRAL"


def compute_residual_momentum(
    stock_prices: pd.Series,
    benchmark_prices: pd.Series | None = None,
    skip_days: int = 21,
    lookback_days: int = 252,
) -> float:
    """Computes 12-1 Residual Momentum (252 days lookback skipping recent 21 days)."""
    if benchmark_prices is not None and not benchmark_prices.empty:
        aligned = pd.concat([stock_prices.rename("stock"), benchmark_prices.rename("bench")], axis=1, sort=False).dropna()
        if len(aligned) >= lookback_days + skip_days:
            frame = aligned.iloc[-(lookback_days + skip_days) : -skip_days]
            stock_ret = frame["stock"].iloc[-1] / frame["stock"].iloc[0] - 1.0
            bench_ret = frame["bench"].iloc[-1] / frame["bench"].iloc[0] - 1.0
            return float(stock_ret - bench_ret)

    clean = pd.to_numeric(stock_prices, errors="coerce").dropna()
    if len(clean) >= lookback_days + skip_days:
        start = clean.iloc[-(lookback_days + skip_days)]
        end = clean.iloc[-skip_days]
        return float(end / start - 1.0) if start > 0 else 0.0
    return 0.0


def compute_zscore(prices: pd.Series, window: int = 50) -> float:
    """Computes current price distance from moving average in standard deviations."""
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    if len(clean) < window:
        return 0.0
    rolling_mean = clean.rolling(window=window).mean().iloc[-1]
    rolling_std = clean.rolling(window=window).std().iloc[-1]
    if rolling_std <= 1e-6:
        return 0.0
    return float((clean.iloc[-1] - rolling_mean) / rolling_std)
