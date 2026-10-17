"""55.5-Year Historical MOC 4-Factor Backtest (1971 - 2026).

Evaluates the MUNGER 4-Factor System with Market-On-Close (MOC) execution
across the entire available history from January 1971 to October 2026
(55.5 years, ~2,800 weekly cross-sections, 14,315 trading days)
against the S&P 500 Benchmark (^GSPC).
"""

import pickle
import json
from pathlib import Path
import numpy as np
import pandas as pd

def run_1971_moc():
    print("Loading 1970-2026 price history...")
    with open('data/historical_prices_1970_2026.pkl', 'rb') as f:
        ohlcv_dict = pickle.load(f)
        
    with open('data/sp100_nasdaq100_universe.json', 'r', encoding='utf-8') as f:
        fund_dict = json.load(f)
        
    with open('data/historical_quarterly_eps.json', 'r', encoding='utf-8') as f:
        eps_data = json.load(f)
        
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
        
    gspc_df = ohlcv_dict.get('^GSPC')
    if gspc_df is None:
        print("Error: ^GSPC not found.")
        return
        
    start_dt = pd.to_datetime('1971-01-04')
    end_dt = pd.to_datetime('2026-10-12')
    
    # Align dates based on ^GSPC
    common_dates = gspc_df.loc[start_dt:end_dt].index
    print(f"Total trading days available on S&P 500: {len(common_dates)} (Jan 1971 to Oct 2026)")
    
    # Extract Close and Open prices for universe
    tickers = [t for t in fund_dict.keys() if t in ohlcv_dict]
    print(f"Total corporate constituents tracked: {len(tickers)}")
    
    close_dict = {}
    open_dict = {}
    for t in tickers:
        df = ohlcv_dict[t]
        close_dict[t] = df['Close'].reindex(common_dates)
        open_dict[t] = df['Open'].reindex(common_dates)
        
    close_df = pd.DataFrame(close_dict)
    open_df = pd.DataFrame(open_dict)
    
    gspc_close = gspc_df['Close'].reindex(common_dates).ffill()
    gspc_open = gspc_df['Open'].reindex(common_dates).ffill()
    
    # Define the 5 Historical Macro Regimes across 55.5 years
    regimes = {
        "1971-1981 (Stagflation & Volcker)": (pd.to_datetime('1971-01-01'), pd.to_datetime('1981-12-31')),
        "1982-1999 (Disinflation Bull)": (pd.to_datetime('1982-01-01'), pd.to_datetime('1999-12-31')),
        "2000-2007 (Dot-Com Bust & Housing)": (pd.to_datetime('2000-01-01'), pd.to_datetime('2007-12-31')),
        "2008-2022 (GFC, ZIRP & Tightening)": (pd.to_datetime('2008-01-01'), pd.to_datetime('2022-12-31')),
        "2023-2026 (AI Infrastructure Supercycle)": (pd.to_datetime('2023-01-01'), pd.to_datetime('2026-10-15'))
    }
    
    # Test weekly cross-sections (step = 5 trading days) across 55.5 years
    sample_indices = [i for i in range(0, len(common_dates) - 252, 5)]
    print(f"Evaluating {len(sample_indices)} weekly cross-sections from {common_dates[sample_indices[0]].strftime('%Y-%m-%d')} to {common_dates[sample_indices[-1]].strftime('%Y-%m-%d')}...")
    
    # Multiplier cutoffs to test
    cutoffs = [6.0, 10.0, 12.0, 16.0, 18.0]
    cutoff_events = {c: [] for c in cutoffs}
    strict_events = []
    relaxed_events = []
    regime_events_12x = {r: [] for r in regimes}
    
    for idx in sample_indices:
        dt = common_dates[idx]
        row_prices = close_df.iloc[idx]
        
        # Identify active constituents with prices at date dt
        active_t = [t for t in tickers if pd.notna(row_prices[t]) and row_prices[t] > 0]
        if len(active_t) < 10:
            continue
            
        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {}
        curr_raw_de = {}
        
        for t in active_t:
            p = float(row_prices[t])
            
            # Point-in-time EPS: if in parsed_eps, use past 4 quarters
            ttm_eps = None
            if t in parsed_eps:
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
                    
            if ttm_eps is None or ttm_eps <= 0:
                # Historical fundamental trajectory: discount today's EPS by growth
                today_p = float(close_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 20.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 20.0)
                years_ago = (common_dates[-1] - dt).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 10.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.25, annual_g))) ** years_ago)
                
            if ttm_eps > 0.02:
                pe = p / ttm_eps
                if 2.0 <= pe <= 150.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    
                    # 5-Year revenue/price growth
                    idx_5y = idx - (252 * 5)
                    if idx_5y >= 0 and pd.notna(close_df[t].iloc[idx_5y]) and close_df[t].iloc[idx_5y] > 0:
                        p_5y = float(close_df[t].iloc[idx_5y])
                        g_price = ((p / p_5y) ** 0.2 - 1.0) * 100.0
                        sales_g = 0.5 * fund_dict[t].get('sales5y', 10.0) + 0.5 * g_price
                    else:
                        sales_g = fund_dict[t].get('sales5y', 10.0)
                        
                    curr_sales[t] = max(-20.0, min(100.0, sales_g))
                    curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                    raw_de = fund_dict[t].get('de', 50.0)
                    curr_raw_de[t] = raw_de
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))
                    
        if len(curr_pes) < 10:
            continue
            
        valid_t = list(curr_pes.keys())
        sales_vals = [curr_sales[t] for t in valid_t]
        roic_vals = [curr_roic[t] for t in valid_t]
        ey_vals = [curr_ey[t] for t in valid_t]
        de_inv_vals = [curr_de_inv[t] for t in valid_t]
        pes_vals = [curr_pes[t] for t in valid_t]
        
        s_std, s_med = float(np.std(sales_vals)), float(np.median(sales_vals))
        r_std, r_med = float(np.std(roic_vals)), float(np.median(roic_vals))
        e_std, e_med = float(np.std(ey_vals)), float(np.median(ey_vals))
        d_std, d_med = float(np.std(de_inv_vals)), float(np.median(de_inv_vals))
        p_q1, p_med = float(np.percentile(pes_vals, 25)), float(np.median(pes_vals))
        
        # Benchmark returns (MOC: from Close_t)
        gspc_c0 = gspc_close.iloc[idx]
        gspc_c21 = gspc_close.iloc[idx + 21]
        gspc_c63 = gspc_close.iloc[idx + 63]
        gspc_c126 = gspc_close.iloc[idx + 126]
        gspc_c252 = gspc_close.iloc[idx + 252]
        
        spy_1m = (gspc_c21 / gspc_c0) - 1.0
        spy_3m = (gspc_c63 / gspc_c0) - 1.0
        spy_6m = (gspc_c126 / gspc_c0) - 1.0
        spy_1y = (gspc_c252 / gspc_c0) - 1.0
        
        # Current regime
        curr_regime = None
        for r_name, (r_start, r_end) in regimes.items():
            if r_start <= dt <= r_end:
                curr_regime = r_name
                break
                
        for t in valid_t:
            c0 = close_df[t].iloc[idx]
            c21 = close_df[t].iloc[idx + 21]
            c63 = close_df[t].iloc[idx + 63]
            c126 = close_df[t].iloc[idx + 126]
            c252 = close_df[t].iloc[idx + 252]
            
            if pd.isna(c0) or pd.isna(c63) or c0 <= 0:
                continue
                
            ret_1m = (c21 / c0) - 1.0 if pd.notna(c21) else None
            ret_3m = (c63 / c0) - 1.0
            ret_6m = (c126 / c0) - 1.0 if pd.notna(c126) else None
            ret_1y = (c252 / c0) - 1.0 if pd.notna(c252) else None
            
            alpha_3m = ret_3m - spy_3m
            
            # Max DD over 3M
            window = close_df[t].iloc[idx : idx + 64].dropna()
            max_dd_3m = float(((window - window.cummax()) / window.cummax()).min()) if len(window) > 1 else None
            
            ev = {
                'date': dt,
                'ticker': t,
                'ret_1m': ret_1m,
                'ret_3m': ret_3m,
                'ret_6m': ret_6m,
                'ret_1y': ret_1y,
                'alpha_3m': alpha_3m,
                'spy_3m': spy_3m,
                'max_dd_3m': max_dd_3m,
                'regime': curr_regime
            }
            
            pe = curr_pes[t]
            sales = curr_sales[t]
            raw_de = curr_raw_de[t]
            
            # Strict Q1 Value
            if pe <= p_q1 and sales >= 12.0 and raw_de <= 100.0:
                strict_events.append(ev)
                
            # Relaxed to Median
            if pe <= p_med and sales >= 10.0 and raw_de <= 120.0:
                relaxed_events.append(ev)
                
            # Multiplier calculation
            zg = (sales - s_med) / (s_std if s_std > 0 else 1.0)
            zr = (curr_roic[t] - r_med) / (r_std if r_std > 0 else 1.0)
            zy = (curr_ey[t] - e_med) / (e_std if e_std > 0 else 1.0)
            zs = (curr_de_inv[t] - d_med) / (d_std if d_std > 0 else 1.0)
            
            all_4_above = (zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0)
            if all_4_above:
                m_score = (1.0 + zg) * (1.0 + zr) * (1.0 + zy) * (1.0 + zs)
                for c in cutoffs:
                    if m_score >= c:
                        cutoff_events[c].append(ev)
                if m_score >= 12.0 and curr_regime in regime_events_12x:
                    regime_events_12x[curr_regime].append(ev)
                    
    def summarize(events, name):
        if not events:
            return {"Strategy": name, "Signals": 0}
        n = len(events)
        r1m = [e['ret_1m'] for e in events if e['ret_1m'] is not None]
        r3m = [e['ret_3m'] for e in events if e['ret_3m'] is not None]
        r6m = [e['ret_6m'] for e in events if e['ret_6m'] is not None]
        r1y = [e['ret_1y'] for e in events if e['ret_1y'] is not None]
        a3m = [e['alpha_3m'] for e in events if e['alpha_3m'] is not None]
        s3m = [e['spy_3m'] for e in events if e['spy_3m'] is not None]
        dd = [e['max_dd_3m'] for e in events if e['max_dd_3m'] is not None]
        win = sum(1 for r in r3m if r > 0) / len(r3m) * 100.0 if r3m else 0.0
        
        return {
            "Strategy": name,
            "Signals": n,
            "Win Rate (3M)": f"{win:.1f}%",
            "Fwd 1M Ret": f"{np.mean(r1m)*100.0:+.2f}%" if r1m else "N/A",
            "Fwd 3M Ret": f"{np.mean(r3m)*100.0:+.2f}%" if r3m else "N/A",
            "S&P 3M Ret": f"{np.mean(s3m)*100.0:+.2f}%" if s3m else "N/A",
            "3M Alpha vs S&P": f"{np.mean(a3m)*100.0:+.2f}%" if a3m else "N/A",
            "Fwd 6M Ret": f"{np.mean(r6m)*100.0:+.2f}%" if r6m else "N/A",
            "Fwd 1Y Ret": f"{np.mean(r1y)*100.0:+.2f}%" if r1y else "N/A",
            "Max DD (3M)": f"{np.mean(dd)*100.0:.1f}%" if dd else "N/A"
        }
        
    print("\n=== 55.5-YEAR OVERALL MULTI-CYCLE PERFORMANCE (1971 - 2026: MOC EXECUTION) ===")
    summary_rows = [
        summarize(strict_events, "Strict Q1 Deep Value"),
        summarize(relaxed_events, "Relaxed to Median (All 4)"),
        summarize(cutoff_events[6.0], "Multiplier >= 6.0x"),
        summarize(cutoff_events[10.0], "Multiplier >= 10.0x"),
        summarize(cutoff_events[12.0], "Multiplier >= 12.0x (Optimal)"),
        summarize(cutoff_events[16.0], "Multiplier >= 16.0x"),
        summarize(cutoff_events[18.0], "Multiplier >= 18.0x (Generational)")
    ]
    df_sum = pd.DataFrame(summary_rows)
    print(df_sum.to_string(index=False))
    
    print("\n=== 55.5-YEAR MACRO REGIME BREAKDOWN FOR MULTIPLIER >= 12.0x (1971 - 2026) ===")
    reg_rows = []
    for r_name in regimes:
        reg_rows.append(summarize(regime_events_12x[r_name], r_name))
    df_reg = pd.DataFrame(reg_rows)
    print(df_reg.to_string(index=False))
    
    with open('data/backtest_1971_2026_results.json', 'w') as f:
        json.dump({
            "overall": summary_rows,
            "regimes": reg_rows
        }, f, indent=2)

if __name__ == '__main__':
    run_1971_moc()
