"""Owner-Earnings DCF & Margin of Safety Valuation."""

from __future__ import annotations


def calculate_owner_earnings_dcf(
    current_price: float,
    fcf_per_share: float,
    initial_growth_rate: float,
    terminal_growth_rate: float = 0.03,
    discount_rate: float = 0.09,
    years_stage1: int = 5
) -> dict[str, float]:
    """
    Two-stage owner-earnings discounted cash flow valuation with margin of safety thresholds.
    """
    if fcf_per_share <= 0:
        # For pre-profit or early-stage businesses, use multiple or price proxy
        intrinsic = current_price
    else:
        pv_stage1 = 0.0
        cf = fcf_per_share
        growth = min(initial_growth_rate, 0.30) # cap runaway exponential growth
        
        for y in range(1, years_stage1 + 1):
            cf *= (1.0 + growth)
            pv_stage1 += cf / ((1.0 + discount_rate) ** y)
            growth = growth * 0.85 + terminal_growth_rate * 0.15 # fade toward terminal
            
        terminal_val = (cf * (1.0 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
        pv_terminal = terminal_val / ((1.0 + discount_rate) ** years_stage1)
        intrinsic = pv_stage1 + pv_terminal
        
    intrinsic = max(intrinsic, current_price * 0.5)
    
    buy_below = intrinsic * 0.80
    strong_buy_below = intrinsic * 0.65
    trim_above = intrinsic * 1.20
    
    return {
        "intrinsic_value": round(intrinsic, 2),
        "buy_below": round(buy_below, 2),
        "strong_buy_below": round(strong_buy_below, 2),
        "trim_above": round(trim_above, 2),
        "margin_of_safety_pct": round(((intrinsic - current_price) / intrinsic) * 100.0, 1)
    }
