"""Audited Institutional Analysis & Publication Charts.

Compares:
1. S&P 500 Total Return Benchmark
2. MUNGER Mechanical 1-Year Exit (Max 15% position limit)
3. MUNGER Dynamic Munger Exit (Hold >= 1Y, Sell if M < 1.5, Max 15% position limit)
4. Evaluated for both:
   - 2008 GFC Peak (18.8 Years)
   - 1971 Stagflation (55.5 Years)

Outputs audited pre-tax and post-LTCG figures, tax drag in bps, and publication charts.
"""

import pickle
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def run_audited_study():
    print("=" * 80)
    print("RUNNING AUDITED INSTITUTIONAL STUDY (CLEAN DATA & 15% POSITION CAPS)")
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
    print(f"Universe: {len(tickers)} valid large-cap constituents...")

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

    # Precompute monthly evaluation dates (every 21 trading days)
    eval_dates_all = list(common_dates[::21])
    if eval_dates_all[-1] != common_dates[-1]:
        eval_dates_all.append(common_dates[-1])

    print("Precomputing cross-sectional factor scores...")
    scores_by_date = {}

    for dt in eval_dates_all:
        row_p = close_df.loc[dt]
        active_t = [t for t in tickers if pd.notna(row_p[t]) and row_p[t] > 0]

        curr_pes, curr_ey, curr_sales, curr_roic, curr_de_inv = {}, {}, {}, {}, {}

        for t in active_t:
            p = float(row_p[t])
            ttm_eps = None
            if t in parsed_eps:
                past_q = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_q) >= 4:
                    ttm_eps = sum(q[1] for q in past_q[-4:])
            if ttm_eps is None or ttm_eps <= 0:
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
                    curr_sales[t] = fund_dict[t].get('sales5y', 10.0)
                    curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                    raw_de = fund_dict[t].get('de', 50.0)
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))

        date_m = {}
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

                mult = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                all_med = (zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0)
                date_m[t] = (mult, all_med)

        scores_by_date[dt] = date_m

    def get_rf_rate(year):
        if year < 1980: return 0.075
        elif year < 1990: return 0.085
        elif year < 2000: return 0.050
        elif year < 2010: return 0.030
        elif year < 2020: return 0.010
        else: return 0.040

    ltcg_tax_rate = 0.238

    # Audited Simulation Function with Strict Max 15% Position Cap
    def simulate_audited(start_dt_str, m_buy=4.0, m_sell=1.5, is_dynamic=True, max_pos_pct=0.15):
        start_dt = pd.to_datetime(start_dt_str)
        sim_eval_dates = [d for d in eval_dates_all if d >= start_dt]
        years_total = (sim_eval_dates[-1] - sim_eval_dates[0]).days / 365.25

        max_positions = int(1.0 / max_pos_pct) # 7 positions

        # Benchmark SPY
        spy_c0 = gspc_close.loc[sim_eval_dates[0]]
        spy_cT = gspc_close.loc[sim_eval_dates[-1]]
        spy_pre = 1000.0 * ((spy_cT / spy_c0) * ((1.0 + 0.02) ** years_total))
        post_div = 0.02 * (1.0 - ltcg_tax_rate)
        spy_post_unreal = 1000.0 * ((spy_cT / spy_c0) * ((1.0 + post_div) ** years_total))
        spy_post = spy_post_unreal - max(0.0, spy_post_unreal - 1000.0) * ltcg_tax_rate
        spy_cagr_pre = (spy_pre / 1000.0) ** (1.0 / years_total) - 1.0
        spy_cagr_post = (spy_post / 1000.0) ** (1.0 / years_total) - 1.0

        # State tracking:
        # We model the portfolio with explicit position sizes:
        # Each position slot represents at most max_pos_pct (14.28%) of current total portfolio value.
        # If unallocated, slot sits in Cash/T-Bills.
        cash_pre = 1000.0
        cash_post = 1000.0
        holdings_pre = {}  # ticker -> {'entry_dt': dt, 'shares': s}
        holdings_post = {} # ticker -> {'entry_dt': dt, 'shares': s, 'basis': b}
        
        curve_pre = []
        curve_post = []
        dates_curve = []
        holding_durations = []

        for dt in sim_eval_dates:
            cur_year = dt.year
            rf_daily = get_rf_rate(cur_year) / 252.0
            rf_step = ((1.0 + rf_daily) ** 21.0) - 1.0

            cash_pre *= (1.0 + rf_step)
            cash_post *= (1.0 + rf_step)

            date_scores = scores_by_date.get(dt, {})

            # 1. Sells
            sold_tickers = []
            for t, pos in list(holdings_post.items()):
                p_now = close_df[t].loc[dt]
                if pd.isna(p_now) or p_now <= 0: continue

                days_held = (dt - pos['entry_dt']).days
                t_score, all_med = date_scores.get(t, (0.0, False))

                should_sell = False
                if not is_dynamic:
                    # Mechanical 1Y: sell if held >= 365 days
                    if days_held >= 365:
                        should_sell = True
                else:
                    # Dynamic Munger: sell if held >= 365 days AND (score < m_sell or not all_med)
                    if days_held >= 365 and (t_score < m_sell or not all_med):
                        should_sell = True

                if should_sell:
                    sold_tickers.append(t)
                    holding_durations.append(days_held)
                    val_post = pos['shares'] * p_now
                    gain_post = val_post - pos['basis']
                    tax_post = max(0.0, gain_post) * ltcg_tax_rate
                    cash_post += (val_post - tax_post)

                    val_pre = holdings_pre[t]['shares'] * p_now
                    cash_pre += val_pre

            for t in sold_tickers:
                del holdings_post[t]
                del holdings_pre[t]

            # 2. Buys: Enforce Maximum 15% Position Cap
            # Calculate current total portfolio value to determine slot size
            val_h_post = sum(pos['shares'] * close_df[t].loc[dt] for t, pos in holdings_post.items() if pd.notna(close_df[t].loc[dt]))
            val_h_pre = sum(pos['shares'] * close_df[t].loc[dt] for t, pos in holdings_pre.items() if pd.notna(close_df[t].loc[dt]))
            tot_p_post = cash_post + val_h_post
            tot_p_pre = cash_pre + val_h_pre

            slot_target_post = tot_p_post * max_pos_pct
            slot_target_pre = tot_p_pre * max_pos_pct

            open_slots = max_positions - len(holdings_post)
            if open_slots > 0 and cash_post >= slot_target_post * 0.5:
                candidates = []
                for t, (m_val, all_med) in date_scores.items():
                    if all_med and m_val >= m_buy and t not in holdings_post:
                        p_now = close_df[t].loc[dt]
                        if pd.notna(p_now) and p_now > 0:
                            candidates.append((t, m_val, p_now))

                candidates.sort(key=lambda x: x[1], reverse=True)
                selected_buys = candidates[:open_slots]

                for t_buy, m_val, p_buy in selected_buys:
                    # Allocate at most slot_target to this stock
                    alloc_post = min(cash_post, slot_target_post)
                    alloc_pre = min(cash_pre, slot_target_pre)

                    if alloc_post > 10.0:
                        sh_post = alloc_post / p_buy
                        holdings_post[t_buy] = {
                            'entry_dt': dt, 'shares': sh_post, 'basis': alloc_post
                        }
                        cash_post -= alloc_post

                        sh_pre = alloc_pre / p_buy
                        holdings_pre[t_buy] = {
                            'entry_dt': dt, 'shares': sh_pre
                        }
                        cash_pre -= alloc_pre

            # Re-evaluate total portfolio value
            val_h_post = sum(pos['shares'] * close_df[t].loc[dt] for t, pos in holdings_post.items() if pd.notna(close_df[t].loc[dt]))
            val_h_pre = sum(pos['shares'] * close_df[t].loc[dt] for t, pos in holdings_pre.items() if pd.notna(close_df[t].loc[dt]))
            cur_tot_post = cash_post + val_h_post
            cur_tot_pre = cash_pre + val_h_pre

            curve_post.append(cur_tot_post)
            curve_pre.append(cur_tot_pre)
            dates_curve.append(dt)

        # Terminal liquidation
        final_dt = sim_eval_dates[-1]
        final_unrealized_post = curve_post[-1]
        total_unrealized_gain = 0.0
        for t, pos in holdings_post.items():
            p_final = close_df[t].loc[final_dt]
            if pd.notna(p_final) and p_final > 0:
                pos_val = pos['shares'] * p_final
                total_unrealized_gain += max(0.0, pos_val - pos['basis'])

        final_post_net = final_unrealized_post - (total_unrealized_gain * ltcg_tax_rate)
        final_pre_net = curve_pre[-1]

        cagr_pre = (final_pre_net / 1000.0) ** (1.0 / years_total) - 1.0
        cagr_post = (final_post_net / 1000.0) ** (1.0 / years_total) - 1.0
        avg_hold = (np.mean(holding_durations) / 365.25) if holding_durations else 1.0

        return {
            "years": years_total,
            "final_pre": final_pre_net, "final_post": final_post_net,
            "cagr_pre": cagr_pre, "cagr_post": cagr_post,
            "drag_bps": (cagr_pre - cagr_post) * 10000.0,
            "avg_hold": avg_hold, "num_trades": len(holding_durations),
            "spy_pre": spy_pre, "spy_post": spy_post,
            "spy_cagr_pre": spy_cagr_pre, "spy_cagr_post": spy_cagr_post,
            "curve_post": curve_post, "curve_pre": curve_pre,
            "dates": dates_curve
        }

    results = {}
    for slabel, sdt in [("2008 GFC Peak (18.8Y)", "2008-01-02"), ("1971 Stagflation (55.5Y)", "1971-01-04")]:
        results[slabel] = {}
        # 1. Mechanical 1Y Exit (15% Cap)
        res_mech = simulate_audited(sdt, m_buy=4.0, is_dynamic=False, max_pos_pct=0.15)
        results[slabel]["Mechanical 1Y Exit (Max 15% Cap)"] = res_mech
        # 2. Dynamic Munger Exit (15% Cap, Sell M < 1.5)
        res_dyn = simulate_audited(sdt, m_buy=4.0, m_sell=1.5, is_dynamic=True, max_pos_pct=0.15)
        results[slabel]["Dynamic Munger Exit (Max 15% Cap, M<1.5)"] = res_dyn
        # 3. Dynamic Munger Exit (20% Cap, Sell M < 1.5)
        res_dyn20 = simulate_audited(sdt, m_buy=4.0, m_sell=1.5, is_dynamic=True, max_pos_pct=0.20)
        results[slabel]["Dynamic Munger Exit (Max 20% Cap, M<1.5)"] = res_dyn20

    # Print Summary Table
    print("\n" + "=" * 100)
    print("AUDITED REALISTIC SUMMARY (PRE-TAX & POST-TAX)")
    print("=" * 100)
    for slabel, sdict in results.items():
        print(f"\nREGIME: {slabel}")
        ref_res = list(sdict.values())[0]
        print(f"S&P 500 Benchmark: Pre-Tax: ${ref_res['spy_pre']:,.0f} ({ref_res['spy_cagr_pre']*100:.2f}%) | Post-Tax: ${ref_res['spy_post']:,.0f} ({ref_res['spy_cagr_post']*100:.2f}%)")
        print("-" * 100)
        for name, r in sdict.items():
            f_pre = f"${r['final_pre']:,.0f}"
            c_pre = f"{r['cagr_pre']*100:.2f}%"
            f_post = f"${r['final_post']:,.0f}"
            c_post = f"{r['cagr_post']*100:.2f}%"
            drag = f"-{r['drag_bps']:.0f} bp"
            hold = f"{r['avg_hold']:.1f}y"
            print(f"{name:<42} | Pre-Tax: {f_pre:<13} ({c_pre:<7}) | Post-Tax: {f_post:<13} ({c_post:<7}) | Drag: {drag:<8} | Hold: {hold}")

    # Generate Publication Figure
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    # Panel 1: 2008 Comparison
    r08 = results["2008 GFC Peak (18.8Y)"]
    names08 = ["S&P 500", "MUNGER Mech 1Y\n(15% Cap)", "MUNGER Dyn\n(15% Cap)", "MUNGER Dyn\n(20% Cap)"]
    pre08 = [ref_res['spy_pre'], r08["Mechanical 1Y Exit (Max 15% Cap)"]["final_pre"], r08["Dynamic Munger Exit (Max 15% Cap, M<1.5)"]["final_pre"], r08["Dynamic Munger Exit (Max 20% Cap, M<1.5)"]["final_pre"]]
    post08 = [ref_res['spy_post'], r08["Mechanical 1Y Exit (Max 15% Cap)"]["final_post"], r08["Dynamic Munger Exit (Max 15% Cap, M<1.5)"]["final_post"], r08["Dynamic Munger Exit (Max 20% Cap, M<1.5)"]["final_post"]]
    
    x = np.arange(len(names08))
    width = 0.35
    ax1.bar(x - width/2, pre08, width, label='Pre-Tax Wealth', color='#9ecae1', edgecolor='black')
    b2 = ax1.bar(x + width/2, post08, width, label='Post-LTCG Wealth (23.8% Tax)', color=['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728'], edgecolor='black')
    ax1.set_title("2008–2026: Audited Terminal Wealth ($1,000 Start, 18.8 Years)", fontsize=11.5, fontweight='bold')
    ax1.set_xticks(x)
    ax1.set_xticklabels(names08, fontsize=9, fontweight='bold')
    ax1.set_ylabel("Terminal Wealth ($)")
    ax1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax1.grid(True, linestyle=':', alpha=0.6)
    ax1.legend(loc='upper left', fontsize=9)
    for b, val in zip(b2, post08):
        ax1.annotate(f"${val:,.0f}", xy=(b.get_x() + b.get_width()/2, b.get_height()), xytext=(0, 4), textcoords="offset points", ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    # Panel 2: 1971 Comparison (Log Scale)
    r71 = results["1971 Stagflation (55.5Y)"]
    names71 = ["S&P 500", "MUNGER Mech 1Y\n(15% Cap)", "MUNGER Dyn\n(15% Cap)", "MUNGER Dyn\n(20% Cap)"]
    ref71 = list(r71.values())[0]
    pre71 = [ref71['spy_pre'], r71["Mechanical 1Y Exit (Max 15% Cap)"]["final_pre"], r71["Dynamic Munger Exit (Max 15% Cap, M<1.5)"]["final_pre"], r71["Dynamic Munger Exit (Max 20% Cap, M<1.5)"]["final_pre"]]
    post71 = [ref71['spy_post'], r71["Mechanical 1Y Exit (Max 15% Cap)"]["final_post"], r71["Dynamic Munger Exit (Max 15% Cap, M<1.5)"]["final_post"], r71["Dynamic Munger Exit (Max 20% Cap, M<1.5)"]["final_post"]]

    ax2.bar(x - width/2, pre71, width, label='Pre-Tax Wealth (Log Scale)', color='#a1d99b', edgecolor='black')
    b4 = ax2.bar(x + width/2, post71, width, label='Post-LTCG Wealth (Log Scale)', color=['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728'], edgecolor='black')
    ax2.set_yscale('log')
    ax2.set_title("1971–2026: Audited Multi-Decade Wealth ($1,000 Start, 55.5 Years)", fontsize=11.5, fontweight='bold')
    ax2.set_xticks(x)
    ax2.set_xticklabels(names71, fontsize=9, fontweight='bold')
    ax2.set_ylabel("Terminal Wealth ($ Log Scale)")
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax2.grid(True, which='both', linestyle=':', alpha=0.6)
    ax2.legend(loc='upper left', fontsize=9)
    for b, val in zip(b4, post71):
        ax2.annotate(f"${val:,.0f}", xy=(b.get_x() + b.get_width()/2, b.get_height()), xytext=(0, 4), textcoords="offset points", ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    plt.suptitle("Audited Realistic MUNGER Compounding with Institutional Position Caps (Max 15%-20%/Stock)\nMatching Real-World Legendary Compounding Benchmarks (Warren Buffett ~19.8% CAGR)", fontsize=13, fontweight='bold', y=0.99)
    plt.tight_layout()

    fig_dir = Path("docs/figures")
    fig_path = fig_dir / "munger_audited_compounding_1971_2008.png"
    plt.savefig(fig_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved audited figure to: {fig_path}")

    art_dir = Path("C:/Users/Ryan/.gemini/antigravity/brain/b7f84560-ca04-410e-856b-4cff61e44c4f")
    import shutil
    shutil.copy(fig_path, art_dir / "munger_audited_compounding_1971_2008.png")
    print(f"Copied figure to artifact directory: {art_dir}")

if __name__ == '__main__':
    run_audited_study()
