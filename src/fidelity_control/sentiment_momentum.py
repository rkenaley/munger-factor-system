"""Sentiment, Momentum, and Extremizing Indicators."""

from __future__ import annotations
import numpy as np
import pandas as pd


def compute_rsi(prices: pd.Series, period: int = 14) -> float:
    """Computes standard Relative Strength Index (RSI 14)."""
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    if len(clean) < period + 1:
        return 50.0
    
    delta = clean.diff().fillna(0.0)
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    
    # Wilder smoothing for remainder
    for i in range(period, len(clean)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
        
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    
    if abs(last_gain) < 1e-9 and abs(last_loss) < 1e-9:
        return 50.0 # completely flat series is neutral 50
    if abs(last_loss) < 1e-9:
        return 100.0 # purely gaining
    if abs(last_gain) < 1e-9:
        return 0.0 # purely losing
        
    rs = last_gain / last_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(np.clip(rsi, 0.0, 100.0))


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


def compute_extremizing_index(
    rsi14: float,
    zscore_50d: float,
    dist_200d_pct: float,
    residual_mom: float,
    dist_200w_pct: float | None = None
) -> tuple[float, str]:
    """
    Computes a synthesized Extremizing Index ranging from -100 to +100.
    
    Index <= -50: EXTREME_OVERSOLD (Aggressive Margin-of-Safety Buy Zone)
    -50 to -20:   OVERSOLD (Favorable Staged Accumulation Zone)
    -20 to +40:   NEUTRAL (Scheduled Rebalancing / Tranche Hold)
    +40 to +70:   OVERBOUGHT (Trim Review / Hold Zone)
    >= +70:       EXTREME_OVERBOUGHT (Frothy / Covered Call & Trim Zone)
    """
    # 1. RSI Scaled to -100..+100 (Neutral = 50 -> 0)
    rsi_scaled = float(np.clip((rsi14 - 50.0) * 2.0, -100.0, 100.0))
    
    # 2. 50-day Mean-Reversion Z-score Scaled to -100..+100 (+/- 2.5 sigma = +/- 100)
    z_scaled = float(np.clip(zscore_50d * 40.0, -100.0, 100.0))
    
    # 3. 200-day Trend Extension Scaled to -100..+100 (+/- 25% = +/- 100)
    trend_scaled = float(np.clip(dist_200d_pct * 4.0, -100.0, 100.0))
    
    # Weighted composite index spanning -100 to +100
    composite = (rsi_scaled * 0.50) + (z_scaled * 0.30) + (trend_scaled * 0.20)
    
    # Adjust for 200-week Munger floor if available
    if dist_200w_pct is not None and dist_200w_pct <= 5.0:
        composite -= 15.0
        
    final_index = float(np.clip(composite, -100.0, 100.0))
    
    if final_index <= -50.0:
        status = "EXTREME_OVERSOLD"
    elif final_index <= -20.0:
        status = "OVERSOLD"
    elif final_index < 40.0:
        status = "NEUTRAL"
    elif final_index < 70.0:
        status = "OVERBOUGHT"
    else:
        status = "EXTREME_OVERBOUGHT"
        
    return final_index, status
