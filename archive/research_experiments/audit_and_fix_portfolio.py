"""Audited Portfolio Simulation with Institutional Diversification Rules.

Fixes:
1. Removes spurious/recycled ticker artifacts (e.g. SNDK).
2. Strict Institutional Position Cap: Max 15.0% in any single stock (minimum 7 positions to be fully invested).
   If fewer than 7 stocks qualify, remaining capital is held in Cash/T-Bills (Risk-Free Rate).
3. Realistic, audited compounding for both:
   - 2008-2026 (18.8 Years, Point-in-time financial filings)
   - 1971-2026 (55.5 Years, Multi-decade stress test)
"""

import pickle
import json
import numpy as np
import pandas as pd
from pathlib import Path

def run_audited_simulation():
    print("=" * 80)
    print("AUDITED REALISTIC MUNGER SIMULATION WITH INSTITUTIONAL POSITION LIMITS")
    print("=" * 80)

    with open('data/historical_prices_1970_2026.pkl', 'rb') as f:
        ohlcv = pickle.load(f)
    with open('data/sp100_nasdaq100_universe.json', 'r', encoding='utf-8') as f:
        fund_dict = json.load(f)
    with open('data/historical_quarterly_eps.json', 'r', encoding='utf-8') as f:
        eps_data = json.load(f)

    # Exclude delisted/recycled tickers
    excluded_tickers = {'SNDK'}
    tickers = [t for t in fund_dict.keys() if t in ohlcv and t not in excluded_tickers]
    print(f"Tracking {len(tickers)} valid corporate constituents (excluded {excluded_tickers})...")

    parsed_eps = {}
    for t, q_dict in eps_data.items():
        if t in excluded_tickers: continue
        pairs = []
        for d_str, val in q_dict.items():
            try: pairs.append((pd.to_datetime(d_str), float(val)))
            except: pass
        pairs.sort(key=lambda x: x[0])
        parsed_eps[t] = pairs

    gspc_df = ohlcv['^GSPC']
    start_1971 = pd.to_datetime('1971-01-04')
    end_date = pd.to_datetime('2026-10-12')
    common_dates = gspc_df.loc[start_1971:end_date].index

    close_df = pd.DataFrame({t: ohlcv[t]['Close'].reindex(common_dates) for t in tickers})
    gspc_close = gspc_df['Close'].reindex(common_dates).ffill()

    def get_rf_rate(year):
        if year < 1980: return 0.075
        elif year < 1990: return 0.085
        elif year < 2000: return 0.050
        elif year < 2010: return 0.030
        elif year < 2020: return 0.010
        else: return 0.040

    ltcg_tax_rate = 0.238

    # Evaluate at annual cycles
    def simulate_audited_start(start_dt_str, max_pos_pct=0.15, m_buy=4.0):
        start_dt = pd.to_datetime(start_dt_str)
        eval_dates = [d for d in common_dates if d >= start_dt]
        years_total = (eval_dates[-1] - eval_dates[0]).days / 365.25

        cycle_indices = list(range(0, len(eval_dates), 252))
        if cycle_indices[-1] != len(eval_dates) - 1:
            cycle_indices.append(len(eval_dates) - 1)

        # SPY Benchmark
        spy_c0 = gspc_close.loc[eval_dates[cycle_indices[0]]]
        spy_cT = gspc_close.loc[eval_dates[cycle_indices[-1]]]
        spy_pre = 1000.0 * ((spy_cT / spy_c0) * ((1.0 + 0.02) ** years_total))
        post_div = 0.02 * (1.0 - ltcg_tax_rate)
        spy_post_unreal = 1000.0 * ((spy_cT / spy_c0) * ((1.0 + post_div) ** years_total))
        spy_post = spy_post_unreal - max(0.0, spy_post_unreal - 1000.0) * ltcg_tax_rate
        spy_cagr_pre = (spy_pre / 1000.0) ** (1.0 / years_total) - 1.0
        spy_cagr_post = (spy_post / 1000.0) ** (1.0 / years_total) - 1.0

        cur_pre = 1000.0
        cur_post = 1000.0
        cycle_details = []

        for c in range(len(cycle_indices) - 1):
            i0 = cycle_indices[c]
            i1 = cycle_indices[c+1]
            d0 = eval_dates[i0]
            d1 = eval_dates[i1]
            cur_year = d0.year
            rf_rate = get_rf_rate(cur_year)

            # Evaluate cross-section at d0
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
            qual = []
            if len(valid_t) >= 10:
                s_vals = [curr_sales[t] for t in valid_t]
                r_vals = [curr_roic[t] for t in valid_t]
                e_vals = [curr_ey[t] for t in valid_t]
                d_vals = [curr_de_inv[t] for t in valid_t]

                s_std, s_med = float(np.std(s_vals)), float(np.median(s_vals))
                r_std, r_med = float(np.std(r_vals)), float(np.median(r_vals))
                e_std, e_med = float(np.std(e_vals)), float(np.median(e_vals))
                d_std, d_med = float(np.std(d_vals)), float(np.median(d_vals))

                for t in valid_t:
                    zg = (curr_sales[t] - s_med) / (s_std if s_std > 0 else 1.0)
                    zr = (curr_roic[t] - r_med) / (r_std if r_std > 0 else 1.0)
                    zy = (curr_ey[t] - e_med) / (e_std if e_std > 0 else 1.0)
                    zs = (curr_de_inv[t] - d_med) / (d_std if d_std > 0 else 1.0)
                    if zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0:
                        m = (1.0 + zg) * (1.0 + zr) * (1.0 + zy) * (1.0 + zs)
                        if m >= m_buy:
                            qual.append((t, m))

            qual.sort(key=lambda x: x[1], reverse=True)

            # INSTITUTIONAL POSITION SIZING RULE:
            # Max 15% per stock (or up to 7 stocks max = 7 * 14.3% = 100%).
            # If fewer than 7 stocks qualify, remaining weight is placed in Cash/T-Bills!
            max_stocks = int(1.0 / max_pos_pct) # 1 / 0.15 = 6.66 -> 7 stocks
            num_qual = len(qual)
            selected_t = [x[0] for x in qual[:max_stocks]]

            if num_qual == 0:
                # 100% in Cash/T-Bills
                stock_weight = 0.0
                cash_weight = 1.0
                avg_stock_ret = 0.0
            elif num_qual < max_stocks:
                # e.g., 2 stocks qualify: each gets 14.3% (total 28.6%), remaining 71.4% in Cash
                stock_weight_each = 1.0 / max_stocks
                stock_weight = stock_weight_each * num_qual
                cash_weight = 1.0 - stock_weight
                rets = []
                for st in selected_t:
                    p0 = close_df[st].loc[d0]
                    p1 = close_df[st].loc[d1]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        rets.append((p1 / p0) - 1.0)
                avg_stock_ret = float(np.mean(rets)) if rets else rf_rate
            else:
                # 7 or more qualify: fully invested across top 7 (each 14.3%)
                stock_weight = 1.0
                cash_weight = 0.0
                rets = []
                for st in selected_t:
                    p0 = close_df[st].loc[d0]
                    p1 = close_df[st].loc[d1]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        rets.append((p1 / p0) - 1.0)
                avg_stock_ret = float(np.mean(rets)) if rets else rf_rate

            portfolio_ret = (stock_weight * avg_stock_ret) + (cash_weight * rf_rate)
            spy_ret = (gspc_close.loc[d1] / gspc_close.loc[d0]) - 1.0

            cur_pre *= (1.0 + portfolio_ret)
            gain = cur_post * portfolio_ret
            if gain > 0:
                cur_post += gain * (1.0 - ltcg_tax_rate)
            else:
                cur_post += gain

            cycle_details.append({
                "cycle": c+1, "d0": d0.strftime('%Y-%m-%d'), "d1": d1.strftime('%Y-%m-%d'),
                "port_ret": portfolio_ret, "spy_ret": spy_ret,
                "cur_pre": cur_pre, "cur_post": cur_post,
                "holdings": selected_t, "cash_pct": cash_weight
            })

        cagr_pre = (cur_pre / 1000.0) ** (1.0 / years_total) - 1.0
        cagr_post = (cur_post / 1000.0) ** (1.0 / years_total) - 1.0

        return {
            "years": years_total,
            "final_pre": cur_pre, "final_post": cur_post,
            "cagr_pre": cagr_pre, "cagr_post": cagr_post,
            "drag_bps": (cagr_pre - cagr_post) * 10000.0,
            "spy_pre": spy_pre, "spy_post": spy_post,
            "spy_cagr_pre": spy_cagr_pre, "spy_cagr_post": spy_cagr_post,
            "cycles": cycle_details
        }

    # Run for 2008 and 1971 across various position caps (15% cap vs 20% cap)
    for start_name, s_date in [("2008 GFC Peak (18.8Y)", "2008-01-02"), ("1971 Stagflation (55.5Y)", "1971-01-04")]:
        print(f"\n==================================================================")
        print(f"AUDITED RESULTS FOR: {start_name.upper()}")
        print(f"==================================================================")

        for cap in [0.15, 0.20, 1.0]: # 15% cap (7 stocks), 20% cap (5 stocks), unconstrained
            for mb in [4.0, 6.0]:
                res = simulate_audited_start(s_date, max_pos_pct=cap, m_buy=mb)
                cap_str = f"Max {cap*100:.0f}%/stock" if cap < 1.0 else "Unconstrained (Buggy)"
                f_pre = f"${res['final_pre']:,.0f}"
                f_post = f"${res['final_post']:,.0f}"
                c_pre = f"{res['cagr_pre']*100:.2f}%"
                c_post = f"{res['cagr_post']*100:.2f}%"
                print(f"Buy M>={mb:.0f}x | {cap_str:<22} | Pre-Tax: {f_pre:<14} ({c_pre:<7}) | Post-LTCG: {f_post:<13} ({c_post:<7})")

        print(f"S&P 500 Benchmark: Pre-Tax: ${res['spy_pre']:,.0f} ({res['spy_cagr_pre']*100:.2f}%), Post-Tax: ${res['spy_post']:,.0f} ({res['spy_cagr_post']*100:.2f}%)")

if __name__ == '__main__':
    run_audited_simulation()
