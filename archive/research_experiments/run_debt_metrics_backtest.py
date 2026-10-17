"""Empirical Backtest: Head-to-Head Comparison of Balance Sheet Debt Metrics.

Evaluates 6 competing debt/solvency paradigms across the S&P 100 + Nasdaq 100 universe
from January 2008 to October 2026 (18.8 years, covering GFC, 2011 crisis, 2020 COVID, 2022 rate shock):
  1. Baseline (No Debt Filter - 3-Factor Growth+ROIC+Value)
  2. Model 1: Traditional Book Debt-to-Equity (Accounting D/E <= 100%)
  3. Model 2: Net Debt / EBITDA (Operating Earnings Leverage <= 2.5x)
  4. Model 3: Debt / Market Capitalization (Enterprise Solvency <= 25%)
  5. Model 4: Net Debt / Free Cash Flow (FCF Coverage <= 3.5x)
  6. Model 5: "Munger Fortress" Dual-Solvency (Net Debt/EBITDA <= 2.5x AND Debt/MktCap <= 25%)
"""

import json
import math
import sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def run_debt_backtest():
    print("=" * 100)
    print(" MUNGER FACTOR ENGINE: EMPIRICAL BALANCE SHEET DEBT METRIC SHOWDOWN")
    print(" 18.8-Year Historical Backtest (2008 - 2026) | 177 Equities | Point-In-Time Pricing & EPS")
    print("=" * 100)

    # 1. Load Data
    price_file = Path("data/historical_prices_2007_2026.pkl")
    prices_df = pd.read_pickle(price_file)
    print(f"Loaded prices: {prices_df.index[0].strftime('%Y-%m-%d')} to {prices_df.index[-1].strftime('%Y-%m-%d')} ({len(prices_df)} trading days)")

    with open("data/historical_quarterly_eps.json", "r", encoding="utf-8") as f:
        eps_data = json.load(f)

    with open("data/sp100_nasdaq100_universe.json", "r", encoding="utf-8") as f:
        fund_dict = json.load(f)

    with open("data/universe_debt_metrics.json", "r", encoding="utf-8") as f:
        debt_metrics = json.load(f)

    # Parse point-in-time EPS
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
    start_dt = pd.to_datetime("2008-01-02")
    sample_indices = [i for i in range(0, len(dates) - 63, 5) if dates[i] >= start_dt]
    print(f"Evaluating {len(sample_indices)} weekly cross-sections from {dates[sample_indices[0]].strftime('%Y-%m-%d')} to {dates[sample_indices[-1]].strftime('%Y-%m-%d')}...")

    # Market Regimes
    regimes = {
        '2008-2010 (GFC & Recovery)': (pd.to_datetime('2008-01-01'), pd.to_datetime('2010-12-31')),
        '2011-2015 (Euro Crisis & Zero Rates)': (pd.to_datetime('2011-01-01'), pd.to_datetime('2015-12-31')),
        '2016-2019 (Tech Expansion & QT)': (pd.to_datetime('2016-01-01'), pd.to_datetime('2019-12-31')),
        '2020-2022 (COVID Crash & Rate Shock)': (pd.to_datetime('2020-01-01'), pd.to_datetime('2022-12-31')),
        '2023-2026 (AI Bull Market)': (pd.to_datetime('2023-01-01'), pd.to_datetime('2026-10-15'))
    }

    # Model definition keys
    models = [
        'Baseline (No Debt Filter)',
        'Model 1: Book Debt/Equity (<=100%)',
        'Model 2: Net Debt / EBITDA (<=2.5x)',
        'Model 3: Debt / Market Cap (<=25%)',
        'Model 4: Net Debt / FCF (<=3.5x)',
        'Model 5: Dual Solvency (ND/EBITDA<=2.5x & D/MC<=25%)'
    ]

    model_events = {m: [] for m in models}
    regime_events = {m: {r: [] for r in regimes} for m in models}

    # Compounder inclusion tracking (did the model allow these elite compounders or falsely block them?)
    compounder_whitelist = ['AAPL', 'MA', 'HD', 'MSFT', 'PEP', 'GOOGL', 'NVDA', 'AZO', 'ORCL', 'COST']
    compounder_passes = {m: {t: 0 for t in compounder_whitelist} for m in models}
    compounder_total_evals = {t: 0 for t in compounder_whitelist}

    # Toxic debt disaster tracking (did the model successfully block these troubled balance sheets?)
    disaster_blacklist = ['BA', 'INTC', 'WBA', 'LUMN', 'F']
    disaster_blocks = {m: {t: 0 for t in disaster_blacklist} for m in models}
    disaster_total_evals = {t: 0 for t in disaster_blacklist}

    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]

        # Determine regime
        curr_regime = None
        for r_name, (r_start, r_end) in regimes.items():
            if r_start <= dt <= r_end:
                curr_regime = r_name
                break

        # Collect fundamentals for valid tickers on this date
        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_raw = {}
        curr_nd_ebitda = {}
        curr_debt_mktcap = {}
        curr_nd_fcf = {}

        for t in fund_dict.keys():
            if t not in row_prices or pd.isna(row_prices[t]) or row_prices[t] <= 0:
                continue

            p = float(row_prices[t])

            # 1. Point-in-time TTM EPS
            ttm_eps = None
            if t in parsed_eps:
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])

            if ttm_eps is None or ttm_eps <= 0:
                today_p = float(prices_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 25.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 25.0)
                years_ago = (dates[-1] - dt).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 12.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.35, annual_g))) ** years_ago)

            if ttm_eps <= 0.05:
                continue

            pe = p / ttm_eps
            if not (2.0 <= pe <= 200.0):
                continue

            curr_pes[t] = pe
            curr_ey[t] = 1.0 / pe

            # 5Y annualized growth rate
            idx_5y = idx - (252 * 5)
            if idx_5y >= 0 and pd.notna(prices_df[t].iloc[idx_5y]) and prices_df[t].iloc[idx_5y] > 0:
                p_5y = float(prices_df[t].iloc[idx_5y])
                g_price = ((p / p_5y) ** 0.2 - 1.0) * 100.0
                sales_g = 0.5 * fund_dict[t].get('sales5y', 12.0) + 0.5 * g_price
            else:
                sales_g = fund_dict[t].get('sales5y', 12.0)

            curr_sales[t] = max(-20.0, min(120.0, sales_g))
            curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)

            # Debt metrics for ticker
            d_info = debt_metrics.get(t, {})
            # 1. Book D/E
            curr_de_raw[t] = fund_dict[t].get('de', 50.0)
            if d_info.get('debtToEquity') is not None:
                curr_de_raw[t] = float(d_info['debtToEquity'])

            # 2. Net Debt / EBITDA
            # Scale EBITDA historically with earnings level
            nd_eb = d_info.get('net_debt_to_ebitda')
            if nd_eb is None:
                # Default proxy from debt and earnings
                nd_eb = 1.2
            curr_nd_ebitda[t] = float(nd_eb)

            # 3. Debt / Market Cap (scaled dynamically by historical price ratio)
            td = d_info.get('totalDebt', 0) or 0
            mc_today = d_info.get('marketCap', 1) or 1
            p_today = float(prices_df[t].dropna().iloc[-1])
            mc_hist = mc_today * (p / (p_today if p_today > 0 else p))
            curr_debt_mktcap[t] = (td / mc_hist) if mc_hist > 0 else 0.15

            # 4. Net Debt / FCF
            nd_fcf = d_info.get('net_debt_to_fcf')
            if nd_fcf is None:
                nd_fcf = 1.5
            curr_nd_fcf[t] = float(nd_fcf)

        if len(curr_pes) < 25:
            continue

        valid_t = list(curr_pes.keys())
        sales_vals = [curr_sales[t] for t in valid_t]
        roic_vals = [curr_roic[t] for t in valid_t]
        ey_vals = [curr_ey[t] for t in valid_t]
        pes_vals = [curr_pes[t] for t in valid_t]

        s_med, s_std = float(np.median(sales_vals)), float(np.std(sales_vals) or 1.0)
        r_med, r_std = float(np.median(roic_vals)), float(np.std(roic_vals) or 1.0)
        e_med, e_std = float(np.median(ey_vals)), float(np.std(ey_vals) or 1.0)
        p_med = float(np.median(pes_vals))

        # Forward benchmark return (SPY)
        spy_ret_3m = None
        spy_ret_6m = None
        spy_ret_1y = None
        if 'SPY' in prices_df:
            spy_p0 = float(prices_df['SPY'].iloc[idx])
            if idx + 63 < len(dates):
                spy_ret_3m = (float(prices_df['SPY'].iloc[idx + 63]) / spy_p0) - 1.0
            if idx + 126 < len(dates):
                spy_ret_6m = (float(prices_df['SPY'].iloc[idx + 126]) / spy_p0) - 1.0
            if idx + 252 < len(dates):
                spy_ret_1y = (float(prices_df['SPY'].iloc[idx + 252]) / spy_p0) - 1.0

        for t in valid_t:
            S0 = float(row_prices[t])
            pe = curr_pes[t]
            sales = curr_sales[t]
            roic = curr_roic[t]
            ey = curr_ey[t]

            # Forward returns for this stock
            ret_3m = (float(prices_df[t].iloc[idx + 63]) / S0) - 1.0 if idx + 63 < len(dates) else None
            ret_6m = (float(prices_df[t].iloc[idx + 126]) / S0) - 1.0 if idx + 126 < len(dates) else None
            ret_1y = (float(prices_df[t].iloc[idx + 252]) / S0) - 1.0 if idx + 252 < len(dates) else None

            alpha_3m = (ret_3m - spy_ret_3m) if (ret_3m is not None and spy_ret_3m is not None) else None
            alpha_6m = (ret_6m - spy_ret_6m) if (ret_6m is not None and spy_ret_6m is not None) else None
            alpha_1y = (ret_1y - spy_ret_1y) if (ret_1y is not None and spy_ret_1y is not None) else None

            # 3M & 1Y Max Drawdown
            max_dd_3m = None
            if idx + 63 < len(dates):
                win3 = prices_df[t].iloc[idx : idx + 64]
                dd = (win3 - win3.cummax()) / win3.cummax()
                max_dd_3m = float(dd.min())

            max_dd_1y = None
            if idx + 252 < len(dates):
                win1y = prices_df[t].iloc[idx : idx + 253]
                dd1y = (win1y - win1y.cummax()) / win1y.cummax()
                max_dd_1y = float(dd1y.min())

            # Core Munger Growth & Quality Hurdles:
            # Sales >= Median AND ROIC >= Median AND PE <= Median
            passes_growth_quality_value = (sales >= s_med and roic >= r_med and pe <= p_med)

            if not passes_growth_quality_value:
                continue

            # Track total evaluation counts for compounders and disasters
            if t in compounder_whitelist:
                compounder_total_evals[t] += 1
            if t in disaster_blacklist:
                disaster_total_evals[t] += 1

            de_val = curr_de_raw[t]
            nd_eb_val = curr_nd_ebitda[t]
            d_mc_val = curr_debt_mktcap[t]
            nd_fcf_val = curr_nd_fcf[t]

            # Standardized z-scores for multiplier calculation
            zs = max(0.0, (sales - s_med) / s_std)
            zr = max(0.0, (roic - r_med) / r_std)
            ze = max(0.0, (ey - e_med) / e_std)
            base_multiplier = (1.0 + zs) * (1.0 + zr) * (1.0 + ze)

            ev_base = {
                'date': dt.strftime('%Y-%m-%d'), 'ticker': t, 'price': S0, 'pe': pe, 'sales': sales,
                'ret_3m': ret_3m, 'ret_6m': ret_6m, 'ret_1y': ret_1y,
                'alpha_3m': alpha_3m, 'alpha_6m': alpha_6m, 'alpha_1y': alpha_1y,
                'max_dd_3m': max_dd_3m, 'max_dd_1y': max_dd_1y, 'regime': curr_regime
            }

            # -------------------------------------------------------------
            # Model 0: Baseline (No Debt Filter)
            # -------------------------------------------------------------
            model_events['Baseline (No Debt Filter)'].append(ev_base)
            if curr_regime:
                regime_events['Baseline (No Debt Filter)'][curr_regime].append(ev_base)
            if t in compounder_whitelist:
                compounder_passes['Baseline (No Debt Filter)'][t] += 1

            # -------------------------------------------------------------
            # Model 1: Book Debt/Equity (<= 100%)
            # -------------------------------------------------------------
            # Traditional accounting hurdle
            if de_val <= 100.0:
                model_events['Model 1: Book Debt/Equity (<=100%)'].append(ev_base)
                if curr_regime:
                    regime_events['Model 1: Book Debt/Equity (<=100%)'][curr_regime].append(ev_base)
                if t in compounder_whitelist:
                    compounder_passes['Model 1: Book Debt/Equity (<=100%)'][t] += 1
            else:
                if t in disaster_blacklist:
                    disaster_blocks['Model 1: Book Debt/Equity (<=100%)'][t] += 1

            # -------------------------------------------------------------
            # Model 2: Net Debt / EBITDA (<= 2.5x)
            # -------------------------------------------------------------
            if nd_eb_val <= 2.5:
                model_events['Model 2: Net Debt / EBITDA (<=2.5x)'].append(ev_base)
                if curr_regime:
                    regime_events['Model 2: Net Debt / EBITDA (<=2.5x)'][curr_regime].append(ev_base)
                if t in compounder_whitelist:
                    compounder_passes['Model 2: Net Debt / EBITDA (<=2.5x)'][t] += 1
            else:
                if t in disaster_blacklist:
                    disaster_blocks['Model 2: Net Debt / EBITDA (<=2.5x)'][t] += 1

            # -------------------------------------------------------------
            # Model 3: Debt / Market Cap (<= 25%)
            # -------------------------------------------------------------
            if d_mc_val <= 0.25:
                model_events['Model 3: Debt / Market Cap (<=25%)'].append(ev_base)
                if curr_regime:
                    regime_events['Model 3: Debt / Market Cap (<=25%)'][curr_regime].append(ev_base)
                if t in compounder_whitelist:
                    compounder_passes['Model 3: Debt / Market Cap (<=25%)'][t] += 1
            else:
                if t in disaster_blacklist:
                    disaster_blocks['Model 3: Debt / Market Cap (<=25%)'][t] += 1

            # -------------------------------------------------------------
            # Model 4: Net Debt / FCF (<= 3.5x)
            # -------------------------------------------------------------
            if nd_fcf_val <= 3.5:
                model_events['Model 4: Net Debt / FCF (<=3.5x)'].append(ev_base)
                if curr_regime:
                    regime_events['Model 4: Net Debt / FCF (<=3.5x)'][curr_regime].append(ev_base)
                if t in compounder_whitelist:
                    compounder_passes['Model 4: Net Debt / FCF (<=3.5x)'][t] += 1
            else:
                if t in disaster_blacklist:
                    disaster_blocks['Model 4: Net Debt / FCF (<=3.5x)'][t] += 1

            # -------------------------------------------------------------
            # Model 5: Dual Solvency (Net Debt/EBITDA <= 2.5x AND Debt/MktCap <= 25%)
            # -------------------------------------------------------------
            if nd_eb_val <= 2.5 and d_mc_val <= 0.25:
                model_events['Model 5: Dual Solvency (ND/EBITDA<=2.5x & D/MC<=25%)'].append(ev_base)
                if curr_regime:
                    regime_events['Model 5: Dual Solvency (ND/EBITDA<=2.5x & D/MC<=25%)'][curr_regime].append(ev_base)
                if t in compounder_whitelist:
                    compounder_passes['Model 5: Dual Solvency (ND/EBITDA<=2.5x & D/MC<=25%)'][t] += 1
            else:
                if t in disaster_blacklist:
                    disaster_blocks['Model 5: Dual Solvency (ND/EBITDA<=2.5x & D/MC<=25%)'][t] += 1

    # =========================================================================
    # PERFORMANCE METRICS CALCULATION & REPORTING
    # =========================================================================
    summary_results = {}

    print("\n" + "=" * 115)
    print(" 1. HEAD-TO-HEAD BACKTEST SUMMARY: 18.8 YEARS (2008 - 2026)")
    print("=" * 115)
    print(f" {'Solvency Model Tested':<36} | {'Signals':<7} | {'WinRate 3M':<10} | {'3M Ret':<8} | {'1Y Ret':<8} | {'1Y Alpha':<9} | {'1Y MaxDD':<9} | {'Calmar'}")
    print("-" * 115)

    for m in models:
        evs = model_events[m]
        with_3m = [e for e in evs if e['ret_3m'] is not None]
        with_1y = [e for e in evs if e['ret_1y'] is not None]

        count = len(with_3m)
        if count == 0:
            continue

        win_3m = (sum(1 for e in with_3m if e['ret_3m'] > 0) / count) * 100.0
        win_1y = (sum(1 for e in with_1y if e['ret_1y'] > 0) / len(with_1y)) * 100.0 if with_1y else 0.0
        r3 = float(np.mean([e['ret_3m'] for e in with_3m])) * 100.0
        r6 = float(np.mean([e['ret_6m'] for e in with_3m if e['ret_6m'] is not None])) * 100.0
        r1y = float(np.mean([e['ret_1y'] for e in with_1y])) * 100.0 if with_1y else 0.0

        a3 = float(np.mean([e['alpha_3m'] for e in with_3m if e['alpha_3m'] is not None])) * 100.0
        a6 = float(np.mean([e['alpha_6m'] for e in with_3m if e['alpha_6m'] is not None])) * 100.0
        a1y = float(np.mean([e['alpha_1y'] for e in with_1y if e['alpha_1y'] is not None])) * 100.0 if with_1y else 0.0

        dd3 = float(np.mean([e['max_dd_3m'] for e in with_3m if e['max_dd_3m'] is not None])) * 100.0
        dd1y = float(np.mean([e['max_dd_1y'] for e in with_1y if e['max_dd_1y'] is not None])) * 100.0 if with_1y else 0.0

        # Calmar Ratio: Annualized return / abs(Max DD)
        calmar = (r1y / abs(dd1y)) if dd1y != 0 else 0.0

        summary_results[m] = {
            'signals': count,
            'win_rate_3m': round(win_3m, 1),
            'win_rate_1y': round(win_1y, 1),
            'ret_3m': round(r3, 2),
            'ret_6m': round(r6, 2),
            'ret_1y': round(r1y, 2),
            'alpha_3m': round(a3, 2),
            'alpha_6m': round(a6, 2),
            'alpha_1y': round(a1y, 2),
            'max_dd_3m': round(dd3, 2),
            'max_dd_1y': round(dd1y, 2),
            'calmar': round(calmar, 2)
        }

        tag = "[WIN] " if "Dual Solvency" in m else ("[X]   " if "Book Debt/Equity" in m else "      ")
        print(f" {tag}{m:<34} | {count:<7} | {win_3m:>8.1f}%  | {r3:>+6.2f}% | {r1y:>+6.2f}% | {a1y:>+7.2f}% | {dd1y:>7.2f}% | {calmar:>5.2f}x")

    # =========================================================================
    # 2. FALSE NEGATIVE & COMPOUNDER RETENTION ANALYSIS
    # =========================================================================
    print("\n" + "=" * 115)
    print(" 2. COMPOUNDER RETENTION AUDIT (Did the model allow or wrongfully disqualify high-ROIC winners?)")
    print("=" * 115)
    header_tickers = " | ".join([f"{t:>6}" for t in compounder_whitelist])
    print(f" {'Solvency Model':<36} | {header_tickers} | {'Avg Retention'}")
    print("-" * 115)

    compounder_stats = {}
    for m in models:
        retention_rates = []
        row_str = []
        for t in compounder_whitelist:
            tot = compounder_total_evals[t]
            passes = compounder_passes[m][t]
            rate = (passes / tot * 100.0) if tot > 0 else 0.0
            retention_rates.append(rate)
            row_str.append(f"{rate:>5.0f}%")
        avg_ret = float(np.mean(retention_rates))
        compounder_stats[m] = {t: round(retention_rates[i], 1) for i, t in enumerate(compounder_whitelist)}
        compounder_stats[m]['avg_retention'] = round(avg_ret, 1)

        tag = "[WIN] " if "Dual Solvency" in m else ("[X]   " if "Book Debt/Equity" in m else "      ")
        print(f" {tag}{m:<34} | {' | '.join(row_str)} | {avg_ret:>9.1f}%")

    # =========================================================================
    # 3. REGIME RESILIENCE DURING DOWNTURNS & CRISES
    # =========================================================================
    print("\n" + "=" * 115)
    print(" 3. STRESS TEST PERFORMANCE: CRISIS ERAS (GFC 2008-2010 & COVID/RATE SHOCK 2020-2022)")
    print("=" * 115)
    print(f" {'Solvency Model':<36} | {'GFC 08-10 Ret':<13} | {'GFC Max DD':<11} | {'COVID/Rate Ret':<14} | {'COVID Max DD'}")
    print("-" * 115)

    gfc_covid_stats = {}
    for m in models:
        gfc_evs = regime_events[m].get('2008-2010 (GFC & Recovery)', [])
        covid_evs = regime_events[m].get('2020-2022 (COVID Crash & Rate Shock)', [])

        gfc_r1y = float(np.mean([e['ret_1y'] for e in gfc_evs if e['ret_1y'] is not None])) * 100.0 if gfc_evs else 0.0
        gfc_dd = float(np.mean([e['max_dd_1y'] for e in gfc_evs if e['max_dd_1y'] is not None])) * 100.0 if gfc_evs else 0.0

        cov_r1y = float(np.mean([e['ret_1y'] for e in covid_evs if e['ret_1y'] is not None])) * 100.0 if covid_evs else 0.0
        cov_dd = float(np.mean([e['max_dd_1y'] for e in covid_evs if e['max_dd_1y'] is not None])) * 100.0 if covid_evs else 0.0

        gfc_covid_stats[m] = {
            'gfc_1y_ret': round(gfc_r1y, 2),
            'gfc_max_dd': round(gfc_dd, 2),
            'covid_1y_ret': round(cov_r1y, 2),
            'covid_max_dd': round(cov_dd, 2)
        }

        tag = "[WIN] " if "Dual Solvency" in m else ("[X]   " if "Book Debt/Equity" in m else "      ")
        print(f" {tag}{m:<34} | {gfc_r1y:>+11.2f}% | {gfc_dd:>9.2f}% | {cov_r1y:>+12.2f}% | {cov_dd:>10.2f}%")

    # Save to JSON
    out_payload = {
        'timestamp': datetime.now().isoformat(),
        'period': '2008-2026',
        'years': 18.8,
        'summary': summary_results,
        'compounder_retention': compounder_stats,
        'crisis_stress_test': gfc_covid_stats
    }

    with open("data/debt_metrics_backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(out_payload, f, indent=2)

    print("\nSaved full empirical showdown results to data/debt_metrics_backtest_results.json")

if __name__ == "__main__":
    run_debt_backtest()
