from fidelity_control.backtester import EXTENDED_FUNDAMENTALS
import yfinance as yf
import numpy as np
import pandas as pd

tickers = list(EXTENDED_FUNDAMENTALS.keys())
download_list = [t for t in tickers if t not in ['ROOTS', 'CRYPTO', 'T-Bills', 'VTI / VT']]
prices_df = yf.download(download_list + ['SPY'], period="2y", interval='1d', progress=False)['Close']

base_prices = prices_df.iloc[-1]
base_eps = {t: float(base_prices[t]) / EXTENDED_FUNDAMENTALS[t].get('pe', 25.0) for t in tickers if t in base_prices and pd.notna(base_prices[t])}
dates = prices_df.index
sample_indices = range(0, len(dates) - 63, 5)

events = []
for idx in sample_indices:
    dt = dates[idx]
    row_prices = prices_df.loc[dt]
    curr_pes, curr_ey, curr_sales, curr_roic, curr_de_inv = {}, {}, {}, {}, {}
    for t in tickers:
        if t in row_prices and pd.notna(row_prices[t]) and t in base_eps and base_eps[t] > 0:
            pe = float(row_prices[t]) / base_eps[t]
            if 2.0 <= pe <= 200.0:
                curr_pes[t] = pe
                curr_ey[t] = 1.0 / pe
                curr_sales[t] = EXTENDED_FUNDAMENTALS[t]['sales5y']
                curr_roic[t] = EXTENDED_FUNDAMENTALS[t].get('roic_proxy', 0.15)
                de = EXTENDED_FUNDAMENTALS[t]['de']
                curr_de_inv[t] = 100.0 / (10.0 + max(0.0, de))
    if len(curr_pes) < 12: continue
    valid_tickers = list(curr_pes.keys())
    sales_vals = [curr_sales[t] for t in valid_tickers]
    roic_vals = [curr_roic[t] for t in valid_tickers]
    ey_vals = [curr_ey[t] for t in valid_tickers]
    de_inv_vals = [curr_de_inv[t] for t in valid_tickers]
    s_med, s_std = np.median(sales_vals), np.std(sales_vals)
    r_med, r_std = np.median(roic_vals), np.std(roic_vals)
    e_med, e_std = np.median(ey_vals), np.std(ey_vals)
    d_med, d_std = np.median(de_inv_vals), np.std(de_inv_vals)

    for t in valid_tickers:
        price = float(row_prices[t])
        sales, roic, ey, de_inv = curr_sales[t], curr_roic[t], curr_ey[t], curr_de_inv[t]
        if sales > s_med and roic > r_med and ey > e_med and de_inv > d_med:
            zs = max(0.0, (sales - s_med)/s_std)
            zr = max(0.0, (roic - r_med)/r_std)
            ze = max(0.0, (ey - e_med)/e_std)
            zd = max(0.0, (de_inv - d_med)/d_std)
            comp_score = (1 + zs) * (1 + zr) * (1 + ze) * (1 + zd)
            p_future_3m = float(prices_df[t].iloc[idx + 63])
            ret_3m = (p_future_3m / price) - 1.0
            spy_start = float(prices_df['SPY'].iloc[idx])
            spy_end = float(prices_df['SPY'].iloc[idx + 63])
            spy_ret_3m = (spy_end / spy_start) - 1.0
            alpha_3m = ret_3m - spy_ret_3m
            w_prices = prices_df[t].iloc[idx : idx + 64]
            peak = w_prices.cummax()
            max_dd = float(((w_prices - peak) / peak).min())
            p_future_6m = float(prices_df[t].iloc[idx + 126]) if idx + 126 < len(dates) else None
            ret_6m = (p_future_6m / price) - 1.0 if p_future_6m else None

            events.append({
                "ticker": t,
                "score": comp_score,
                "ret_3m": ret_3m * 100,
                "ret_6m": ret_6m * 100 if ret_6m else None,
                "alpha_3m": alpha_3m * 100,
                "max_dd": max_dd * 100
            })

import pandas as pd
print("Events captured:", len(events))
for th in [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]:
    sub = [e for e in events if e['score'] >= th]
    if not sub:
        print(f"Thresh >={th:>4.1f}x: 0 signals")
        continue
    tickers_sub = list(set(e['ticker'] for e in sub))
    win = sum(1 for e in sub if e['ret_3m'] > 0) / len(sub) * 100
    avg_3m = np.mean([e['ret_3m'] for e in sub])
    avg_alp = np.mean([e['alpha_3m'] for e in sub])
    avg_dd = np.mean([e['max_dd'] for e in sub])
    valid_6m = [e['ret_6m'] for e in sub if e['ret_6m'] is not None]
    avg_6m = np.mean(valid_6m) if valid_6m else None
    ret6_str = f"{avg_6m:+.1f}%" if avg_6m is not None else "N/A"
    print(f"Thresh >={th:>4.1f}x: N={len(sub):<3} | Win={win:>5.1f}% | 3M={avg_3m:>+6.1f}% | Alpha={avg_alp:>+6.1f}% | 6M={ret6_str} | MaxDD={avg_dd:>5.1f}% | Tickers={tickers_sub}")

