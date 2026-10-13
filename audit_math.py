"""Audit math script to inspect year-by-year portfolio progression.
"""

import pickle
import json
import numpy as np
import pandas as pd

def audit():
    with open('data/historical_prices_1970_2026.pkl', 'rb') as f:
        ohlcv = pickle.load(f)
    with open('data/sp100_nasdaq100_universe.json', 'r', encoding='utf-8') as f:
        fund_dict = json.load(f)
    with open('data/historical_quarterly_eps.json', 'r', encoding='utf-8') as f:
        eps_data = json.load(f)

    parsed_eps = {}
    for t, q_dict in eps_data.items():
        pairs = []
        for d_str, val in q_dict.items():
            try: pairs.append((pd.to_datetime(d_str), float(val)))
            except: pass
        pairs.sort(key=lambda x: x[0])
        parsed_eps[t] = pairs

    gspc_df = ohlcv['^GSPC']
    start_dt = pd.to_datetime('2008-01-02')
    end_dt = pd.to_datetime('2026-10-12')
    common_dates = gspc_df.loc[start_dt:end_dt].index

    tickers = [t for t in fund_dict.keys() if t in ohlcv]
    close_df = pd.DataFrame({t: ohlcv[t]['Close'].reindex(common_dates) for t in tickers})
    gspc_close = gspc_df['Close'].reindex(common_dates).ffill()

    cycle_indices = list(range(0, len(common_dates), 252))
    if cycle_indices[-1] != len(common_dates) - 1:
        cycle_indices.append(len(common_dates) - 1)

    print("=" * 80)
    print("YEAR-BY-YEAR AUDIT: 2008 TO 2026 (M >= 4.0x)")
    print("=" * 80)

    cur_pre = 1000.0
    cur_post = 1000.0
    spy_pre = 1000.0

    for c in range(len(cycle_indices) - 1):
        i0 = cycle_indices[c]
        i1 = cycle_indices[c+1]
        d0 = common_dates[i0]
        d1 = common_dates[i1]

        # Calculate cross-sectional scores at d0
        row_p = close_df.loc[d0]
        curr_pes, curr_ey, curr_sales, curr_roic, curr_de_inv = {}, {}, {}, {}, {}

        for t in tickers:
            p = float(row_p[t]) if pd.notna(row_p[t]) else 0.0
            if p <= 0: continue
            ttm_eps = None
            if t in parsed_eps:
                past_q = [q for q in parsed_eps[t] if q[0] <= d0]
                if len(past_q) >= 4:
                    ttm_eps = sum(q[1] for q in past_q[-4:])
            if ttm_eps is None or ttm_eps <= 0:
                today_p = float(close_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 20.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 20.0)
                years_ago = (common_dates[-1] - d0).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 10.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.25, annual_g))) ** years_ago)

            if ttm_eps > 0.02:
                pe = p / ttm_eps
                if 2.0 <= pe <= 150.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    curr_sales[t] = fund_dict[t].get('sales5y', 10.0)
                    curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                    raw_de = fund_dict[t].get('de', 50.0)
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))

        valid_t = list(curr_pes.keys())
        s_vals = [curr_sales[t] for t in valid_t]
        r_vals = [curr_roic[t] for t in valid_t]
        e_vals = [curr_ey[t] for t in valid_t]
        d_vals = [curr_de_inv[t] for t in valid_t]

        s_std, s_med = float(np.std(s_vals)), float(np.median(s_vals))
        r_std, r_med = float(np.std(r_vals)), float(np.median(r_vals))
        e_std, e_med = float(np.std(e_vals)), float(np.median(e_vals))
        d_std, d_med = float(np.std(d_vals)), float(np.median(d_vals))

        qual = []
        for t in valid_t:
            zg = (curr_sales[t] - s_med) / (s_std if s_std > 0 else 1.0)
            zr = (curr_roic[t] - r_med) / (r_std if r_std > 0 else 1.0)
            zy = (curr_ey[t] - e_med) / (e_std if e_std > 0 else 1.0)
            zs = (curr_de_inv[t] - d_med) / (d_std if d_std > 0 else 1.0)
            if zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0:
                m = (1.0 + zg) * (1.0 + zr) * (1.0 + zy) * (1.0 + zs)
                if m >= 4.0:
                    qual.append((t, m))

        qual.sort(key=lambda x: x[1], reverse=True)
        top_t = [x[0] for x in qual[:8]]

        rets = []
        for st in top_t:
            p0 = close_df[st].loc[d0]
            p1 = close_df[st].loc[d1]
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                rets.append((p1 / p0) - 1.0)

        ret_munger = float(np.mean(rets)) if rets else 0.03
        ret_spy = (gspc_close.loc[d1] / gspc_close.loc[d0]) - 1.0

        cur_pre *= (1.0 + ret_munger)
        gain = cur_post * ret_munger
        if gain > 0:
            cur_post += gain * (1.0 - 0.238)
        else:
            cur_post += gain

        spy_pre *= (1.0 + ret_spy)

        print(f"Cycle {c+1:2d} ({d0.strftime('%Y-%m-%d')} to {d1.strftime('%Y-%m-%d')}): Munger Ret: {ret_munger*100:+6.2f}% | SPY Ret: {ret_spy*100:+6.2f}% | Pre-Tax: ${cur_pre:10,.0f} | Post-LTCG: ${cur_post:8,.0f} | Holdings: {top_t}")

if __name__ == '__main__':
    audit()
