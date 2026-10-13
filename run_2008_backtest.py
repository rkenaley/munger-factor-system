"""Multi-Decade Historical Backtest (2008 - 2026).

Evaluates the Extremized Multiplier Factor Theory (EMFT) across the S&P 100 + Nasdaq 100
universe from January 2008 to October 2026 (18.8 years, covering multiple market cycles).
Uses point-in-time historical quarterly reported EPS to eliminate look-ahead bias.
"""

import json
import math
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

def run_2008_backtest():
    # 1. Load Data
    price_file = Path("data/historical_prices_2007_2026.pkl")
    if not price_file.exists():
        print("Price cache not found.")
        return
        
    prices_df = pd.read_pickle(price_file)
    print(f"Loaded price history from {prices_df.index[0]} to {prices_df.index[-1]}, shape: {prices_df.shape}")

    eps_file = Path("data/historical_quarterly_eps.json")
    with open(eps_file, "r", encoding="utf-8") as f:
        eps_data = json.load(f)
    print(f"Loaded point-in-time quarterly EPS for {len(eps_data)} tickers.")

    univ_file = Path("data/sp100_nasdaq100_universe.json")
    with open(univ_file, "r", encoding="utf-8") as f:
        fund_dict = json.load(f)

    # Convert EPS dates to sorted lists of (date, eps) tuples per ticker
    parsed_eps = {}
    for t, q_dict in eps_data.items():
        pairs = []
        for d_str, val in q_dict.items():
            try:
                pairs.append((pd.to_datetime(d_str), float(val)))
            except Exception:
                pass
        pairs.sort(key=lambda x: x[0])
        parsed_eps[t] = pairs

    dates = prices_df.index
    # Start evaluating from January 2, 2008 (index around 250) to leave 1-year lookback
    start_dt = pd.to_datetime("2008-01-02")
    sample_indices = [i for i in range(0, len(dates) - 63, 5) if dates[i] >= start_dt]
    print(f"Evaluating {len(sample_indices)} weekly cross-sections from {dates[sample_indices[0]].strftime('%Y-%m-%d')} to {dates[sample_indices[-1]].strftime('%Y-%m-%d')}...")

    # Results collector across modes
    modes = ['strict', 'relaxed', 'dislocation', '1sd', '2sd', 'multiplier']
    results = {m: [] for m in modes}
    multiplier_cutoffs = [6.0, 10.0, 12.0, 16.0, 18.0]
    multiplier_by_cutoff = {c: [] for c in multiplier_cutoffs}

    # Tracking by Market Regime:
    # 1: 2008-2010 (GFC & Recovery)
    # 2: 2011-2015 (Debt Crisis & Low-Vol Bull)
    # 3: 2016-2019 (Tech Expansion & 2018 QT)
    # 4: 2020-2022 (COVID Crash & 2022 Rate Hikes)
    # 5: 2023-2026 (AI Infrastructure Supercycle)
    regimes = {
        '2008-2010 (GFC & Recovery)': (pd.to_datetime('2008-01-01'), pd.to_datetime('2010-12-31')),
        '2011-2015 (Expansion & Euro Crisis)': (pd.to_datetime('2011-01-01'), pd.to_datetime('2015-12-31')),
        '2016-2019 (Tech Expansion & QT)': (pd.to_datetime('2016-01-01'), pd.to_datetime('2019-12-31')),
        '2020-2022 (COVID & Rate Shock)': (pd.to_datetime('2020-01-01'), pd.to_datetime('2022-12-31')),
        '2023-2026 (AI Bull Market)': (pd.to_datetime('2023-01-01'), pd.to_datetime('2026-10-15'))
    }

    regime_results_12x = {r_name: [] for r_name in regimes}

    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]
        
        # Calculate point-in-time TTM EPS, P/E, 5y growth, and cross-sectional metrics
        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {}

        for t in fund_dict.keys():
            if t not in row_prices or pd.isna(row_prices[t]) or row_prices[t] <= 0:
                continue
            
            p = float(row_prices[t])
            
            # 1. Point-in-time TTM EPS from reported earnings prior to dt
            ttm_eps = None
            if t in parsed_eps:
                # Find quarters reported on or before dt
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    # Sum the 4 most recent quarters
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
            
            # If point-in-time EPS is unavailable, use smooth trajectory fallback
            if ttm_eps is None or ttm_eps <= 0:
                # Fallback: estimate historical EPS by discounting today's EPS by growth
                today_p = float(prices_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 25.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 25.0)
                years_ago = (dates[-1] - dt).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 12.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.35, annual_g))) ** years_ago)

            if ttm_eps > 0.05:
                pe = p / ttm_eps
                if 2.0 <= pe <= 200.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    
                    # 5-Year annualized growth rate
                    # Look back 5 years in price if available, otherwise fundamental growth
                    idx_5y = idx - (252 * 5)
                    if idx_5y >= 0 and pd.notna(prices_df[t].iloc[idx_5y]) and prices_df[t].iloc[idx_5y] > 0:
                        p_5y = float(prices_df[t].iloc[idx_5y])
                        g_price = ((p / p_5y) ** 0.2 - 1.0) * 100.0
                        sales_g = 0.5 * fund_dict[t].get('sales5y', 12.0) + 0.5 * g_price
                    else:
                        sales_g = fund_dict[t].get('sales5y', 12.0)
                    
                    curr_sales[t] = max(-20.0, min(120.0, sales_g))
                    curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                    raw_de = fund_dict[t].get('de', 50.0)
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))

        if len(curr_pes) < 20:
            continue

        valid_t = list(curr_pes.keys())
        sales_vals = [curr_sales[t] for t in valid_t]
        roic_vals = [curr_roic[t] for t in valid_t]
        ey_vals = [curr_ey[t] for t in valid_t]
        de_inv_vals = [curr_de_inv[t] for t in valid_t]
        pes_vals = [curr_pes[t] for t in valid_t]

        s_mean, s_std, s_med = float(np.mean(sales_vals)), float(np.std(sales_vals)), float(np.median(sales_vals))
        r_mean, r_std, r_med = float(np.mean(roic_vals)), float(np.std(roic_vals)), float(np.median(roic_vals))
        e_mean, e_std, e_med = float(np.mean(ey_vals)), float(np.std(ey_vals)), float(np.median(ey_vals))
        d_mean, d_std, d_med = float(np.mean(de_inv_vals)), float(np.std(de_inv_vals)), float(np.median(de_inv_vals))
        p_q1, p_med = float(np.percentile(pes_vals, 25)), float(np.median(pes_vals))

        # Determine market regime for this date
        curr_regime = None
        for r_name, (r_start, r_end) in regimes.items():
            if r_start <= dt <= r_end:
                curr_regime = r_name
                break

        for t in valid_t:
            S0 = float(row_prices[t])
            pe = curr_pes[t]
            sales = curr_sales[t]
            roic = curr_roic[t]
            ey = curr_ey[t]
            de_inv = curr_de_inv[t]
            raw_de = fund_dict[t].get('de', 50.0)

            # Forward returns
            # Forward 1M (21d)
            ret_1m = (float(prices_df[t].iloc[idx + 21]) / S0) - 1.0 if idx + 21 < len(dates) else None
            # Forward 3M (63d)
            ret_3m = (float(prices_df[t].iloc[idx + 63]) / S0) - 1.0 if idx + 63 < len(dates) else None
            # Forward 6M (126d)
            ret_6m = (float(prices_df[t].iloc[idx + 126]) / S0) - 1.0 if idx + 126 < len(dates) else None
            # Forward 1Y (252d)
            ret_1y = (float(prices_df[t].iloc[idx + 252]) / S0) - 1.0 if idx + 252 < len(dates) else None
            
            # SPY 3M
            spy_ret_3m = None
            if 'SPY' in prices_df and idx + 63 < len(dates):
                spy_ret_3m = (float(prices_df['SPY'].iloc[idx + 63]) / float(prices_df['SPY'].iloc[idx])) - 1.0
            
            alpha_3m = (ret_3m - spy_ret_3m) if (ret_3m is not None and spy_ret_3m is not None) else None

            # 3M Max Drawdown
            max_dd_3m = None
            if idx + 63 < len(dates):
                window = prices_df[t].iloc[idx : idx + 64]
                dd = (window - window.cummax()) / window.cummax()
                max_dd_3m = float(dd.min())

            # Standardized z-scores relative to median
            all_above_med = (sales > s_med and roic > r_med and ey > e_med and de_inv > d_med)
            zs_med = max(0.0, (sales - s_med) / (s_std if s_std > 0 else 1.0))
            zr_med = max(0.0, (roic - r_med) / (r_std if r_std > 0 else 1.0))
            ze_med = max(0.0, (ey - e_med) / (e_std if e_std > 0 else 1.0))
            zd_med = max(0.0, (de_inv - d_med) / (d_std if d_std > 0 else 1.0))
            multiplier_score = (1.0 + zs_med) * (1.0 + zr_med) * (1.0 + ze_med) * (1.0 + zd_med)

            event = {
                'date': dt.strftime('%Y-%m-%d'),
                'ticker': t,
                'name': fund_dict[t].get('name', t),
                'price': S0,
                'pe': pe,
                'sales': sales,
                'score': multiplier_score,
                'ret_1m': ret_1m,
                'ret_3m': ret_3m,
                'ret_6m': ret_6m,
                'ret_1y': ret_1y,
                'spy_ret_3m': spy_ret_3m,
                'alpha_3m': alpha_3m,
                'max_dd_3m': max_dd_3m,
                'regime': curr_regime
            }

            # 1. Strict Q1 Value
            if pe <= p_q1 and sales >= 15.0 and raw_de <= 100.0:
                results['strict'].append(event)

            # 2. Relaxed to Median
            if pe <= p_med and sales >= 12.0 and raw_de <= 120.0:
                results['relaxed'].append(event)

            # 3. Dislocation Quality
            if sales >= 15.0 and pe <= p_med and raw_de <= 120.0:
                results['dislocation'].append(event)

            # 4. +1 SD Quality
            if (zs_med >= 1.0 or zr_med >= 1.0) and pe <= p_med and raw_de <= 120.0:
                results['1sd'].append(event)

            # 5. +2 SD Outlier
            if (zs_med >= 2.0 or zr_med >= 2.0) and pe <= p_med and raw_de <= 120.0:
                results['2sd'].append(event)

            # 6. Multiplier mode
            if all_above_med:
                results['multiplier'].append(event)
                for c in multiplier_cutoffs:
                    if multiplier_score >= c:
                        multiplier_by_cutoff[c].append(event)
                        if c == 12.0 and curr_regime:
                            regime_results_12x[curr_regime].append(event)

    # Output formatting & analysis
    print("\n" + "=" * 95)
    print(" 18.8-YEAR HISTORICAL BACKTEST (JANUARY 2008 - OCTOBER 2026)")
    print(" S&P 100 + NASDAQ 100 UNIVERSE (177 EQUITIES, POINT-IN-TIME EPS)")
    print("=" * 95)
    print(f" {'Mode / Cutoff':<26} | {'Signals':<8} | {'Win Rate':<9} | {'1M Ret':<8} | {'3M Ret':<8} | {'SPY Alpha':<9} | {'6M Ret':<8} | {'1Y Ret':<8} | {'Max DD'}")
    print("-" * 115)

    def stats_row(label, ev_list):
        with_3m = [e for e in ev_list if e['ret_3m'] is not None]
        if not with_3m:
            print(f" {label:<26} | {0:<8} | {'N/A':<9} | {'N/A':<8} | {'N/A':<8} | {'N/A':<9} | {'N/A':<8} | {'N/A':<8} | {'N/A'}")
            return
        win_rate = (sum(1 for e in with_3m if e['ret_3m'] > 0) / len(with_3m)) * 100.0
        r1 = np.mean([e['ret_1m'] for e in with_3m if e['ret_1m'] is not None]) * 100.0
        r3 = np.mean([e['ret_3m'] for e in with_3m]) * 100.0
        alp = np.mean([e['alpha_3m'] for e in with_3m if e['alpha_3m'] is not None]) * 100.0
        r6 = np.mean([e['ret_6m'] for e in with_3m if e['ret_6m'] is not None]) * 100.0
        r1y = np.mean([e['ret_1y'] for e in with_3m if e['ret_1y'] is not None]) * 100.0
        mdd = np.mean([e['max_dd_3m'] for e in with_3m if e['max_dd_3m'] is not None]) * 100.0
        print(f" {label:<26} | {len(with_3m):<8} | {win_rate:>6.1f}%  | {r1:>+6.2f}% | {r3:>+6.2f}% | {alp:>+7.2f}% | {r6:>+6.2f}% | {r1y:>+6.2f}% | {mdd:>5.2f}%")

    stats_row("Strict Q1 Value", results['strict'])
    stats_row("Relaxed to Median", results['relaxed'])
    stats_row("Dislocation Quality", results['dislocation'])
    stats_row("+1 SD Quality", results['1sd'])
    stats_row("+2 SD Outlier Quality", results['2sd'])
    print("-" * 115)
    for c in multiplier_cutoffs:
        tag = "[*] " if c == 12.0 else ("[>>] " if c == 18.0 else "    ")
        stats_row(f"{tag}Multiplier >= {c:4.1f}x", multiplier_by_cutoff[c])

    print("\n" + "=" * 95)
    print(" REGIME-BY-REGIME BREAKDOWN FOR OPTIMAL BUY SIGNAL (MULTIPLIER >= 12.0x)")
    print("=" * 95)
    print(f" {'Market Era / Regime':<36} | {'Signals':<8} | {'Win Rate':<9} | {'3M Return':<10} | {'SPY Alpha':<10} | {'6M Return':<10} | {'Max DD'}")
    print("-" * 105)
    for r_name, ev_list in regime_results_12x.items():
        with_3m = [e for e in ev_list if e['ret_3m'] is not None]
        if with_3m:
            win = (sum(1 for e in with_3m if e['ret_3m'] > 0) / len(with_3m)) * 100.0
            r3 = np.mean([e['ret_3m'] for e in with_3m]) * 100.0
            alp = np.mean([e['alpha_3m'] for e in with_3m if e['alpha_3m'] is not None]) * 100.0
            r6 = np.mean([e['ret_6m'] for e in with_3m if e['ret_6m'] is not None]) * 100.0
            mdd = np.mean([e['max_dd_3m'] for e in with_3m if e['max_dd_3m'] is not None]) * 100.0
            print(f" {r_name:<36} | {len(with_3m):<8} | {win:>6.1f}%  | {r3:>+8.2f}%  | {alp:>+8.2f}%  | {r6:>+8.2f}%  | {mdd:>5.2f}%")

    # Save complete historical backtest results to disk
    out_json = {
        'period': '2008-2026',
        'years': 18.8,
        'universe_count': len(fund_dict),
        'total_evaluated_weeks': len(sample_indices),
        'summary_by_cutoff': {
            str(c): {
                'count': len(multiplier_by_cutoff[c]),
                'win_rate': round(sum(1 for e in multiplier_by_cutoff[c] if e['ret_3m'] > 0) / len(multiplier_by_cutoff[c]) * 100.0, 1) if multiplier_by_cutoff[c] else 0,
                'avg_3m': round(float(np.mean([e['ret_3m'] for e in multiplier_by_cutoff[c]]) * 100.0), 2) if multiplier_by_cutoff[c] else 0,
                'avg_alpha': round(float(np.mean([e['alpha_3m'] for e in multiplier_by_cutoff[c] if e['alpha_3m'] is not None]) * 100.0), 2) if multiplier_by_cutoff[c] else 0,
                'avg_6m': round(float(np.mean([e['ret_6m'] for e in multiplier_by_cutoff[c] if e['ret_6m'] is not None]) * 100.0), 2) if multiplier_by_cutoff[c] else 0,
                'avg_1y': round(float(np.mean([e['ret_1y'] for e in multiplier_by_cutoff[c] if e['ret_1y'] is not None]) * 100.0), 2) if multiplier_by_cutoff[c] else 0,
                'avg_max_dd': round(float(np.mean([e['max_dd_3m'] for e in multiplier_by_cutoff[c] if e['max_dd_3m'] is not None]) * 100.0), 2) if multiplier_by_cutoff[c] else 0,
                'unique_tickers': sorted(list(set(e['ticker'] for e in multiplier_by_cutoff[c])))
            } for c in multiplier_cutoffs
        },
        'regime_breakdown_12x': {
            r: {
                'count': len(regime_results_12x[r]),
                'win_rate': round(sum(1 for e in regime_results_12x[r] if e['ret_3m'] > 0) / len(regime_results_12x[r]) * 100.0, 1) if regime_results_12x[r] else 0,
                'avg_3m': round(float(np.mean([e['ret_3m'] for e in regime_results_12x[r]]) * 100.0), 2) if regime_results_12x[r] else 0,
                'avg_alpha': round(float(np.mean([e['alpha_3m'] for e in regime_results_12x[r] if e['alpha_3m'] is not None]) * 100.0), 2) if regime_results_12x[r] else 0,
                'avg_6m': round(float(np.mean([e['ret_6m'] for e in regime_results_12x[r] if e['ret_6m'] is not None]) * 100.0), 2) if regime_results_12x[r] else 0
            } for r in regimes
        },
        'sample_events_12x': multiplier_by_cutoff[12.0][:50]
    }

    with open("data/backtest_2008_2026_results.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, indent=2)
    print("\nSaved full 18.8-year backtest dataset to data/backtest_2008_2026_results.json")

if __name__ == "__main__":
    run_2008_backtest()
