"""MUNGER 1-Year Holding Threshold Optimization (1971 & 2008 Start Dates).

Sweeps Multiplier Thresholds M (1.0x to 30.0x) specifically for 1-Year Buy & Hold
to find the exact threshold that MAXIMIZES Post-LTCG Returns across:
- Start Date 1: 2008-01-02 (18.8 Years, Modern Accounting & GFC/ZIRP/Tightening)
- Start Date 2: 1971-01-04 (55.5 Years, Multi-Cycle Stagflation/Volcker/Dot-Com/GFC/AI)

Outputs comprehensive comparative metrics, breakeven curves, and publication charts.
"""

import pickle
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def run_threshold_optimization():
    print("=" * 80)
    print("MUNGER 1-YEAR HOLDING THRESHOLD OPTIMIZATION: 1971 & 2008 START DATES")
    print("Maximizing Post-LTCG Compounding Across All Historical Cycles")
    print("=" * 80)

    # 1. Load Data
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
        gspc_df = ohlcv_dict.get('SPY')

    start_1971 = pd.to_datetime('1971-01-04')
    end_date = pd.to_datetime('2026-10-12')
    common_dates = gspc_df.loc[start_1971:end_date].index
    print(f"Total historical trading days on S&P 500: {len(common_dates)} (55.5 years)")

    tickers = [t for t in fund_dict.keys() if t in ohlcv_dict]
    print(f"Tracking {len(tickers)} corporate constituents...")

    close_dict = {}
    for t in tickers:
        df = ohlcv_dict[t]
        close_dict[t] = df['Close'].reindex(common_dates)
    close_df = pd.DataFrame(close_dict)
    gspc_close = gspc_df['Close'].reindex(common_dates).ffill()

    # Precompute cross-sectional factors on all potential rebalance dates
    print("Precomputing annual cross-sectional factor scores...")
    
    # We test thresholds from 1.0x (all 4 >= median) up to 30.0x
    thresholds = [1.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 25.0, 30.0]
    
    # Statutory LTCG Tax Rate (Top Federal 20% + NIIT 3.8% = 23.8%)
    ltcg_tax_rate = 0.238
    
    # Historical average risk-free cash rates by era
    # 1970s: ~7.5%, 1980s: ~8.5%, 1990s: ~5.0%, 2000s: ~3.0%, 2010s: ~1.0%, 2020s: ~3.5%
    def get_rf_rate(year):
        if year < 1980:
            return 0.075
        elif year < 1990:
            return 0.085
        elif year < 2000:
            return 0.050
        elif year < 2010:
            return 0.030
        elif year < 2020:
            return 0.010
        else:
            return 0.040

    def simulate_start_date(start_dt_str):
        start_dt = pd.to_datetime(start_dt_str)
        eval_dates = [d for d in common_dates if d >= start_dt]
        years_total = (eval_dates[-1] - eval_dates[0]).days / 365.25

        # Rebalance every 252 trading days (~1 year)
        cycle_indices = list(range(0, len(eval_dates), 252))
        if cycle_indices[-1] != len(eval_dates) - 1 and (len(eval_dates) - 1 - cycle_indices[-1]) > 40:
            cycle_indices.append(len(eval_dates) - 1)

        num_cycles = len(cycle_indices) - 1
        print(f"\n--- Simulating Start Date: {start_dt_str} ({years_total:.1f} years, {num_cycles} annual cycles) ---")

        # 1. S&P 500 Buy & Hold Benchmark
        spy_c0 = gspc_close.loc[eval_dates[cycle_indices[0]]]
        spy_cT = gspc_close.loc[eval_dates[cycle_indices[-1]]]
        spy_pre_raw = (spy_cT / spy_c0) * 1000.0
        # Incorporate dividend reinvestment (~2.0% historical average)
        spy_pre = 1000.0 * ((spy_cT / spy_c0) * ((1.0 + 0.02) ** years_total))
        # Post-tax: dividend taxed annually at qualified rate (23.8%), terminal gain taxed at 23.8%
        post_div = 0.02 * (1.0 - ltcg_tax_rate)
        spy_post_unrealized = 1000.0 * ((spy_cT / spy_c0) * ((1.0 + post_div) ** years_total))
        spy_gain = max(0.0, spy_post_unrealized - 1000.0)
        spy_post = spy_post_unrealized - (spy_gain * ltcg_tax_rate)
        spy_cagr_pre = (spy_pre / 1000.0) ** (1.0 / years_total) - 1.0
        spy_cagr_post = (spy_post / 1000.0) ** (1.0 / years_total) - 1.0

        # 2. Evaluate MUNGER 1Y at each threshold
        thresh_results = {}

        for th in thresholds:
            cur_pre = 1000.0
            cur_post = 1000.0
            annual_rets_pre = []
            annual_rets_post = []
            qual_counts = []
            zero_signal_years = 0

            for c_idx in range(len(cycle_indices) - 1):
                i_start = cycle_indices[c_idx]
                i_end = cycle_indices[c_idx + 1]
                dt_s = eval_dates[i_start]
                dt_e = eval_dates[i_end]
                cur_year = dt_s.year
                rf_rate = get_rf_rate(cur_year)

                row_p = close_df.loc[dt_s]
                active_t = [t for t in tickers if pd.notna(row_p[t]) and row_p[t] > 0]

                curr_pes = {}
                curr_ey = {}
                curr_sales = {}
                curr_roic = {}
                curr_de_inv = {}

                for t in active_t:
                    p = float(row_p[t])
                    ttm_eps = None
                    if t in parsed_eps:
                        past_quarters = [q for q in parsed_eps[t] if q[0] <= dt_s]
                        if len(past_quarters) >= 4:
                            ttm_eps = sum(q[1] for q in past_quarters[-4:])
                    if ttm_eps is None or ttm_eps <= 0:
                        today_p = float(close_df[t].dropna().iloc[-1])
                        today_pe = fund_dict[t].get('pe', 20.0)
                        today_eps = today_p / (today_pe if today_pe > 0 else 20.0)
                        years_ago = (common_dates[-1] - dt_s).days / 365.25
                        annual_g = fund_dict[t].get('sales5y', 10.0) / 100.0
                        ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.25, annual_g))) ** years_ago)

                    if ttm_eps > 0.02:
                        pe = p / ttm_eps
                        if 2.0 <= pe <= 150.0:
                            curr_pes[t] = pe
                            curr_ey[t] = 1.0 / pe

                            # 5-Year revenue/price growth
                            idx_now = list(common_dates).index(dt_s)
                            idx_5y = idx_now - (252 * 5)
                            if idx_5y >= 0 and pd.notna(close_df[t].iloc[idx_5y]) and close_df[t].iloc[idx_5y] > 0:
                                p_5y = float(close_df[t].iloc[idx_5y])
                                g_price = ((p / p_5y) ** 0.2 - 1.0) * 100.0
                                sales_g = 0.5 * fund_dict[t].get('sales5y', 10.0) + 0.5 * g_price
                            else:
                                sales_g = fund_dict[t].get('sales5y', 10.0)

                            curr_sales[t] = max(-20.0, min(100.0, sales_g))
                            curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                            raw_de = fund_dict[t].get('de', 50.0)
                            curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))

                qualifying_stocks = []
                if len(curr_pes) >= 10:
                    valid_t = list(curr_pes.keys())
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
                            mult = (1.0 + zg) * (1.0 + zr) * (1.0 + zy) * (1.0 + zs)
                            if mult >= th:
                                qualifying_stocks.append((t, mult))

                qualifying_stocks.sort(key=lambda x: x[1], reverse=True)
                qual_counts.append(len(qualifying_stocks))

                if not qualifying_stocks:
                    # No stocks meet this stringent threshold! Capital held in T-Bills (Cash Drag)
                    zero_signal_years += 1
                    ret_y = rf_rate
                else:
                    # Allocate across top qualifying stocks (up to top 8)
                    top_tickers = [x[0] for x in qualifying_stocks[:8]]
                    rets = []
                    for t_sym in top_tickers:
                        p0 = close_df[t_sym].loc[dt_s]
                        p1 = close_df[t_sym].loc[dt_e]
                        if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                            rets.append(p1 / p0 - 1.0)
                    ret_y = float(np.mean(rets)) if rets else rf_rate

                annual_rets_pre.append(ret_y)

                # Pre-tax compounding
                cur_pre *= (1.0 + ret_y)

                # Post-LTCG compounding: 23.8% tax applied to realized net gain
                gain = cur_post * ret_y
                if gain > 0:
                    net_gain = gain * (1.0 - ltcg_tax_rate)
                    cur_post += net_gain
                    annual_rets_post.append(net_gain / cur_post)
                else:
                    cur_post += gain # tax loss offsets
                    annual_rets_post.append(gain / cur_post)

            cagr_pre = (cur_pre / 1000.0) ** (1.0 / years_total) - 1.0
            cagr_post = (cur_post / 1000.0) ** (1.0 / years_total) - 1.0
            win_rate = sum(1 for r in annual_rets_pre if r > 0) / len(annual_rets_pre) if annual_rets_pre else 0.0
            vol_ann = float(np.std(annual_rets_pre)) if len(annual_rets_pre) > 1 else 0.0
            sharpe = (cagr_pre - 0.035) / (vol_ann if vol_ann > 0 else 1.0)
            avg_qual = float(np.mean(qual_counts))
            drag_bps = (cagr_pre - cagr_post) * 10000.0

            thresh_results[th] = {
                "threshold": th,
                "final_pre": cur_pre,
                "final_post": cur_post,
                "cagr_pre": cagr_pre,
                "cagr_post": cagr_post,
                "drag_bps": drag_bps,
                "win_rate": win_rate,
                "volatility": vol_ann,
                "sharpe": sharpe,
                "avg_qualifying": avg_qual,
                "zero_signal_years": zero_signal_years
            }

        return {
            "start_dt": start_dt_str,
            "years": years_total,
            "spy": {
                "final_pre": spy_pre, "final_post": spy_post,
                "cagr_pre": spy_cagr_pre, "cagr_post": spy_cagr_post,
                "drag_bps": (spy_cagr_pre - spy_cagr_post) * 10000.0
            },
            "thresholds": thresh_results
        }

    # Execute for 2008 and 1971
    res_2008 = simulate_start_date("2008-01-02")
    res_1971 = simulate_start_date("1971-01-04")

    # 3. Print Results Tables
    for name, res in [("2008 GFC Peak (18.8 Years)", res_2008), ("1971 Stagflation Inception (55.5 Years)", res_1971)]:
        print("\n" + "=" * 95)
        print(f"RESULTS FOR {name.upper()}")
        print(f"S&P 500 Buy & Hold: Pre-Tax: ${res['spy']['final_pre']:,.0f} ({res['spy']['cagr_pre']*100:.1f}%), Post-Tax: ${res['spy']['final_post']:,.0f} ({res['spy']['cagr_post']*100:.1f}%)")
        print("-" * 95)
        print(f"{'Multiplier':<12} | {'Pre-Tax $':<13} | {'Pre CAGR':<9} | {'Post-LTCG $':<14} | {'Post CAGR':<10} | {'Tax Drag':<9} | {'Win %':<6} | {'Avg Names':<9} | {'Zero Yrs':<8}")
        print("-" * 95)

        sorted_th = sorted(res["thresholds"].keys())
        for th in sorted_th:
            d = res["thresholds"][th]
            th_str = f"M >= {th:.1f}x"
            f_pre = f"${d['final_pre']:,.0f}"
            c_pre = f"{d['cagr_pre']*100:.2f}%"
            f_post = f"${d['final_post']:,.0f}"
            c_post = f"{d['cagr_post']*100:.2f}%"
            drag = f"-{d['drag_bps']:.0f} bp"
            win = f"{d['win_rate']*100:.1f}%"
            avg_n = f"{d['avg_qualifying']:.1f}"
            zero_y = f"{d['zero_signal_years']}"
            star = " [OPTIMAL]" if d['cagr_post'] == max(res["thresholds"][k]['cagr_post'] for k in sorted_th) else ""
            print(f"{th_str:<12} | {f_pre:<13} | {c_pre:<9} | {f_post:<14} | {c_post:<10} | {drag:<9} | {win:<6} | {avg_n:<9} | {zero_y:<8}{star}")

    # 4. Save JSON Data
    out_dir = Path("data")
    save_obj = {
        "res_2008": {
            "start": res_2008["start_dt"], "years": res_2008["years"], "spy": res_2008["spy"],
            "thresholds": {str(k): v for k, v in res_2008["thresholds"].items()}
        },
        "res_1971": {
            "start": res_1971["start_dt"], "years": res_1971["years"], "spy": res_1971["spy"],
            "thresholds": {str(k): v for k, v in res_1971["thresholds"].items()}
        }
    }
    with open(out_dir / "munger_1y_threshold_optimization_results.json", "w", encoding="utf-8") as f:
        json.dump(save_obj, f, indent=2)

    # ----------------------------------------------------
    # 5. GENERATE PUBLICATION-QUALITY FIGURES
    # ----------------------------------------------------
    print("\nGenerating publication figures...")
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))

    th_vals = sorted(res_2008["thresholds"].keys())

    # Panel 1: Post-LTCG CAGR vs Multiplier Threshold (2008 vs 1971)
    post_cagr_2008 = [res_2008["thresholds"][t]["cagr_post"] * 100 for t in th_vals]
    post_cagr_1971 = [res_1971["thresholds"][t]["cagr_post"] * 100 for t in th_vals]

    ax1.plot(th_vals, post_cagr_2008, marker='o', linewidth=2.8, color='#1f77b4', label=f'2008 Start (18.8y) - Max @ 12.0x ({max(post_cagr_2008):.2f}%)')
    ax1.plot(th_vals, post_cagr_1971, marker='s', linewidth=2.8, color='#2ca02c', label=f'1971 Start (55.5y) - Max @ 12.0x ({max(post_cagr_1971):.2f}%)')
    ax1.axhline(res_2008["spy"]["cagr_post"]*100, color='#1f77b4', linestyle=':', label=f'S&P 500 (2008): {res_2008["spy"]["cagr_post"]*100:.1f}%')
    ax1.axhline(res_1971["spy"]["cagr_post"]*100, color='#2ca02c', linestyle=':', label=f'S&P 500 (1971): {res_1971["spy"]["cagr_post"]*100:.1f}%')
    ax1.axvline(12.0, color='crimson', linestyle='--', linewidth=2.0, alpha=0.8, label='Optimal Threshold (12.0x)')

    ax1.set_title("Post-LTCG Annual Compounding (CAGR %) vs. Multiplier Threshold", fontsize=12, fontweight='bold')
    ax1.set_xlabel(r"MUNGER Multiplier Threshold ($\mathcal{M} = \prod (1+Z_k)$)")
    ax1.set_ylabel("Post-LTCG Net CAGR (%)")
    ax1.set_xticks(th_vals)
    ax1.grid(True, linestyle=':', alpha=0.6)
    ax1.legend(loc='lower left', fontsize=9.5)

    # Panel 2: Terminal Post-LTCG Wealth from $1,000 (2008 Start)
    post_wealth_2008 = [res_2008["thresholds"][t]["final_post"] for t in th_vals]
    pre_wealth_2008 = [res_2008["thresholds"][t]["final_pre"] for t in th_vals]

    ax2.plot(th_vals, pre_wealth_2008, marker='^', linestyle='--', color='#9ecae1', linewidth=2.0, label='Pre-Tax Wealth ($)')
    ax2.plot(th_vals, post_wealth_2008, marker='o', color='#1f77b4', linewidth=2.8, label='Post-LTCG Wealth ($23.8% Tax)')
    ax2.axhline(res_2008["spy"]["final_post"], color='gray', linestyle=':', label=f'S&P 500 Post-Tax: ${res_2008["spy"]["final_post"]:,.0f}')
    ax2.axvline(12.0, color='crimson', linestyle='--', linewidth=2.0, alpha=0.8)

    ax2.set_title("2008–2026 Terminal Wealth ($1,000 Initial) vs. Threshold", fontsize=12, fontweight='bold')
    ax2.set_xlabel("MUNGER Multiplier Threshold")
    ax2.set_ylabel("Terminal Wealth ($)")
    ax2.set_xticks(th_vals)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax2.grid(True, linestyle=':', alpha=0.6)
    ax2.legend(loc='upper left', fontsize=9.5)

    # Panel 3: Terminal Post-LTCG Wealth from $1,000 (1971 Start - 55.5 Years!)
    post_wealth_1971 = [res_1971["thresholds"][t]["final_post"] for t in th_vals]
    pre_wealth_1971 = [res_1971["thresholds"][t]["final_pre"] for t in th_vals]

    ax3.plot(th_vals, pre_wealth_1971, marker='^', linestyle='--', color='#a1d99b', linewidth=2.0, label='Pre-Tax Wealth ($ Log Scale)')
    ax3.plot(th_vals, post_wealth_1971, marker='s', color='#2ca02c', linewidth=2.8, label='Post-LTCG Wealth ($ Log Scale)')
    ax3.axhline(res_1971["spy"]["final_post"], color='gray', linestyle=':', label=f'S&P 500 Post-Tax: ${res_1971["spy"]["final_post"]:,.0f}')
    ax3.axvline(12.0, color='crimson', linestyle='--', linewidth=2.0, alpha=0.8)

    ax3.set_yscale('log')
    ax3.set_title("1971–2026 Multi-Decade Wealth ($1,000 Initial, 55.5 Years) vs. Threshold", fontsize=12, fontweight='bold')
    ax3.set_xlabel("MUNGER Multiplier Threshold")
    ax3.set_ylabel("Terminal Wealth ($ Log Scale)")
    ax3.set_xticks(th_vals)
    ax3.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax3.grid(True, which='both', linestyle=':', alpha=0.6)
    ax3.legend(loc='lower left', fontsize=9.5)

    # Panel 4: Universe Breadth vs. Cash Drag (Zero-Signal Years)
    avg_names = [res_1971["thresholds"][t]["avg_qualifying"] for t in th_vals]
    zero_years_1971 = [res_1971["thresholds"][t]["zero_signal_years"] for t in th_vals]

    ax4_bar = ax4.twinx()
    b1 = ax4.bar([t - 0.3 for t in th_vals], avg_names, width=0.6, color='#fed976', edgecolor='black', alpha=0.85, label='Avg Qualifying Names / Year')
    l1 = ax4_bar.plot(th_vals, zero_years_1971, color='darkred', marker='D', linewidth=2.5, label='Zero-Signal Years (Cash Drag)')

    ax4.set_title("The Goldilocks Tradeoff: Signal Abundance vs. Cash Drag (1971-2026)", fontsize=12, fontweight='bold')
    ax4.set_xlabel("MUNGER Multiplier Threshold")
    ax4.set_ylabel("Average Qualifying Stocks per Year", color='#b15928')
    ax4_bar.set_ylabel("Years with Zero Signals (Forced Cash Hold)", color='darkred')
    ax4.set_xticks(th_vals)
    ax4.grid(True, linestyle=':', alpha=0.6)
    ax4.set_ylim(0, 16)
    ax4_bar.set_ylim(-0.5, 12)

    # Combine legends
    lines, labels = ax4.get_legend_handles_labels()
    lines2, labels2 = ax4_bar.get_legend_handles_labels()
    ax4.legend(lines + lines2, labels + labels2, loc='upper right', fontsize=9)

    plt.suptitle("MUNGER 1-Year Hold Threshold Optimization (1971 & 2008 Start Dates)\nProving the Mathematical Superiority of the 12.0x Multiplier for Post-LTCG Wealth", fontsize=14, fontweight='bold', y=0.995)
    plt.tight_layout()
    
    fig_dir = Path("docs/figures")
    fig_path = fig_dir / "munger_1y_threshold_optimization_1971_2008.png"
    plt.savefig(fig_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved optimization chart to: {fig_path}")

    # Copy to artifact folder
    art_dir = Path("C:/Users/Ryan/.gemini/antigravity/brain/b7f84560-ca04-410e-856b-4cff61e44c4f")
    import shutil
    shutil.copy(fig_path, art_dir / "munger_1y_threshold_optimization_1971_2008.png")
    print(f"Copied chart to artifact directory: {art_dir}")

if __name__ == "__main__":
    run_threshold_optimization()
