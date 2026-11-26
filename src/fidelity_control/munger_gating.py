"""Munger Two-Tier (100-Week / 200-Week / 50-Week) & 200-Day SMA Technical Gating Engine."""

from __future__ import annotations
import pandas as pd
import numpy as np


def evaluate_munger_gating(
    current_price: float,
    weekly_history: pd.Series | pd.DataFrame,
    daily_history: pd.Series | pd.DataFrame | None = None,
    buffer_ratio: float = 1.05
) -> dict[str, float | bool | str | int]:
    """
    Evaluates Charlie Munger's Two-Tier Weekly Moving Average gating overlay:
      - TIER2_BUY_100%: Price <= 1.05 * SMA_200w (deep crash institutional floor)
      - TIER1_BUY_50%:  Price <= 1.05 * SMA_100w (backtest-optimal 2Y compounder sweet spot)
      - CSP_LIZARD_ZONE: Price <= 1.00 * SMA_50w (sell 25d Cash-Secured Puts / Jade Lizards)
      - HOLD_CASH: Extended above SMA_50w

    Preserves `gating_open` (True when <= 1.05 * SMA_200w) and `policy_status`
    for strict backward compatibility with existing pipeline & tests.
    """
    close_w = weekly_history['Close'] if isinstance(weekly_history, pd.DataFrame) else weekly_history
    clean_w = pd.to_numeric(close_w, errors="coerce").dropna()

    if len(clean_w) >= 200:
        sma200w = float(clean_w.rolling(window=200).mean().iloc[-1])
    elif len(clean_w) > 50:
        sma200w = float(clean_w.mean())
    else:
        sma200w = current_price * 0.85

    if len(clean_w) >= 100:
        sma100w = float(clean_w.rolling(window=100).mean().iloc[-1])
    elif len(clean_w) > 50:
        sma100w = float(clean_w.mean())
    else:
        sma100w = current_price * 0.85

    if len(clean_w) >= 50:
        sma50w = float(clean_w.rolling(window=50).mean().iloc[-1])
    elif len(clean_w) > 0:
        sma50w = float(clean_w.mean())
    else:
        sma50w = current_price * 0.90

    trigger_price_200w = sma200w * buffer_ratio
    trigger_price_100w = sma100w * buffer_ratio
    dist_200w_pct = ((current_price - sma200w) / (sma200w + 1e-9)) * 100.0
    dist_100w_pct = ((current_price - sma100w) / (sma100w + 1e-9)) * 100.0
    dist_50w_pct = ((current_price - sma50w) / (sma50w + 1e-9)) * 100.0

    tier2_open = current_price <= trigger_price_200w
    tier1_open = current_price <= trigger_price_100w
    csp_lizard_zone = current_price <= sma50w
    gating_open = tier2_open

    if tier2_open:
        gating_tier = "TIER2_BUY_100%"
        deploy_tranche_pct = 100
    elif tier1_open:
        gating_tier = "TIER1_BUY_50%"
        deploy_tranche_pct = 50
    elif csp_lizard_zone:
        gating_tier = "CSP_LIZARD_ZONE"
        deploy_tranche_pct = 0
    else:
        gating_tier = "HOLD_CASH"
        deploy_tranche_pct = 0

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
        "sma50w": sma50w,
        "sma100w": sma100w,
        "sma200w": sma200w,
        "trigger_price_100w": trigger_price_100w,
        "trigger_price_200w": trigger_price_200w,
        "dist_50w_pct": dist_50w_pct,
        "dist_100w_pct": dist_100w_pct,
        "dist_200w_pct": dist_200w_pct,
        "sma200d": sma200d,
        "sma50d": sma50d,
        "dist_200d_pct": dist_200d_pct,
        "gating_open": gating_open,
        "tier1_open": tier1_open,
        "tier2_open": tier2_open,
        "csp_lizard_zone": csp_lizard_zone,
        "gating_tier": gating_tier,
        "deploy_tranche_pct": deploy_tranche_pct,
        "policy_status": policy_status,
    }
