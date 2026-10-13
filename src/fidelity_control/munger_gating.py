"""Munger 200-Week & 200-Day SMA Technical Gating Engine."""

from __future__ import annotations
import pandas as pd
import numpy as np


def evaluate_munger_gating(
    current_price: float,
    weekly_history: pd.Series | pd.DataFrame,
    daily_history: pd.Series | pd.DataFrame | None = None,
    buffer_ratio: float = 1.05
) -> dict[str, float | bool | str]:
    """
    Evaluates Charlie Munger's 200-Week Moving Average gating overlay.
    
    Capital deployment into long-term secular compounders is favored when:
    Price <= 1.05 * SMA_200w (institutional secular support floor).
    """
    close_w = weekly_history['Close'] if isinstance(weekly_history, pd.DataFrame) else weekly_history
    clean_w = pd.to_numeric(close_w, errors="coerce").dropna()
    
    if len(clean_w) >= 200:
        sma200w = float(clean_w.rolling(window=200).mean().iloc[-1])
    elif len(clean_w) > 50:
        sma200w = float(clean_w.mean())
    else:
        sma200w = current_price * 0.85
        
    trigger_price_200w = sma200w * buffer_ratio
    dist_200w_pct = ((current_price - sma200w) / (sma200w + 1e-9)) * 100.0
    gating_open = current_price <= trigger_price_200w
    
    # 200-day Daily SMA
    sma200d = current_price
    sma50d = current_price
    dist_200d_pct = 0.0
    has_daily = False
    
    if daily_history is not None and not daily_history.empty:
        close_d = daily_history['Close'] if isinstance(daily_history, pd.DataFrame) else daily_history
        clean_d = pd.to_numeric(close_d, errors="coerce").dropna()
        if len(clean_d) >= 200:
            sma200d = float(clean_d.rolling(window=200).mean().iloc[-1])
            dist_200d_pct = ((current_price - sma200d) / (sma200d + 1e-9)) * 100.0
            has_daily = True
        if len(clean_d) >= 50:
            sma50d = float(clean_d.rolling(window=50).mean().iloc[-1])

    if gating_open:
        policy_status = "GATING_OPEN_BUY"
    elif dist_200w_pct > 35.0:
        policy_status = "EXTENDED_HOLD"
    elif has_daily and dist_200d_pct <= 2.0:
        policy_status = "PULLBACK_ZONE"
    else:
        policy_status = "ACCUMULATE_CASH"

    return {
        "current_price": current_price,
        "sma200w": sma200w,
        "trigger_price_200w": trigger_price_200w,
        "dist_200w_pct": dist_200w_pct,
        "sma200d": sma200d,
        "sma50d": sma50d,
        "dist_200d_pct": dist_200d_pct,
        "gating_open": gating_open,
        "policy_status": policy_status
    }
