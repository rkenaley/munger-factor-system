"""Sample Portfolio Simulation and Tax Drag Analysis ($1,000 Initial Capital).

Simulates dynamic portfolio growth across 5 historical start points:
1. 2008-01-02 (GFC Peak - 18.8 Years)
2. 2009-03-09 (GFC Bottom - 17.5 Years)
3. 2015-01-02 (Mid-Cycle Sideways - 11.8 Years)
4. 2020-02-19 (Pre-COVID Peak - 6.6 Years)
5. 2022-01-03 (Inflation Bear Market Peak - 4.8 Years)

Compares:
1. S&P 500 Buy & Hold (SPY)
2. MUNGER 1-Year Buy & Hold (LTCG Mode, 23.8% tax)
3. MUNGER 90-Day Tactical Equities (STCG Mode, 40.8% tax)
4. MUNGER 90-Day Call Spreads (80/20 Institutional Model, 40.8% tax)

Evaluates Pre-Tax vs Post-Tax CAGR, Tax Drag (bps), Terminal Wealth, and Breakeven Tax Rates.
Generates publication-quality charts saved to docs/figures/ and brain artifact directory.
"""

import pickle
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def run_simulation():
    print("=" * 70)
    print("MUNGER SAMPLE PORTFOLIO ($1,000) & TAX DRAG SIMULATION ENGINE")
    print("=" * 70)

    # 1. Load Data
    print("Loading historical data caches...")
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

    spy_df = ohlcv_dict.get('SPY')
    if spy_df is None:
        spy_df = ohlcv_dict.get('^GSPC')

    start_global = pd.to_datetime('2007-01-03')
    end_global = pd.to_datetime('2026-10-12')
    common_dates = spy_df.loc[start_global:end_global].index

    tickers = [t for t in fund_dict.keys() if t in ohlcv_dict]
    print(f"Tracking {len(tickers)} corporate constituents over {len(common_dates)} trading sessions...")

    close_dict = {}
    for t in tickers:
        df = ohlcv_dict[t]
        close_dict[t] = df['Close'].reindex(common_dates).ffill()
    close_df = pd.DataFrame(close_dict)
    spy_close = spy_df['Close'].reindex(common_dates).ffill()

    # Precompute cross-sectional scores for all dates
    print("Precomputing cross-sectional MUNGER scores across all evaluation dates...")
    munger_scores = {} # dt -> list of (ticker, multiplier, score_dict)
    
    # Precompute at weekly frequency to allow fast portfolio lookups
    all_dates_list = list(common_dates)
    date_to_idx = {d: i for i, d in enumerate(all_dates_list)}

    for i in range(50, len(all_dates_list)):
        dt = all_dates_list[i]
        # Only evaluate every 5 days or if date is a rebalance target
        row_prices = close_df.iloc[i]
        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {}

        for t in tickers:
            p = row_prices[t]
            if pd.isna(p) or p <= 0:
                continue
            ttm_eps = None
            if t in parsed_eps:
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
            if ttm_eps is None or ttm_eps <= 0:
                today_p = float(close_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 25.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 25.0)
                years_ago = (common_dates[-1] - dt).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 12.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.35, annual_g))) ** years_ago)

            if ttm_eps > 0.05:
                pe = p / ttm_eps
                if 2.0 <= pe <= 200.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    curr_sales[t] = fund_dict[t].get('sales5y', 12.0)
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

        s_std, s_med = float(np.std(sales_vals)), float(np.median(sales_vals))
        r_std, r_med = float(np.std(roic_vals)), float(np.median(roic_vals))
        e_std, e_med = float(np.std(ey_vals)), float(np.median(ey_vals))
        d_std, d_med = float(np.std(de_inv_vals)), float(np.median(de_inv_vals))

        active_qualifying = []
        for t in valid_t:
            zg = (curr_sales[t] - s_med) / (s_std if s_std > 0 else 1.0)
            zr = (curr_roic[t] - r_med) / (r_std if r_std > 0 else 1.0)
            zy = (curr_ey[t] - e_med) / (e_std if e_std > 0 else 1.0)
            zs = (curr_de_inv[t] - d_med) / (d_std if d_std > 0 else 1.0)

            # Conjunctive hurdle: All 4 >= Median (Z >= 0)
            if zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0:
                mult = (1.0 + zg) * (1.0 + zr) * (1.0 + zy) * (1.0 + zs)
                if mult >= 12.0:
                    active_qualifying.append((t, mult, zg, zr, zy, zs))

        active_qualifying.sort(key=lambda x: x[1], reverse=True)
        munger_scores[dt] = active_qualifying

    print(f"Precomputed scores across {len(munger_scores)} trading dates.")

    # 2. Define Simulation Parameters
    start_scenarios = [
        ("2008 GFC Peak", "2008-01-02", "18.8 Yrs (Crash Stress-Test)"),
        ("2009 GFC Bottom", "2009-03-09", "17.5 Yrs (Generational Bull Run)"),
        ("2015 Mid-Cycle", "2015-01-02", "11.8 Yrs (Range-Bound / Chop)"),
        ("2020 Pre-COVID Peak", "2020-02-19", "6.6 Yrs (Vol Shock & AI Boom)"),
        ("2022 Inflation Peak", "2022-01-03", "4.8 Yrs (Rate Hike Bear Market)")
    ]

    # Tax Rates
    # High Tax Bracket (Federal Top 37% + NIIT 3.8% = 40.8% STCG; 20% + 3.8% = 23.8% LTCG)
    tax_rates_high = {"stcg": 0.408, "ltcg": 0.238, "div": 0.238}
    tax_rates_mod = {"stcg": 0.28, "ltcg": 0.15, "div": 0.15}
    tax_rates_zero = {"stcg": 0.0, "ltcg": 0.0, "div": 0.0}

    results_by_scenario = {}

    def simulate_strategy_path(start_date_str, tax_config):
        s_dt = pd.to_datetime(start_date_str)
        sim_dates = [d for d in all_dates_list if d >= s_dt]
        if len(sim_dates) < 252:
            return None

        years = (sim_dates[-1] - sim_dates[0]).days / 365.25

        # ----------------------------------------------------
        # 1. S&P 500 Buy & Hold
        # ----------------------------------------------------
        spy_start_p = spy_close.loc[sim_dates[0]]
        spy_shares = 1000.0 / spy_start_p
        spy_curve_pre = []
        spy_curve_post = []
        
        # We track dividend reinvestment (approx 1.8% annual = 0.007% daily)
        div_rate_daily = 0.018 / 252.0
        post_tax_div_rate = div_rate_daily * (1.0 - tax_config['div'])

        cur_spy_shares_pre = spy_shares
        cur_spy_shares_post = spy_shares
        spy_cost_basis = 1000.0

        for d in sim_dates:
            p = spy_close.loc[d]
            cur_spy_shares_pre *= (1.0 + div_rate_daily)
            cur_spy_shares_post *= (1.0 + post_tax_div_rate)
            v_pre = cur_spy_shares_pre * p
            v_post_unrealized = cur_spy_shares_post * p
            spy_curve_pre.append(v_pre)
            spy_curve_post.append(v_post_unrealized)

        # Terminal liquidation tax for S&P 500
        final_spy_pre = spy_curve_pre[-1]
        final_spy_post_unrealized = spy_curve_post[-1]
        total_gain_spy = max(0.0, final_spy_post_unrealized - spy_cost_basis)
        final_spy_post = final_spy_post_unrealized - (total_gain_spy * tax_config['ltcg'])

        # ----------------------------------------------------
        # 2. MUNGER 1-Year Buy & Hold (LTCG Mode)
        # ----------------------------------------------------
        # Rebalances every 252 trading days (~1 year).
        # Tax realized at each 1-year mark at LTCG rate (23.8%).
        m1y_curve_pre = [1000.0]
        m1y_curve_post = [1000.0]
        cur_m1y_pre = 1000.0
        cur_m1y_post = 1000.0

        year_steps = list(range(0, len(sim_dates), 252))
        if year_steps[-1] != len(sim_dates) - 1:
            year_steps.append(len(sim_dates) - 1)

        dates_eval_1y = [sim_dates[idx] for idx in year_steps]

        for y_idx in range(len(year_steps) - 1):
            i_start = year_steps[y_idx]
            i_end = year_steps[y_idx + 1]
            dt_start = sim_dates[i_start]
            dt_end = sim_dates[i_end]

            # Find candidates at dt_start
            cands = munger_scores.get(dt_start, [])
            if not cands:
                # search nearest prior date with scores
                for lookback in range(1, 10):
                    cand_dt = sim_dates[max(0, i_start - lookback)]
                    if cand_dt in munger_scores and munger_scores[cand_dt]:
                        cands = munger_scores[cand_dt]
                        break

            # Pick top 5 (or up to 10)
            selected_tickers = [c[0] for c in cands[:8]]
            if not selected_tickers:
                # fallback to SPY
                ret_period = spy_close.loc[dt_end] / spy_close.loc[dt_start] - 1.0
            else:
                stock_rets = []
                for st in selected_tickers:
                    p0 = close_df[st].loc[dt_start]
                    p1 = close_df[st].loc[dt_end]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        stock_rets.append(p1 / p0 - 1.0)
                ret_period = float(np.mean(stock_rets)) if stock_rets else (spy_close.loc[dt_end] / spy_close.loc[dt_start] - 1.0)

            # Pre-tax update
            cur_m1y_pre *= (1.0 + ret_period)
            
            # Post-tax update (LTCG applied to realized net gain)
            gain = cur_m1y_post * ret_period
            if gain > 0:
                cur_m1y_post = cur_m1y_post + gain * (1.0 - tax_config['ltcg'])
            else:
                cur_m1y_post = cur_m1y_post + gain # loss offsets

            m1y_curve_pre.append(cur_m1y_pre)
            m1y_curve_post.append(cur_m1y_post)

        final_m1y_pre = cur_m1y_pre
        final_m1y_post = cur_m1y_post

        # ----------------------------------------------------
        # 3. MUNGER 90-Day Tactical Cash Equities (STCG Mode)
        # ----------------------------------------------------
        # Rebalances every 63 trading days (~90 calendar days).
        # Tax realized annually (every 4 quarters) at STCG rate (40.8%).
        m90_curve_pre = [1000.0]
        m90_curve_post = [1000.0]
        cur_m90_pre = 1000.0
        cur_m90_post = 1000.0

        q_steps = list(range(0, len(sim_dates), 63))
        if q_steps[-1] != len(sim_dates) - 1:
            q_steps.append(len(sim_dates) - 1)

        annual_accum_gain = 0.0

        for q_idx in range(len(q_steps) - 1):
            i_start = q_steps[q_idx]
            i_end = q_steps[q_idx + 1]
            dt_start = sim_dates[i_start]
            dt_end = sim_dates[i_end]

            cands = munger_scores.get(dt_start, [])
            if not cands:
                for lookback in range(1, 10):
                    cand_dt = sim_dates[max(0, i_start - lookback)]
                    if cand_dt in munger_scores and munger_scores[cand_dt]:
                        cands = munger_scores[cand_dt]
                        break

            selected_tickers = [c[0] for c in cands[:8]]
            if not selected_tickers:
                ret_q = spy_close.loc[dt_end] / spy_close.loc[dt_start] - 1.0
            else:
                stock_rets = []
                for st in selected_tickers:
                    p0 = close_df[st].loc[dt_start]
                    p1 = close_df[st].loc[dt_end]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        stock_rets.append(p1 / p0 - 1.0)
                ret_q = float(np.mean(stock_rets)) if stock_rets else (spy_close.loc[dt_end] / spy_close.loc[dt_start] - 1.0)

            # Pre-tax compounding
            cur_m90_pre *= (1.0 + ret_q)

            # Accumulate gain for annual tax settlement
            q_gain = cur_m90_post * ret_q
            cur_m90_post += q_gain
            annual_accum_gain += q_gain

            # Settle tax at year end (every 4 quarters or final quarter)
            if (q_idx + 1) % 4 == 0 or q_idx == len(q_steps) - 2:
                if annual_accum_gain > 0:
                    tax_due = annual_accum_gain * tax_config['stcg']
                    cur_m90_post -= tax_due
                annual_accum_gain = 0.0

            m90_curve_pre.append(cur_m90_pre)
            m90_curve_post.append(cur_m90_post)

        final_m90_pre = cur_m90_pre
        final_m90_post = cur_m90_post

        # ----------------------------------------------------
        # 4. MUNGER 90-Day Call Spreads (The 80/20 Model)
        # ----------------------------------------------------
        # 80% Cash earning risk-free yield (historical ~3.5% avg = 0.875% per quarter)
        # 20% in 90-Day Bull Call Spreads (95C / 110C)
        # Debit = 6.08% of spot ($6.08 on $100 stock). Max payoff = $15.00 ($15.00 - $6.08 = $8.92 profit = +146.7% ROC)
        # Options gains + cash interest taxed annually at STCG rate (40.8%).
        m8020_curve_pre = [1000.0]
        m8020_curve_post = [1000.0]
        cur_m8020_pre = 1000.0
        cur_m8020_post = 1000.0
        annual_accum_gain_8020 = 0.0

        r_tbill_q = 0.00875 # approx 3.5% annualized risk-free rate

        for q_idx in range(len(q_steps) - 1):
            i_start = q_steps[q_idx]
            i_end = q_steps[q_idx + 1]
            dt_start = sim_dates[i_start]
            dt_end = sim_dates[i_end]

            cands = munger_scores.get(dt_start, [])
            if not cands:
                for lookback in range(1, 10):
                    cand_dt = sim_dates[max(0, i_start - lookback)]
                    if cand_dt in munger_scores and munger_scores[cand_dt]:
                        cands = munger_scores[cand_dt]
                        break

            selected_tickers = [c[0] for c in cands[:8]]
            if not selected_tickers:
                stock_ret_q = spy_close.loc[dt_end] / spy_close.loc[dt_start] - 1.0
                spread_rocs = [min(1.467, max(-1.0, (max(0.0, min(15.0, 100.0 * (1.0 + stock_ret_q) - 96.0)) - 6.08) / 6.08))]
            else:
                spread_rocs = []
                for st in selected_tickers:
                    p0 = close_df[st].loc[dt_start]
                    p1 = close_df[st].loc[dt_end]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        ret_i = p1 / p0 - 1.0
                        # 95C / 110C normalized payoff ($100 stock base: K1=96.0, K2=111.0, Debit=6.08)
                        s_term = 100.0 * (1.0 + ret_i)
                        term_val = max(0.0, min(15.0, s_term - 96.0))
                        roc_i = (term_val - 6.08) / 6.08
                        spread_rocs.append(roc_i)

            avg_spread_roc = float(np.mean(spread_rocs)) if spread_rocs else 0.0

            # Total 80/20 portfolio quarterly return:
            # 80% earning r_tbill_q, 20% earning avg_spread_roc
            port_q_ret = 0.80 * r_tbill_q + 0.20 * avg_spread_roc

            cur_m8020_pre *= (1.0 + port_q_ret)
            
            q_gain_8020 = cur_m8020_post * port_q_ret
            cur_m8020_post += q_gain_8020
            annual_accum_gain_8020 += q_gain_8020

            if (q_idx + 1) % 4 == 0 or q_idx == len(q_steps) - 2:
                if annual_accum_gain_8020 > 0:
                    tax_due_8020 = annual_accum_gain_8020 * tax_config['stcg']
                    cur_m8020_post -= tax_due_8020
                annual_accum_gain_8020 = 0.0

            m8020_curve_pre.append(cur_m8020_pre)
            m8020_curve_post.append(cur_m8020_post)

        final_m8020_pre = cur_m8020_pre
        final_m8020_post = cur_m8020_post

        # Compute CAGRs and Drag
        cagr_spy_pre = (final_spy_pre / 1000.0) ** (1.0 / years) - 1.0
        cagr_spy_post = (final_spy_post / 1000.0) ** (1.0 / years) - 1.0

        cagr_m1y_pre = (final_m1y_pre / 1000.0) ** (1.0 / years) - 1.0
        cagr_m1y_post = (final_m1y_post / 1000.0) ** (1.0 / years) - 1.0

        cagr_m90_pre = (final_m90_pre / 1000.0) ** (1.0 / years) - 1.0
        cagr_m90_post = (final_m90_post / 1000.0) ** (1.0 / years) - 1.0

        cagr_m8020_pre = (final_m8020_pre / 1000.0) ** (1.0 / years) - 1.0
        cagr_m8020_post = (final_m8020_post / 1000.0) ** (1.0 / years) - 1.0

        return {
            "years": years,
            "dates_sim": sim_dates,
            "dates_1y": dates_eval_1y,
            "dates_q": [sim_dates[idx] for idx in q_steps],
            "SPY": {
                "final_pre": final_spy_pre, "final_post": final_spy_post,
                "cagr_pre": cagr_spy_pre, "cagr_post": cagr_spy_post,
                "drag_bps": (cagr_spy_pre - cagr_spy_post) * 10000.0,
                "curve_pre": spy_curve_pre, "curve_post": spy_curve_post
            },
            "MUNGER_1Y": {
                "final_pre": final_m1y_pre, "final_post": final_m1y_post,
                "cagr_pre": cagr_m1y_pre, "cagr_post": cagr_m1y_post,
                "drag_bps": (cagr_m1y_pre - cagr_m1y_post) * 10000.0,
                "curve_pre": m1y_curve_pre, "curve_post": m1y_curve_post
            },
            "MUNGER_90D": {
                "final_pre": final_m90_pre, "final_post": final_m90_post,
                "cagr_pre": cagr_m90_pre, "cagr_post": cagr_m90_post,
                "drag_bps": (cagr_m90_pre - cagr_m90_post) * 10000.0,
                "curve_pre": m90_curve_pre, "curve_post": m90_curve_post
            },
            "MUNGER_8020": {
                "final_pre": final_m8020_pre, "final_post": final_m8020_post,
                "cagr_pre": cagr_m8020_pre, "cagr_post": cagr_m8020_post,
                "drag_bps": (cagr_m8020_pre - cagr_m8020_post) * 10000.0,
                "curve_pre": m8020_curve_pre, "curve_post": m8020_curve_post
            }
        }

    # Execute simulation across all 5 start scenarios under High Tax Bracket
    for label, s_str, desc in start_scenarios:
        print(f"Simulating scenario: {label} ({s_str})...")
        res = simulate_strategy_path(s_str, tax_rates_high)
        if res:
            results_by_scenario[label] = res

    # 3. Print Summary Performance Table
    print("\n" + "=" * 90)
    print(f"{'Scenario':<20} | {'Years':<5} | {'Metric':<10} | {'S&P 500 (SPY)':<14} | {'MUNGER 1Y (LTCG)':<16} | {'MUNGER 90D (STCG)':<17} | {'MUNGER 80/20 Spreads':<18}")
    print("-" * 90)

    for label, res in results_by_scenario.items():
        yrs = f"{res['years']:.1f}y"
        # Pre-tax final
        f_spy_pre = f"${res['SPY']['final_pre']:,.0f}"
        f_1y_pre = f"${res['MUNGER_1Y']['final_pre']:,.0f}"
        f_90_pre = f"${res['MUNGER_90D']['final_pre']:,.0f}"
        f_80_pre = f"${res['MUNGER_8020']['final_pre']:,.0f}"

        # Post-tax final
        f_spy_post = f"${res['SPY']['final_post']:,.0f}"
        f_1y_post = f"${res['MUNGER_1Y']['final_post']:,.0f}"
        f_90_post = f"${res['MUNGER_90D']['final_post']:,.0f}"
        f_80_post = f"${res['MUNGER_8020']['final_post']:,.0f}"

        # CAGRs
        c_spy = f"{res['SPY']['cagr_post']*100:.1f}% ({res['SPY']['drag_bps']:.0f}bp)"
        c_1y = f"{res['MUNGER_1Y']['cagr_post']*100:.1f}% ({res['MUNGER_1Y']['drag_bps']:.0f}bp)"
        c_90 = f"{res['MUNGER_90D']['cagr_post']*100:.1f}% ({res['MUNGER_90D']['drag_bps']:.0f}bp)"
        c_80 = f"{res['MUNGER_8020']['cagr_post']*100:.1f}% ({res['MUNGER_8020']['drag_bps']:.0f}bp)"

        print(f"{label:<20} | {yrs:<5} | {'Pre-Tax $':<10} | {f_spy_pre:<14} | {f_1y_pre:<16} | {f_90_pre:<17} | {f_80_pre:<18}")
        print(f"{'':<20} | {'':<5} | {'Post-Tax $':<10} | {f_spy_post:<14} | {f_1y_post:<16} | {f_90_post:<17} | {f_80_post:<18}")
        print(f"{'':<20} | {'':<5} | {'Net CAGR':<10} | {c_spy:<14} | {c_1y:<16} | {c_90:<17} | {c_80:<18}")
        print("-" * 90)

    # 4. Tax Bracket Sensitivity Sweep (0% to 50% STCG)
    print("\nRunning Tax Rate Sensitivity Sweep (Breakeven Analysis)...")
    stcg_rates = np.linspace(0.0, 0.50, 11)
    tax_sweep_results = []
    
    # Evaluate over 2008-2026 (18.8 years)
    for stcg in stcg_rates:
        # standard statutory relationship: LTCG approx ~60% of STCG
        ltcg = stcg * 0.583 # 40.8% -> 23.8%
        t_cfg = {"stcg": stcg, "ltcg": ltcg, "div": ltcg}
        sweep_res = simulate_strategy_path("2008-01-02", t_cfg)
        tax_sweep_results.append({
            "stcg_rate": stcg,
            "ltcg_rate": ltcg,
            "spy_cagr": sweep_res["SPY"]["cagr_post"],
            "m1y_cagr": sweep_res["MUNGER_1Y"]["cagr_post"],
            "m90_cagr": sweep_res["MUNGER_90D"]["cagr_post"],
            "m8020_cagr": sweep_res["MUNGER_8020"]["cagr_post"],
            "spy_final": sweep_res["SPY"]["final_post"],
            "m1y_final": sweep_res["MUNGER_1Y"]["final_post"],
            "m90_final": sweep_res["MUNGER_90D"]["final_post"],
            "m8020_final": sweep_res["MUNGER_8020"]["final_post"],
        })

    # Save results to json
    out_dir = Path("data")
    fig_dir = Path("docs/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    art_dir = Path("C:/Users/Ryan/.gemini/antigravity/brain/b7f84560-ca04-410e-856b-4cff61e44c4f")

    # Serialize JSON
    save_data = {
        "summary": {k: {sk: {ssk: ssv for ssk, ssv in sv.items() if not ssk.startswith("curve")} for sk, sv in v.items() if sk in ["SPY", "MUNGER_1Y", "MUNGER_90D", "MUNGER_8020"]} for k, v in results_by_scenario.items()},
        "tax_sweep": tax_sweep_results
    }
    with open(out_dir / "sample_portfolio_simulation_results.json", "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2)

    # ----------------------------------------------------
    # 5. GENERATE PUBLICATION-QUALITY CHARTS
    # ----------------------------------------------------
    print("\nGenerating publication figures with matplotlib...")
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    colors = {
        "SPY": "#1f77b4",          # Deep Blue
        "MUNGER_1Y": "#2ca02c",    # Forest Green
        "MUNGER_90D": "#ff7f0e",   # Amber / Orange
        "MUNGER_8020": "#d62728"   # Crimson Red
    }

    # -------------------------------------------------------------------------
    # FIGURE 1: 5-Panel Multi-Scenario Growth Curves ($1,000 Initial Capital)
    # -------------------------------------------------------------------------
    fig, axes = plt.subplots(3, 2, figsize=(16, 15))
    axes = axes.flatten()

    for idx, (label, s_str, desc) in enumerate(start_scenarios):
        ax = axes[idx]
        res = results_by_scenario[label]
        
        d_spy = res["dates_sim"]
        d_1y = res["dates_1y"]
        d_q = res["dates_q"]

        # Plot Post-Tax Curves (Solid Lines)
        ax.plot(d_spy, res["SPY"]["curve_post"], label=f"S&P 500 (B&H): ${res['SPY']['final_post']:,.0f} ({res['SPY']['cagr_post']*100:.1f}%)", color=colors["SPY"], linewidth=2.0)
        ax.plot(d_1y, res["MUNGER_1Y"]["curve_post"], label=f"MUNGER 1Y (LTCG): ${res['MUNGER_1Y']['final_post']:,.0f} ({res['MUNGER_1Y']['cagr_post']*100:.1f}%)", color=colors["MUNGER_1Y"], linewidth=2.5)
        ax.plot(d_q, res["MUNGER_90D"]["curve_post"], label=f"MUNGER 90D Tactical: ${res['MUNGER_90D']['final_post']:,.0f} ({res['MUNGER_90D']['cagr_post']*100:.1f}%)", color=colors["MUNGER_90D"], linewidth=2.0, linestyle="--")
        ax.plot(d_q, res["MUNGER_8020"]["curve_post"], label=f"MUNGER 80/20 Spreads: ${res['MUNGER_8020']['final_post']:,.0f} ({res['MUNGER_8020']['cagr_post']*100:.1f}%)", color=colors["MUNGER_8020"], linewidth=2.8)

        ax.set_yscale('log')
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
        ax.set_title(f"{label} ({desc}) - Post-Tax Compounding", fontsize=12, fontweight='bold', pad=8)
        ax.set_ylabel("Portfolio Value ($ Log Scale)")
        ax.grid(True, which='both', linestyle=':', alpha=0.6)
        ax.legend(loc='upper left', fontsize=8.5, frameon=True, framealpha=0.9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

    # Subplot 6: Summary Comparison Bar Chart (2008-2026 Final Wealth)
    ax_bar = axes[5]
    res_2008 = results_by_scenario["2008 GFC Peak"]
    strats = ["S&P 500", "MUNGER 1Y\n(LTCG)", "MUNGER 90D\n(Tactical)", "MUNGER 80/20\n(Options)"]
    pre_vals = [res_2008["SPY"]["final_pre"], res_2008["MUNGER_1Y"]["final_pre"], res_2008["MUNGER_90D"]["final_pre"], res_2008["MUNGER_8020"]["final_pre"]]
    post_vals = [res_2008["SPY"]["final_post"], res_2008["MUNGER_1Y"]["final_post"], res_2008["MUNGER_90D"]["final_post"], res_2008["MUNGER_8020"]["final_post"]]

    x = np.arange(len(strats))
    width = 0.35
    b1 = ax_bar.bar(x - width/2, pre_vals, width, label='Pre-Tax Wealth', color='#9ecae1', edgecolor='black', alpha=0.85)
    b2 = ax_bar.bar(x + width/2, post_vals, width, label='Post-Tax Wealth (High Bracket)', color=['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728'], edgecolor='black')

    ax_bar.set_yscale('log')
    ax_bar.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax_bar.set_title("2008–2026 Cumulative Terminal Wealth ($1,000 Initial)", fontsize=12, fontweight='bold', pad=8)
    ax_bar.set_xticks(x)
    ax_bar.set_xticklabels(strats, fontsize=9.5, fontweight='bold')
    ax_bar.set_ylabel("Terminal Wealth ($ Log Scale)")
    ax_bar.legend(loc='upper left', fontsize=9)
    ax_bar.grid(True, which='both', linestyle=':', alpha=0.6)

    for bar, val in zip(b2, post_vals):
        ax_bar.annotate(f"${val:,.0f}",
                        xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 4), textcoords="offset points",
                        ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    plt.suptitle("MUNGER Factor Strategy: $1,000 Portfolio Growth Across Diverse Market Regimes (Post-Tax)", fontsize=15, fontweight='bold', y=0.995)
    plt.tight_layout()
    fig_path1 = fig_dir / "munger_portfolio_growth_all_scenarios.png"
    plt.savefig(fig_path1, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved Figure 1 to: {fig_path1}")

    # -------------------------------------------------------------------------
    # FIGURE 2: Tax Drag Anatomy & Breakeven Analysis
    # -------------------------------------------------------------------------
    fig2, (ax_drag, ax_sweep) = plt.subplots(1, 2, figsize=(15, 6))

    # Left: Tax Drag in Basis Points (2008-2026)
    drags = [res_2008["SPY"]["drag_bps"], res_2008["MUNGER_1Y"]["drag_bps"], res_2008["MUNGER_90D"]["drag_bps"], res_2008["MUNGER_8020"]["drag_bps"]]
    cagrs_pre = [res_2008["SPY"]["cagr_pre"]*100, res_2008["MUNGER_1Y"]["cagr_pre"]*100, res_2008["MUNGER_90D"]["cagr_pre"]*100, res_2008["MUNGER_8020"]["cagr_pre"]*100]
    cagrs_post = [res_2008["SPY"]["cagr_post"]*100, res_2008["MUNGER_1Y"]["cagr_post"]*100, res_2008["MUNGER_90D"]["cagr_post"]*100, res_2008["MUNGER_8020"]["cagr_post"]*100]

    x_d = np.arange(len(strats))
    ax_drag.bar(x_d - 0.2, cagrs_pre, 0.4, label='Pre-Tax CAGR (%)', color='#b0c4de', edgecolor='black')
    bars_post = ax_drag.bar(x_d + 0.2, cagrs_post, 0.4, label='Post-Tax CAGR (%)', color=['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728'], edgecolor='black')

    ax_drag.set_title("Annualized Compounding: Pre-Tax vs. Post-Tax (2008–2026)", fontsize=11.5, fontweight='bold')
    ax_drag.set_xticks(x_d)
    ax_drag.set_xticklabels(strats, fontsize=9.5, fontweight='bold')
    ax_drag.set_ylabel("Annualized CAGR (%)")
    ax_drag.grid(True, linestyle=':', alpha=0.6)
    ax_drag.legend(loc='upper left', fontsize=9.5)

    for i, (b, d_bp) in enumerate(zip(bars_post, drags)):
        ax_drag.annotate(f"Drag:\n-{d_bp:.0f} bps",
                         xy=(b.get_x() + b.get_width() / 2, b.get_height() / 2),
                         ha='center', va='center', fontsize=8.5, fontweight='bold', color='white')

    # Right: Breakeven Sensitivity Curve (CAGR vs STCG Tax Rate)
    stcg_pct = [r["stcg_rate"]*100 for r in tax_sweep_results]
    spy_sweep_cagr = [r["spy_cagr"]*100 for r in tax_sweep_results]
    m1y_sweep_cagr = [r["m1y_cagr"]*100 for r in tax_sweep_results]
    m90_sweep_cagr = [r["m90_cagr"]*100 for r in tax_sweep_results]
    m80_sweep_cagr = [r["m8020_cagr"]*100 for r in tax_sweep_results]

    ax_sweep.plot(stcg_pct, spy_sweep_cagr, label="S&P 500 (B&H)", color=colors["SPY"], linewidth=2.2, linestyle=':')
    ax_sweep.plot(stcg_pct, m1y_sweep_cagr, label="MUNGER 1-Year Hold (LTCG Advantaged)", color=colors["MUNGER_1Y"], linewidth=2.8)
    ax_sweep.plot(stcg_pct, m90_sweep_cagr, label="MUNGER 90-Day Tactical (STCG Taxed)", color=colors["MUNGER_90D"], linewidth=2.2, linestyle='--')
    ax_sweep.plot(stcg_pct, m80_sweep_cagr, label="MUNGER 80/20 Options Model", color=colors["MUNGER_8020"], linewidth=3.0)

    ax_sweep.axvline(40.8, color='darkred', linestyle='--', alpha=0.7, label='Top Federal + NIIT Bracket (40.8%)')
    ax_sweep.axvline(28.0, color='gray', linestyle=':', alpha=0.7, label='Moderate Bracket (28.0%)')

    ax_sweep.set_title("Is 1-Year Hold (LTCG) Advantaged? Net CAGR vs. Tax Rate", fontsize=11.5, fontweight='bold')
    ax_sweep.set_xlabel("Short-Term Capital Gains Tax Rate (%) [LTCG = STCG × 0.583]")
    ax_sweep.set_ylabel("Net Post-Tax CAGR (%)")
    ax_sweep.grid(True, linestyle=':', alpha=0.6)
    ax_sweep.legend(loc='lower left', fontsize=8.5, frameon=True, framealpha=0.9)

    plt.tight_layout()
    fig_path2 = fig_dir / "munger_tax_drag_and_breakeven_analysis.png"
    plt.savefig(fig_path2, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved Figure 2 to: {fig_path2}")

    # Copy generated figures to artifact folder
    import shutil
    shutil.copy(fig_path1, art_dir / "munger_portfolio_growth_all_scenarios.png")
    shutil.copy(fig_path2, art_dir / "munger_tax_drag_and_breakeven_analysis.png")
    print(f"Copied figures to artifacts directory: {art_dir}")

if __name__ == "__main__":
    run_simulation()
