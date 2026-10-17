"""Empirical evaluation of Munger Multiplier (M >= 10.0x and M >= 12.0x)
under each of the competing Debt/Solvency Pillar formulas.
"""

import json
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    prices_df = pd.read_pickle('data/historical_prices_2007_2026.pkl')
    with open('data/historical_quarterly_eps.json', 'r', encoding='utf-8') as f:
        eps_data = json.load(f)
    with open('data/sp100_nasdaq100_universe.json', 'r', encoding='utf-8') as f:
        fund_dict = json.load(f)
    with open('data/universe_debt_metrics.json', 'r', encoding='utf-8') as f:
        debt_metrics = json.load(f)

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
    start_dt = pd.to_datetime('2008-01-02')
    sample_indices = [i for i in range(0, len(dates) - 63, 5) if dates[i] >= start_dt]

    solv_modes = [
        'Book D/E',
        'Net Debt / EBITDA',
        'Debt / Market Cap',
        'Net Debt / FCF',
        'Dual Solvency'
    ]

    multiplier_results_12x = {m: [] for m in solv_modes}
    multiplier_results_10x = {m: [] for m in solv_modes}

    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]
        
        curr_pes, curr_ey, curr_sales, curr_roic = {}, {}, {}, {}
        solv_vals = {m: {} for m in solv_modes}
        
        for t in fund_dict.keys():
            if t not in row_prices or pd.isna(row_prices[t]) or row_prices[t] <= 0:
                continue
            p = float(row_prices[t])
            
            # Point-in-time TTM EPS
            ttm_eps = None
            if t in parsed_eps:
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
            if ttm_eps is None or ttm_eps <= 0.05:
                continue
            pe = p / ttm_eps
            if not (2.0 <= pe <= 200.0):
                continue
            
            curr_pes[t] = pe
            curr_ey[t] = 1.0 / pe
            curr_sales[t] = fund_dict[t].get('sales5y', 12.0)
            curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
            
            d_info = debt_metrics.get(t, {})
            # 1. Book D/E inverted
            raw_de = d_info.get('debtToEquity', 50.0) or 50.0
            solv_vals['Book D/E'][t] = 100.0 / (10.0 + max(0.0, float(raw_de)))
            
            # 2. Net Debt / EBITDA inverted
            nd_eb = d_info.get('net_debt_to_ebitda', 1.0) or 1.0
            solv_vals['Net Debt / EBITDA'][t] = 10.0 / (2.0 + max(0.0, float(nd_eb)))
            
            # 3. Debt / Market Cap inverted
            td = d_info.get('totalDebt', 0) or 0
            mc_today = d_info.get('marketCap', 1) or 1
            p_today = float(prices_df[t].dropna().iloc[-1])
            mc_hist = mc_today * (p / (p_today if p_today > 0 else p))
            d_mc = (td / mc_hist) if mc_hist > 0 else 0.15
            solv_vals['Debt / Market Cap'][t] = 100.0 / (5.0 + max(0.0, d_mc * 100.0))
            
            # 4. Net Debt / FCF inverted
            nd_fcf = d_info.get('net_debt_to_fcf', 1.5) or 1.5
            solv_vals['Net Debt / FCF'][t] = 10.0 / (2.0 + max(0.0, float(nd_fcf)))
            
            # 5. Dual Solvency composite
            solv_vals['Dual Solvency'][t] = 0.5 * solv_vals['Net Debt / EBITDA'][t] + 0.5 * (solv_vals['Debt / Market Cap'][t] / 10.0)

        if len(curr_pes) < 25:
            continue
            
        valid_t = list(curr_pes.keys())
        s_med, s_std = float(np.median([curr_sales[t] for t in valid_t])), float(np.std([curr_sales[t] for t in valid_t]) or 1.0)
        r_med, r_std = float(np.median([curr_roic[t] for t in valid_t])), float(np.std([curr_roic[t] for t in valid_t]) or 1.0)
        e_med, e_std = float(np.median([curr_ey[t] for t in valid_t])), float(np.std([curr_ey[t] for t in valid_t]) or 1.0)
        
        spy_p0 = float(prices_df['SPY'].iloc[idx])
        spy_r3 = (float(prices_df['SPY'].iloc[idx + 63]) / spy_p0) - 1.0 if idx + 63 < len(dates) else None
        spy_r1 = (float(prices_df['SPY'].iloc[idx + 252]) / spy_p0) - 1.0 if idx + 252 < len(dates) else None

        for m in solv_modes:
            m_vals = [solv_vals[m][t] for t in valid_t]
            m_med, m_std = float(np.median(m_vals)), float(np.std(m_vals) or 1.0)
            
            for t in valid_t:
                sales = curr_sales[t]
                roic = curr_roic[t]
                ey = curr_ey[t]
                s_val = solv_vals[m][t]
                
                # Conjunctive condition: all 4 must exceed median
                if not (sales > s_med and roic > r_med and ey > e_med and s_val > m_med):
                    continue
                    
                zs = max(0.0, (sales - s_med) / s_std)
                zr = max(0.0, (roic - r_med) / r_std)
                ze = max(0.0, (ey - e_med) / e_std)
                zd = max(0.0, (s_val - m_med) / m_std)
                
                score = (1.0 + zs) * (1.0 + zr) * (1.0 + ze) * (1.0 + zd)
                
                S0 = float(row_prices[t])
                r3 = (float(prices_df[t].iloc[idx + 63]) / S0) - 1.0 if idx + 63 < len(dates) else None
                r1 = (float(prices_df[t].iloc[idx + 252]) / S0) - 1.0 if idx + 252 < len(dates) else None
                a3 = (r3 - spy_r3) if (r3 is not None and spy_r3 is not None) else None
                a1 = (r1 - spy_r1) if (r1 is not None and spy_r1 is not None) else None
                
                ev = {'t': t, 'score': score, 'r3': r3, 'r1': r1, 'a3': a3, 'a1': a1}
                if score >= 10.0:
                    multiplier_results_10x[m].append(ev)
                if score >= 12.0:
                    multiplier_results_12x[m].append(ev)

    print("\n" + "=" * 105)
    print(" MUNGER MULTIPLIER (M >= 12.0x): HEAD-TO-HEAD DEBT PILLAR SHOWDOWN")
    print("=" * 105)
    print(f" {'Solvency Pillar Formula':<26} | {'Signals':<7} | {'3M WinRate':<10} | {'3M Return':<10} | {'3M Alpha':<10} | {'1Y Return':<10} | {'1Y Alpha'}")
    print("-" * 105)
    for m in solv_modes:
        evs = multiplier_results_12x[m]
        with_3m = [e for e in evs if e['r3'] is not None]
        with_1y = [e for e in evs if e['r1'] is not None]
        wr = (sum(1 for e in with_3m if e['r3'] > 0) / len(with_3m) * 100.0) if with_3m else 0
        r3 = float(np.mean([e['r3'] for e in with_3m]) * 100.0) if with_3m else 0
        a3 = float(np.mean([e['a3'] for e in with_3m if e['a3'] is not None]) * 100.0) if with_3m else 0
        r1 = float(np.mean([e['r1'] for e in with_1y]) * 100.0) if with_1y else 0
        a1 = float(np.mean([e['a1'] for e in with_1y if e['a1'] is not None]) * 100.0) if with_1y else 0
        tag = "[WIN] " if m == "Net Debt / EBITDA" else "      "
        print(f" {tag}{m:<20} | {len(with_3m):<7} | {wr:>8.1f}%  | {r3:>+8.2f}%  | {a3:>+8.2f}%  | {r1:>+8.2f}%  | {a1:>+8.2f}%")

    print("\n" + "=" * 105)
    print(" MUNGER MULTIPLIER (M >= 10.0x): BROAD STRIKE-ZONE DEBT PILLAR SHOWDOWN")
    print("=" * 105)
    print(f" {'Solvency Pillar Formula':<26} | {'Signals':<7} | {'3M WinRate':<10} | {'3M Return':<10} | {'3M Alpha':<10} | {'1Y Return':<10} | {'1Y Alpha'}")
    print("-" * 105)
    for m in solv_modes:
        evs = multiplier_results_10x[m]
        with_3m = [e for e in evs if e['r3'] is not None]
        with_1y = [e for e in evs if e['r1'] is not None]
        wr = (sum(1 for e in with_3m if e['r3'] > 0) / len(with_3m) * 100.0) if with_3m else 0
        r3 = float(np.mean([e['r3'] for e in with_3m]) * 100.0) if with_3m else 0
        a3 = float(np.mean([e['a3'] for e in with_3m if e['a3'] is not None]) * 100.0) if with_3m else 0
        r1 = float(np.mean([e['r1'] for e in with_1y]) * 100.0) if with_1y else 0
        a1 = float(np.mean([e['a1'] for e in with_1y if e['a1'] is not None]) * 100.0) if with_1y else 0
        tag = "[WIN] " if m == "Net Debt / EBITDA" else "      "
        print(f" {tag}{m:<20} | {len(with_3m):<7} | {wr:>8.1f}%  | {r3:>+8.2f}%  | {a3:>+8.2f}%  | {r1:>+8.2f}%  | {a1:>+8.2f}%")

if __name__ == '__main__':
    main()
