"""Empirical analysis of 1-SD, 2-SD, and Multiplier Composite Extremized screening modes."""

import json
import numpy as np
import pandas as pd
import yfinance as yf
from typing import Dict, Any, List

from fidelity_control.backtester import EXTENDED_FUNDAMENTALS

def run_statistical_experiments(period: str = "2y"):
    tickers = list(EXTENDED_FUNDAMENTALS.keys())
    download_list = [t for t in tickers if t not in ['ROOTS', 'CRYPTO', 'T-Bills', 'VTI / VT']]
    
    print(" [*] Downloading price history for extended universe + SPY...")
    prices_df = yf.download(download_list + ['SPY'], period=period, interval='1d', progress=False)['Close']
    
    if prices_df.empty:
        raise RuntimeError("Failed to download price data.")

    base_prices = prices_df.iloc[-1]
    base_eps = {}
    base_bvps = {}
    for t in tickers:
        if t in base_prices and pd.notna(base_prices[t]) and t in EXTENDED_FUNDAMENTALS:
            p = float(base_prices[t])
            pe = EXTENDED_FUNDAMENTALS[t].get('pe', 25.0)
            pb = EXTENDED_FUNDAMENTALS[t].get('pb', 3.0)
            base_eps[t] = p / (pe if pe > 0 else 25.0)
            base_bvps[t] = p / (pb if pb > 0 else 3.0)

    dates = prices_df.index
    # Sample every 5 trading days (weekly cycle)
    sample_indices = range(0, len(dates) - 63, 5)

    # Containers for experiments
    results_1sd_all = []       # +1 SD across ALL 4 factors
    results_1sd_growth_val = []# +1 SD Growth & Quality with Value <= Median
    results_2sd_all = []       # +2 SD across ALL factors
    results_2sd_growth = []    # +2 SD Growth or Quality with Value <= Median
    multiplier_events = []     # Multiplier Composite Model: Prod(1 + Z) for all-above-median

    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]
        
        # Calculate daily cross-sectional metrics
        curr_pes = {}
        curr_ey = {} # Earnings yield E/P
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {} # Inverse leverage (lower debt = higher score)
        
        for t in tickers:
            if t in row_prices and pd.notna(row_prices[t]) and t in base_eps and base_eps[t] > 0:
                p = float(row_prices[t])
                pe = p / base_eps[t]
                if 2.0 <= pe <= 200.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    curr_sales[t] = EXTENDED_FUNDAMENTALS[t]['sales5y']
                    curr_roic[t] = EXTENDED_FUNDAMENTALS[t].get('roic_proxy', 0.15)
                    # We invert D/E so higher is better: e.g. 100 / (10 + DE)
                    de = EXTENDED_FUNDAMENTALS[t]['de']
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, de))

        if len(curr_pes) < 12:
            continue

        # Compute cross-sectional means, medians, and standard deviations
        valid_tickers = list(curr_pes.keys())
        
        # Sales
        sales_vals = [curr_sales[t] for t in valid_tickers]
        sales_mean, sales_std, sales_med = np.mean(sales_vals), np.std(sales_vals), np.median(sales_vals)
        
        # ROIC
        roic_vals = [curr_roic[t] for t in valid_tickers]
        roic_mean, roic_std, roic_med = np.mean(roic_vals), np.std(roic_vals), np.median(roic_vals)
        
        # Earnings Yield (Value)
        ey_vals = [curr_ey[t] for t in valid_tickers]
        ey_mean, ey_std, ey_med = np.mean(ey_vals), np.std(ey_vals), np.median(ey_vals)
        
        # Debt Safety (Higher is safer)
        de_inv_vals = [curr_de_inv[t] for t in valid_tickers]
        de_inv_mean, de_inv_std, de_inv_med = np.mean(de_inv_vals), np.std(de_inv_vals), np.median(de_inv_vals)
        
        # P/E Median
        pe_med = np.median([curr_pes[t] for t in valid_tickers])

        # Evaluate each ticker at this date
        for t in valid_tickers:
            price = float(row_prices[t])
            pe = curr_pes[t]
            ey = curr_ey[t]
            sales = curr_sales[t]
            roic = curr_roic[t]
            de_inv = curr_de_inv[t]
            raw_de = EXTENDED_FUNDAMENTALS[t]['de']

            # Calculate Z-scores (positive is always favorable)
            z_sales = (sales - sales_mean) / (sales_std if sales_std > 0 else 1.0)
            z_roic = (roic - roic_mean) / (roic_std if roic_std > 0 else 1.0)
            z_ey = (ey - ey_mean) / (ey_std if ey_std > 0 else 1.0) # Higher EY = cheaper PE = favorable
            z_de = (de_inv - de_inv_mean) / (de_inv_std if de_inv_std > 0 else 1.0) # Higher = safer debt

            # Forward returns
            p_future_1m = float(prices_df[t].iloc[idx + 21]) if idx + 21 < len(dates) else None
            p_future_3m = float(prices_df[t].iloc[idx + 63]) if idx + 63 < len(dates) else None
            p_future_6m = float(prices_df[t].iloc[idx + 126]) if idx + 126 < len(dates) else None
            
            ret_1m = (p_future_1m / price) - 1.0 if p_future_1m else None
            ret_3m = (p_future_3m / price) - 1.0 if p_future_3m else None
            ret_6m = (p_future_6m / price) - 1.0 if p_future_6m else None

            # 3M Max drawdown
            max_dd_3m = None
            if idx + 63 < len(dates):
                w_prices = prices_df[t].iloc[idx : idx + 64]
                peak = w_prices.cummax()
                dd = (w_prices - peak) / peak
                max_dd_3m = float(dd.min())

            # SPY 3M return
            spy_ret_3m = None
            if idx + 63 < len(dates) and 'SPY' in prices_df:
                spy_start = float(prices_df['SPY'].iloc[idx])
                spy_end = float(prices_df['SPY'].iloc[idx + 63])
                spy_ret_3m = (spy_end / spy_start) - 1.0

            alpha_3m = (ret_3m - spy_ret_3m) if (ret_3m is not None and spy_ret_3m is not None) else None

            event_data = {
                "date": dt.strftime("%Y-%m-%d"),
                "ticker": t,
                "name": EXTENDED_FUNDAMENTALS[t]['name'],
                "price": round(price, 2),
                "pe": round(pe, 1),
                "sales": round(sales, 1),
                "roic": round(roic * 100, 1),
                "de": round(raw_de, 1),
                "z_sales": round(z_sales, 2),
                "z_roic": round(z_roic, 2),
                "z_ey": round(z_ey, 2),
                "z_de": round(z_de, 2),
                "ret_1m": round(ret_1m * 100, 2) if ret_1m is not None else None,
                "ret_3m": round(ret_3m * 100, 2) if ret_3m is not None else None,
                "ret_6m": round(ret_6m * 100, 2) if ret_6m is not None else None,
                "spy_ret_3m": round(spy_ret_3m * 100, 2) if spy_ret_3m is not None else None,
                "alpha_3m": round(alpha_3m * 100, 2) if alpha_3m is not None else None,
                "max_dd_3m": round(max_dd_3m * 100, 2) if max_dd_3m is not None else None
            }

            # -------------------------------------------------------------
            # Trial 1A: +1 SD Overperformance across ALL 4 factors
            # (Sales >= +1 SD AND ROIC >= +1 SD AND Value >= +1 SD AND Debt Safety >= +1 SD)
            # -------------------------------------------------------------
            if z_sales >= 1.0 and z_roic >= 1.0 and z_ey >= 1.0 and z_de >= 1.0:
                results_1sd_all.append(event_data)

            # -------------------------------------------------------------
            # Trial 1B: +1 SD Overperformance in Quality/Growth with Valuation Discipline
            # (Sales >= +1 SD or ROIC >= +1 SD, with PE <= Median and Debt <= Median)
            # -------------------------------------------------------------
            if (z_sales >= 1.0 or z_roic >= 1.0) and pe <= pe_med and raw_de <= 120.0:
                results_1sd_growth_val.append(event_data)

            # -------------------------------------------------------------
            # Trial 2A: +2 SD Overperformance across ALL 4 factors
            # -------------------------------------------------------------
            if z_sales >= 2.0 and z_roic >= 2.0 and z_ey >= 2.0 and z_de >= 2.0:
                results_2sd_all.append(event_data)

            # -------------------------------------------------------------
            # Trial 2B: +2 SD Outlier Growth or Quality with Valuation Discipline
            # (Sales >= +2 SD or ROIC >= +2 SD, with PE <= Median and Debt <= Median)
            # -------------------------------------------------------------
            if (z_sales >= 2.0 or z_roic >= 2.0) and pe <= pe_med and raw_de <= 120.0:
                results_2sd_growth.append(event_data)

            # -------------------------------------------------------------
            # Trial 3: Extremized Multiplier Metric
            # Hurdle: ALL variables must be above median to qualify!
            # (Sales > sales_med, ROIC > roic_med, EY > ey_med [i.e. PE < pe_med], DE_inv > de_inv_med [DE < de_med])
            # Multiplier = (1 + Z_sales) * (1 + Z_roic) * (1 + Z_ey) * (1 + Z_de)
            # -------------------------------------------------------------
            if sales > sales_med and roic > roic_med and ey > ey_med and de_inv > de_inv_med:
                # Compute individual factor standard deviation multipliers relative to median
                # (since variable > median, Z_med >= 0)
                z_sales_med = max(0.0, (sales - sales_med) / sales_std) if sales_std > 0 else 0.0
                z_roic_med = max(0.0, (roic - roic_med) / roic_std) if roic_std > 0 else 0.0
                z_ey_med = max(0.0, (ey - ey_med) / ey_std) if ey_std > 0 else 0.0
                z_de_med = max(0.0, (de_inv - de_inv_med) / de_inv_std) if de_inv_std > 0 else 0.0

                m_sales = 1.0 + z_sales_med
                m_roic = 1.0 + z_roic_med
                m_ey = 1.0 + z_ey_med
                m_de = 1.0 + z_de_med

                composite_multiplier = m_sales * m_roic * m_ey * m_de

                e_mult = dict(event_data)
                e_mult.update({
                    "m_sales": round(m_sales, 2),
                    "m_roic": round(m_roic, 2),
                    "m_ey": round(m_ey, 2),
                    "m_de": round(m_de, 2),
                    "composite_score": round(composite_multiplier, 2)
                })
                multiplier_events.append(e_mult)

    # Statistical Aggregation Helper
    def summarize(events: List[Dict[str, Any]], title: str):
        total = len(events)
        tickers_unique = sorted(list(set(e['ticker'] for e in events)))
        if total == 0:
            return {
                "title": title,
                "total_signals": 0,
                "unique_tickers": [],
                "win_rate_3m": 0.0,
                "beat_spy_rate_3m": 0.0,
                "avg_1m": None,
                "avg_3m": None,
                "avg_6m": None,
                "avg_spy_3m": None,
                "avg_alpha_3m": None,
                "avg_max_dd": None
            }
        
        valid_3m = [e for e in events if e['ret_3m'] is not None]
        n_3m = len(valid_3m)
        win_rate = (sum(1 for e in valid_3m if e['ret_3m'] > 0) / n_3m * 100) if n_3m else 0.0
        beat_spy = (sum(1 for e in valid_3m if (e['alpha_3m'] or 0) > 0) / n_3m * 100) if n_3m else 0.0
        
        return {
            "title": title,
            "total_signals": total,
            "unique_tickers": tickers_unique,
            "win_rate_3m": round(win_rate, 1),
            "beat_spy_rate_3m": round(beat_spy, 1),
            "avg_1m": round(float(np.mean([e['ret_1m'] for e in valid_3m if e['ret_1m'] is not None])), 2) if valid_3m else None,
            "avg_3m": round(float(np.mean([e['ret_3m'] for e in valid_3m])), 2) if valid_3m else None,
            "avg_6m": round(float(np.mean([e['ret_6m'] for e in valid_3m if e['ret_6m'] is not None])), 2) if any(e['ret_6m'] is not None for e in valid_3m) else None,
            "avg_spy_3m": round(float(np.mean([e['spy_ret_3m'] for e in valid_3m if e['spy_ret_3m'] is not None])), 2) if valid_3m else None,
            "avg_alpha_3m": round(float(np.mean([e['alpha_3m'] for e in valid_3m if e['alpha_3m'] is not None])), 2) if valid_3m else None,
            "avg_max_dd": round(float(np.mean([e['max_dd_3m'] for e in valid_3m if e['max_dd_3m'] is not None])), 2) if valid_3m else None
        }

    summary_1sd_all = summarize(results_1sd_all, "+1 SD Across ALL 4 Factors Simultaneously")
    summary_1sd_growth_val = summarize(results_1sd_growth_val, "+1 SD Growth/Quality with Valuation <= Median")
    summary_2sd_all = summarize(results_2sd_all, "+2 SD Across ALL 4 Factors Simultaneously")
    summary_2sd_growth = summarize(results_2sd_growth, "+2 SD Outlier Growth/Quality with Valuation <= Median")

    # Multiplier Score Cutoff Analysis
    # Let's test score thresholds: >= 2.0, >= 4.0, >= 6.0, >= 8.0, >= 12.0
    multiplier_thresholds = [1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0]
    cutoff_analysis = []
    for thresh in multiplier_thresholds:
        subset = [e for e in multiplier_events if e['composite_score'] >= thresh]
        s = summarize(subset, f"Multiplier Score >= {thresh}x")
        s['threshold'] = thresh
        cutoff_analysis.append(s)

    return {
        "summary_1sd_all": summary_1sd_all,
        "summary_1sd_growth_val": summary_1sd_growth_val,
        "summary_2sd_all": summary_2sd_all,
        "summary_2sd_growth": summary_2sd_growth,
        "multiplier_events_count": len(multiplier_events),
        "cutoff_analysis": cutoff_analysis,
        "sample_multiplier_events": sorted(multiplier_events, key=lambda x: x['composite_score'], reverse=True)[:25]
    }


if __name__ == "__main__":
    res = run_statistical_experiments()
    print("\n" + "=" * 90)
    print(" EXPERIMENT 1: +1 STANDARD DEVIATION OVERPERFORMANCE")
    print("=" * 90)
    print("A. Simultaneous +1 SD Across ALL 4 Factors (Growth, Quality, Value, Debt Safety):")
    print(json.dumps(res['summary_1sd_all'], indent=2))
    print("\nB. +1 SD Growth/Quality with Valuation <= Median:")
    print(json.dumps(res['summary_1sd_growth_val'], indent=2))

    print("\n" + "=" * 90)
    print(" EXPERIMENT 2: +2 STANDARD DEVIATIONS OVERPERFORMANCE")
    print("=" * 90)
    print("A. Simultaneous +2 SD Across ALL 4 Factors:")
    print(json.dumps(res['summary_2sd_all'], indent=2))
    print("\nB. +2 SD Outlier Growth/Quality with Valuation <= Median:")
    print(json.dumps(res['summary_2sd_growth'], indent=2))

    print("\n" + "=" * 90)
    print(" EXPERIMENT 3: EXTREMIZED MULTIPLIER METRIC: Prod(1 + Z_med)")
    print(" (Prerequisite: ALL variables must be above median to qualify)")
    print(" Total Qualifying Base Events:", res['multiplier_events_count'])
    print("=" * 90)
    print(f" {'Cutoff Threshold':<20} | {'Signals':<8} | {'Tickers':<18} | {'Win Rate 3M':<12} | {'3M Ret':<8} | {'SPY 3M':<8} | {'Alpha':<8} | {'6M Ret':<8} | {'Max DD':<8}")
    print("-" * 105)
    for c in res['cutoff_analysis']:
        t_str = f">={c['threshold']}x"
        tickers_str = ", ".join(c['unique_tickers'][:4])
        r3m = f"{c['avg_3m']:+.1f}%" if c['avg_3m'] is not None else "N/A"
        spy3 = f"{c['avg_spy_3m']:+.1f}%" if c['avg_spy_3m'] is not None else "N/A"
        alp = f"{c['avg_alpha_3m']:+.1f}%" if c['avg_alpha_3m'] is not None else "N/A"
        r6m = f"{c['avg_6m']:+.1f}%" if c['avg_6m'] is not None else "N/A"
        dd = f"{c['avg_max_dd']:.1f}%" if c['avg_max_dd'] is not None else "N/A"
        print(f" {t_str:<20} | {c['total_signals']:<8} | {tickers_str:<18} | {c['win_rate_3m']:>5.1f}%      | {r3m:>8} | {spy3:>8} | {alp:>8} | {r6m:>8} | {dd:>8}")

    print("\n Top 10 Highest-Scoring Extremized Events in History:")
    print(f" {'Date':<10} | {'Ticker':<5} | {'Company Name':<20} | {'Price':<8} | {'P/E':<5} | {'Score':<8} | {'1M Ret':<8} | {'3M Ret':<8} | {'Alpha':<8}")
    print("-" * 88)
    for e in res['sample_multiplier_events'][:12]:
        r1m = f"{e['ret_1m']:+.1f}%" if e['ret_1m'] is not None else "N/A"
        r3m = f"{e['ret_3m']:+.1f}%" if e['ret_3m'] is not None else "N/A"
        alp = f"{e['alpha_3m']:+.1f}%" if e['alpha_3m'] is not None else "N/A"
        print(f" {e['date']:<10} | {e['ticker']:<5} | {e['name'][:20]:<20} | ${e['price']:>7.2f} | {e['pe']:>5.1f} | {e['composite_score']:>7.2f}x | {r1m:>8} | {r3m:>8} | {alp:>8}")
