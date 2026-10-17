"""Run empirical backtest across the expanded 177-ticker S&P 100 + Nasdaq 100 universe."""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

DATA_FILE = Path(__file__).resolve().parent / "data" / "sp100_nasdaq100_universe.json"

def run_expanded_universe_analysis(period: str = "2y"):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        universe_data = json.load(f)

    tickers = list(universe_data.keys())
    print(f" [*] Loaded {len(tickers)} companies from S&P 100 + Nasdaq 100 universe.")

    # 1. Download price data for all 177 tickers + SPY
    download_list = [t for t in tickers if t not in ['ROOTS', 'CRYPTO', 'T-Bills']]
    print(f" [*] Downloading 2-year daily close price history for {len(download_list)} tickers + SPY...")
    prices_df = yf.download(download_list + ['SPY'], period=period, interval='1d', progress=False)['Close']

    if prices_df.empty:
        raise RuntimeError("Failed to download price data.")

    # Drop columns that have too many missing values (e.g. newly listed)
    valid_cols = [c for c in download_list if c in prices_df and prices_df[c].notna().sum() > 200]
    print(f" [+] Valid price histories available: {len(valid_cols)} tickers.")

    base_prices = prices_df.iloc[-1]
    base_eps = {}
    base_bvps = {}
    for t in valid_cols:
        p = float(base_prices[t])
        pe = universe_data[t].get('pe', 25.0)
        pb = universe_data[t].get('pb', 4.0)
        base_eps[t] = p / (pe if pe > 0 else 25.0)
        base_bvps[t] = p / (pb if pb > 0 else 4.0)

    dates = prices_df.index
    # Sample every 5 trading days (weekly review)
    sample_indices = range(0, len(dates) - 63, 5)

    events_strict = []
    events_relaxed = []
    events_dislocation = []
    events_1sd = []
    events_2sd = []
    multiplier_events = []

    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]

        curr_pes, curr_pbs, curr_ey, curr_sales, curr_roic, curr_de_inv = {}, {}, {}, {}, {}, {}

        for t in valid_cols:
            if t in row_prices and pd.notna(row_prices[t]) and t in base_eps and base_eps[t] > 0:
                p = float(row_prices[t])
                pe = p / base_eps[t]
                if 2.0 <= pe <= 200.0:
                    curr_pes[t] = pe
                    curr_pbs[t] = p / base_bvps[t]
                    curr_ey[t] = 1.0 / pe
                    curr_sales[t] = universe_data[t].get('sales5y', 12.0)
                    curr_roic[t] = universe_data[t].get('roic_proxy', 0.15)
                    de = universe_data[t].get('de', 50.0)
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, de))

        if len(curr_pes) < 50:
            continue

        active_tickers = list(curr_pes.keys())
        pes_list = [curr_pes[t] for t in active_tickers]
        pbs_list = [curr_pbs[t] for t in active_tickers if curr_pbs[t] < 100]
        des_list = [universe_data[t].get('de', 50.0) for t in active_tickers]

        q1_pe = float(np.percentile(pes_list, 25))
        med_pe = float(np.percentile(pes_list, 50))
        q1_pb = float(np.percentile(pbs_list, 25))
        med_pb = float(np.percentile(pbs_list, 50))
        med_de = float(np.percentile(des_list, 50))

        # Distribution statistics for standard deviations
        sales_vals = [curr_sales[t] for t in active_tickers]
        roic_vals = [curr_roic[t] for t in active_tickers]
        ey_vals = [curr_ey[t] for t in active_tickers]
        de_inv_vals = [curr_de_inv[t] for t in active_tickers]

        s_mean, s_std, s_med = float(np.mean(sales_vals)), float(np.std(sales_vals)), float(np.median(sales_vals))
        r_mean, r_std, r_med = float(np.mean(roic_vals)), float(np.std(roic_vals)), float(np.median(roic_vals))
        e_mean, e_std, e_med = float(np.mean(ey_vals)), float(np.std(ey_vals)), float(np.median(ey_vals))
        d_mean, d_std, d_med = float(np.mean(de_inv_vals)), float(np.std(de_inv_vals)), float(np.median(de_inv_vals))

        # SPY 3M
        spy_start = float(prices_df['SPY'].iloc[idx])
        spy_end = float(prices_df['SPY'].iloc[idx + 63])
        spy_ret_3m = (spy_end / spy_start) - 1.0

        for t in active_tickers:
            price = float(row_prices[t])
            pe = curr_pes[t]
            pb = curr_pbs[t]
            sales = curr_sales[t]
            roic = curr_roic[t]
            raw_de = universe_data[t].get('de', 50.0)
            ey = curr_ey[t]
            de_inv = curr_de_inv[t]

            # Forward returns
            p_1m = float(prices_df[t].iloc[idx + 21]) if idx + 21 < len(dates) else None
            p_3m = float(prices_df[t].iloc[idx + 63])
            p_6m = float(prices_df[t].iloc[idx + 126]) if idx + 126 < len(dates) else None

            ret_1m = ((p_1m / price) - 1.0) * 100.0 if p_1m else None
            ret_3m = ((p_3m / price) - 1.0) * 100.0
            ret_6m = ((p_6m / price) - 1.0) * 100.0 if p_6m else None
            alpha_3m = ret_3m - (spy_ret_3m * 100.0)

            # 3M Max drawdown
            w_prices = prices_df[t].iloc[idx : idx + 64]
            peak = w_prices.cummax()
            max_dd_3m = float(((w_prices - peak) / peak).min()) * 100.0

            event = {
                "date": dt.strftime("%Y-%m-%d"),
                "ticker": t,
                "name": universe_data[t].get('name', t),
                "sector": universe_data[t].get('sector', ''),
                "price": round(price, 2),
                "pe": round(pe, 1),
                "pb": round(pb, 2),
                "sales": round(sales, 1),
                "roic": round(roic * 100, 1),
                "de": round(raw_de, 1),
                "ret_1m": round(ret_1m, 2) if ret_1m is not None else None,
                "ret_3m": round(ret_3m, 2),
                "ret_6m": round(ret_6m, 2) if ret_6m is not None else None,
                "spy_ret_3m": round(spy_ret_3m * 100.0, 2),
                "alpha_3m": round(alpha_3m, 2),
                "max_dd_3m": round(max_dd_3m, 2)
            }

            # Z-scores
            zs = (sales - s_mean) / s_std if s_std > 0 else 0
            zr = (roic - r_mean) / r_std if r_std > 0 else 0
            ze = (ey - e_mean) / e_std if e_std > 0 else 0
            zd = (de_inv - d_mean) / d_std if d_std > 0 else 0

            # 1. Strict
            if pe <= q1_pe and pb <= q1_pb and sales >= 20.0 and raw_de <= med_de:
                events_strict.append(event)

            # 2. Relaxed
            if pe <= med_pe and pb <= med_pb and sales >= 15.0 and raw_de <= 120.0:
                events_relaxed.append(event)

            # 3. Dislocation Quality (Decoupled P/B)
            if sales >= 15.0 and pe <= med_pe and raw_de <= 120.0:
                events_dislocation.append(event)

            # 4. +1 SD Quality
            if (zs >= 1.0 or zr >= 1.0) and pe <= med_pe and raw_de <= 120.0:
                events_1sd.append(event)

            # 5. +2 SD Outlier
            if (zs >= 2.0 or zr >= 2.0) and pe <= med_pe and raw_de <= 120.0:
                events_2sd.append(event)

            # 6. Extremized Multiplier Metric
            if sales > s_med and roic > r_med and ey > e_med and de_inv > d_med:
                zs_med = max(0.0, (sales - s_med) / s_std) if s_std > 0 else 0.0
                zr_med = max(0.0, (roic - r_med) / r_std) if r_std > 0 else 0.0
                ze_med = max(0.0, (ey - e_med) / e_std) if e_std > 0 else 0.0
                zd_med = max(0.0, (de_inv - d_med) / d_std) if d_std > 0 else 0.0

                m = (1.0 + zs_med) * (1.0 + zr_med) * (1.0 + ze_med) * (1.0 + zd_med)
                e_mult = dict(event)
                e_mult["composite_score"] = round(m, 2)
                multiplier_events.append(e_mult)

    def calc_stats(ev_list):
        if not ev_list:
            return {"signals": 0, "tickers": [], "win_rate": 0.0, "ret_3m": None, "alpha": None, "ret_6m": None, "max_dd": None}
        win = sum(1 for e in ev_list if e['ret_3m'] > 0) / len(ev_list) * 100.0
        r3m = float(np.mean([e['ret_3m'] for e in ev_list]))
        alp = float(np.mean([e['alpha_3m'] for e in ev_list]))
        mdd = float(np.mean([e['max_dd_3m'] for e in ev_list]))
        v6m = [e['ret_6m'] for e in ev_list if e['ret_6m'] is not None]
        r6m = float(np.mean(v6m)) if v6m else None
        tickers_unique = sorted(list(set(e['ticker'] for e in ev_list)))
        return {
            "signals": len(ev_list),
            "tickers": tickers_unique,
            "win_rate": round(win, 1),
            "ret_3m": round(r3m, 2),
            "alpha": round(alp, 2),
            "ret_6m": round(r6m, 2) if r6m is not None else None,
            "max_dd": round(mdd, 2)
        }

    return {
        "strict": calc_stats(events_strict),
        "relaxed": calc_stats(events_relaxed),
        "dislocation": calc_stats(events_dislocation),
        "1sd": calc_stats(events_1sd),
        "2sd": calc_stats(events_2sd),
        "multiplier_all": multiplier_events,
        "multiplier_cutoffs": {
            th: calc_stats([e for e in multiplier_events if e['composite_score'] >= th])
            for th in [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0]
        }
    }


if __name__ == "__main__":
    res = run_expanded_universe_analysis()
    print("\n" + "=" * 95)
    print(" S&P 100 + NASDAQ 100 EXPANDED BACKTEST RESULTS (177 CONSTITUENTS)")
    print("=" * 95)
    for mode in ["strict", "relaxed", "dislocation", "1sd", "2sd"]:
        s = res[mode]
        t_str = ", ".join(s['tickers'][:6]) + ("..." if len(s['tickers']) > 6 else "")
        r3 = f"{s['ret_3m']:+.1f}%" if s['ret_3m'] is not None else "N/A"
        alp = f"{s['alpha']:+.1f}%" if s['alpha'] is not None else "N/A"
        r6 = f"{s['ret_6m']:+.1f}%" if s['ret_6m'] is not None else "N/A"
        dd = f"{s['max_dd']:.1f}%" if s['max_dd'] is not None else "N/A"
        print(f" {mode.upper():<14} | Signals: {s['signals']:<5} | Tickers ({len(s['tickers'])}): {t_str:<32} | Win: {s['win_rate']:>5.1f}% | 3M: {r3:>7} | Alpha: {alp:>7} | 6M: {r6:>7} | DD: {dd:>6}")

    print("\n" + "=" * 95)
    print(" EXTREMIZED MULTIPLIER CUTOFF CURVE ACROSS S&P 100 + NASDAQ 100")
    print("=" * 95)
    print(f" {'Cutoff':<10} | {'Signals':<8} | {'Unique Tickers':<6} | {'Top Tickers':<35} | {'Win Rate':<9} | {'3M Ret':<8} | {'Alpha':<8} | {'6M Ret':<8} | {'Max DD':<7}")
    print("-" * 115)
    for th, s in res['multiplier_cutoffs'].items():
        top_t = ", ".join(s['tickers'][:5]) + ("..." if len(s['tickers']) > 5 else "")
        r3 = f"{s['ret_3m']:+.1f}%" if s['ret_3m'] is not None else "N/A"
        alp = f"{s['alpha']:+.1f}%" if s['alpha'] is not None else "N/A"
        r6 = f"{s['ret_6m']:+.1f}%" if s['ret_6m'] is not None else "N/A"
        dd = f"{s['max_dd']:.1f}%" if s['max_dd'] is not None else "N/A"
        print(f" >={th:<4.1f}x    | {s['signals']:<8} | {len(s['tickers']):<6} | {top_t:<35} | {s['win_rate']:>5.1f}%   | {r3:>8} | {alp:>8} | {r6:>8} | {dd:>7}")

    print("\n Top 12 Highest-Scoring Extremized Events in S&P 100 + Nasdaq 100:")
    print(f" {'Date':<10} | {'Ticker':<5} | {'Company Name':<25} | {'Price':<8} | {'P/E':<5} | {'Score':<8} | {'3M Ret':<8} | {'Alpha':<8} | {'Max DD':<7}")
    print("-" * 95)
    top_events = sorted(res['multiplier_all'], key=lambda x: x['composite_score'], reverse=True)[:15]
    for e in top_events:
        print(f" {e['date']:<10} | {e['ticker']:<5} | {e['name'][:25]:<25} | ${e['price']:>7.2f} | {e['pe']:>5.1f} | {e['composite_score']:>6.1f}x | {e['ret_3m']:>+7.1f}% | {e['alpha_3m']:>+7.1f}% | {e['max_dd_3m']:>6.1f}%")
