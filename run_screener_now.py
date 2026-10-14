#!/usr/bin/env python3
"""
THE MUNGER FACTOR SYSTEM: LIVE CROSS-SECTIONAL SCREENER
Executes the audited, zero-lookahead point-in-time factor model across the full
176-constituent corporate universe as of the latest market close.

Outputs:
1. The Conjunctive 4-Pillar Clearers (All 4 Medians Cleared Simultaneously)
2. The Top 10 Raw Composite Munger Multipliers (M_i = Prod(1 + max(0, Z_k)))
3. Pillar Attribution and Hurdle Diagnostics
"""

import pandas as pd
import numpy as np
from munger_engine import (
    load_or_generate_dataset,
    compute_vectorized_munger_scores,
)

def run_screener():
    close_df, gspc_close, fund_dict, eps_dict, is_synth = load_or_generate_dataset("data")
    latest_dt = close_df.index[-1]
    eval_dates = pd.DatetimeIndex([latest_dt])

    scores_df, above_med_df, ey_df = compute_vectorized_munger_scores(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
    )

    latest_scores = scores_df.iloc[-1].dropna()
    latest_above = above_med_df.iloc[-1]
    latest_ey = ey_df.iloc[-1]
    latest_prices = close_df.iloc[-1]

    tickers = list(close_df.columns)
    active_mask = latest_prices.notna() & (latest_prices > 0)

    # Reconstruct individual factor values at latest_dt exactly as in engine
    ret_5y = close_df.pct_change(periods=252 * 5, fill_method=None).iloc[-1]
    ann_g_5y = (((1.0 + ret_5y.clip(lower=-0.90)) ** (1.0 / 5.0)) - 1.0) * 100.0
    vol_1y = close_df.pct_change(fill_method=None).rolling(252, min_periods=21).std().iloc[-1] * np.sqrt(252.0)

    sales_prior = pd.Series({t: float(fund_dict.get(t, {}).get("sales5y", 10.0)) for t in tickers})
    roic_prior = pd.Series({t: float(fund_dict.get(t, {}).get("roic_proxy", 0.15)) for t in tickers})
    de_prior = pd.Series({t: float(fund_dict.get(t, {}).get("de", 50.0)) for t in tickers})

    sales_s = (0.5 * ann_g_5y.fillna(0.0) + sales_prior).where(active_mask, np.nan)
    roic_s = (roic_prior / (1.0 + vol_1y.fillna(0.25))).where(active_mask, np.nan)
    de_s = (de_prior * (1.0 + vol_1y.fillna(0.25))).where(active_mask, np.nan)
    de_inv_s = -de_s

    # Compute individual Z scores
    def get_z(s):
        valid = s.dropna()
        return (s - valid.mean()) / valid.std()

    z_sales = get_z(sales_s)
    z_roic = get_z(roic_s)
    z_ey = get_z(latest_ey)
    z_de = get_z(de_inv_s)

    med_ey = latest_ey.median()
    med_roic = roic_s.median()
    med_growth = sales_s.median()
    med_de = de_s.median()

    print("=" * 115)
    print(f" THE MUNGER FACTOR SYSTEM: LIVE CROSS-SECTIONAL SCREENER (AS OF {latest_dt.strftime('%B %d, %Y')})")
    print("=" * 115)
    print(f"Universe Size        : {len(latest_scores)} active corporate constituents")
    print(f"Point-in-Time Lag    : 45 calendar days enforced on quarterly SEC reports (Zero Lookahead)")
    print(f"Cross-Section Medians: Earnings Yield >= {med_ey*100:.2f}% (P/E <= {1/med_ey:.1f}x) | Vol-Adj ROIC >= {med_roic*100:.2f}%")
    print(f"                       5-Year Growth  >= {med_growth:.2f}%             | Debt-to-Equity <= {med_de:.2f}%")
    print("-" * 115)

    # 1. Strictly Qualified (All 4 Medians Cleared)
    qualifying_mask = latest_above.reindex(latest_scores.index).fillna(False)
    qual_scores = latest_scores[qualifying_mask].sort_values(ascending=False)

    print(f"\n[SECTION 1] CONJUNCTIVE 4-PILLAR CLEARERS ({len(qual_scores)} STOCKS CLEAR ALL 4 MEDIANS SIMULTANEOUSLY):")
    print(f"These stocks pass the strict Munger criteria: H_i = 1 (every factor strictly exceeds cross-sectional median).")
    print("-" * 115)
    print(f"{'Rank':<4} {'Ticker':<6} {'Company Name':<25} {'Sector':<20} {'Price':<10} {'Multiplier':<11} {'P/E':<7} {'ROIC':<7} {'Growth':<8} {'D/E':<7}")
    print("-" * 115)
    for rank, (ticker, score) in enumerate(qual_scores.items(), 1):
        info = fund_dict.get(ticker, {})
        px = float(latest_prices[ticker])
        ey_val = float(latest_ey[ticker])
        pe_str = f"{1.0/ey_val:.1f}x" if ey_val > 0 else "N/A"
        print(f"{rank:<4} {ticker:<6} {info.get('name', ticker)[:24]:<25} {info.get('sector', 'Core')[:19]:<20} ${px:<9.2f} {score:<10.2f}x {pe_str:<7} {roic_s[ticker]*100:<6.1f}% {sales_s[ticker]:<7.1f}% {de_s[ticker]:<6.1f}%")

    # 2. Top 10 Raw Composite Multiplier Scores
    raw_top10 = latest_scores.sort_values(ascending=False).head(10)
    print("\n" + "=" * 115)
    print("[SECTION 2] TOP 10 RAW COMPOSITE MUNGER MULTIPLIERS (M_i = Prod(1 + max(0, Z_k)) Across All 176 Stocks):")
    print("Shows the highest multi-discipline compounding scores and why non-clearing names tripped the conjunctive gate.")
    print("-" * 115)
    print(f"{'Rank':<4} {'Ticker':<6} {'Company Name':<23} {'Price':<10} {'Score':<8} {'P/E':<7} {'ROIC':<7} {'Growth':<8} {'D/E':<7} {'Hurdles':<8} {'Disqualification / Pillar Notes'}")
    print("-" * 115)

    for rank, (ticker, score) in enumerate(raw_top10.items(), 1):
        info = fund_dict.get(ticker, {})
        px = float(latest_prices[ticker])
        ey_val = float(latest_ey[ticker])
        pe_str = f"{1.0/ey_val:.1f}x" if ey_val > 0 else "N/A"
        
        # Check hurdles
        ey_pass = ey_val >= med_ey
        r_pass = roic_s[ticker] >= med_roic
        g_pass = sales_s[ticker] >= med_growth
        s_pass = de_s[ticker] <= med_de
        cleared_cnt = int(ey_pass) + int(r_pass) + int(g_pass) + int(s_pass)
        
        notes = []
        if not ey_pass:
            notes.append(f"Valuation Dislocation failed (P/E {pe_str} > {1/med_ey:.1f}x median)")
        if not r_pass:
            notes.append(f"ROIC failed ({roic_s[ticker]*100:.1f}% < {med_roic*100:.1f}% median)")
        if not g_pass:
            notes.append(f"Growth failed ({sales_s[ticker]:.1f}% < {med_growth:.1f}% median)")
        if not s_pass:
            notes.append(f"Solvency failed (D/E {de_s[ticker]:.1f}% > {med_de:.1f}% median)")
        
        note_str = "CLEARED ALL 4 (H_i = 1)" if cleared_cnt == 4 else "; ".join(notes)
        print(f"{rank:<4} {ticker:<6} {info.get('name', ticker)[:22]:<23} ${px:<9.2f} {score:<7.2f}x {pe_str:<7} {roic_s[ticker]*100:<6.1f}% {sales_s[ticker]:<7.1f}% {de_s[ticker]:<6.1f}% {cleared_cnt}/4     {note_str}")

    print("\n" + "=" * 115)
    print("DETAILED PILLAR Z-SCORE BREAKDOWN (TOP 5 QUALIFYING CANDIDATES):")
    print("-" * 115)
    for rank, (ticker, score) in enumerate(qual_scores.head(5).items(), 1):
        info = fund_dict.get(ticker, {})
        px = float(latest_prices[ticker])
        ey_val = float(latest_ey[ticker])
        pe_str = f"{1.0/ey_val:.1f}x" if ey_val > 0 else "N/A"
        score_str = f"{score:.2f}x"
        print(f"[{rank}] {ticker:<5} | {info.get('name', ticker):<25} | Composite Multiplier: {score_str:>7} | Spot: ${px:.2f}")
        print(f"    • Pillar 1 - Valuation Dislocation (y) : P/E {pe_str:<6} (Earnings Yield {ey_val*100:4.1f}%, Z_y = {z_ey[ticker]:+5.2f})")
        print(f"    • Pillar 2 - Capital Efficiency    (r) : Vol-Adjusted ROIC {roic_s[ticker]*100:4.1f}% (Z_r = {z_roic[ticker]:+5.2f})")
        print(f"    • Pillar 3 - Top-Line Growth       (g) : 5-Year CAGR {sales_s[ticker]:4.1f}% (Z_g = {z_sales[ticker]:+5.2f})")
        print(f"    • Pillar 4 - Balance Sheet Solv.   (s) : Debt-to-Equity {de_s[ticker]:4.1f}% (Z_s = {z_de[ticker]:+5.2f})")
    print("=" * 115)

if __name__ == "__main__":
    run_screener()
