"""MUNGER Dynamic Sell Criteria Simulation (1971 & 2008 Start Dates).

Tests the hypothesis:
"Sell only if held >= 1 year (qualifying for LTCG) AND MUNGER score drops below sell threshold (e.g. M < 1.5)."
Otherwise, CONTINUE HOLDING to let winners run and defer capital gains tax.

Compares:
1. Baseline: Mechanical 1-Year Exit (Sell at exactly 252 days regardless of score)
2. Dynamic Sell at M < 0.5 (Fails multiple medians / severe deterioration)
3. Dynamic Sell at M < 1.0 (Fails any median)
4. Dynamic Sell at M < 1.5 (User Hypothesis)
5. Dynamic Sell at M < 2.0 (Mild compounder fading)
6. Dynamic Sell at M < 3.0 (Moderate threshold)
7. Buy thresholds: M >= 4.0x and M >= 6.0x
8. Both 2008 (18.8 Years) and 1971 (55.5 Years) start dates.
"""

import pickle
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def run_sell_criteria_study():
    print("=" * 80)
    print("MUNGER DYNAMIC SELL CRITERIA & TAX DEFERRAL SIMULATION")
    print("Testing Rule: Hold >= 1 Year AND M < M_sell (Defer Tax on Great Compounders)")
    print("=" * 80)

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

    # Precompute cross-sectional factors across all dates
    print("Precomputing cross-sectional factor scores...")
    
    # Precompute every 21 trading days (~monthly) for portfolio checks
    eval_dates_all = list(common_dates[::21])
    if eval_dates_all[-1] != common_dates[-1]:
        eval_dates_all.append(common_dates[-1])

    scores_by_date = {} # dt -> dict of ticker -> multiplier M

    for dt in eval_dates_all:
        row_p = close_df.loc[dt]
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
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
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

                    idx_now = list(common_dates).index(dt)
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

                # Compute multiplier: allow non-negative (1 + max(0, Z))
                mult = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                # If any factor is below median, flag it
                all_above_med = (zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0)
                date_m[t] = (mult, all_above_med)

        scores_by_date[dt] = date_m

    print(f"Precomputed scores across {len(scores_by_date)} monthly evaluation dates.")

    # Risk-free rate schedule
    def get_rf_rate(year):
        if year < 1980: return 0.075
        elif year < 1990: return 0.085
        elif year < 2000: return 0.050
        elif year < 2010: return 0.030
        elif year < 2020: return 0.010
        else: return 0.040

    ltcg_tax_rate = 0.238

    # Simulation Function
    def simulate_dynamic_portfolio(start_dt_str, m_buy=6.0, m_sell=1.5, max_hold_days=None, max_positions=6):
        start_dt = pd.to_datetime(start_dt_str)
        sim_eval_dates = [d for d in eval_dates_all if d >= start_dt]
        years_total = (sim_eval_dates[-1] - sim_eval_dates[0]).days / 365.25

        # Portfolio state:
        # Cash: available cash to invest
        # Holdings: dict of ticker -> {entry_dt, entry_price, shares, cost_basis}
        cash_pre = 1000.0
        cash_post = 1000.0
        holdings_pre = {}  # ticker -> {'entry_dt': dt, 'entry_price': p, 'shares': s}
        holdings_post = {} # ticker -> {'entry_dt': dt, 'entry_price': p, 'shares': s, 'basis': b}

        holding_durations = [] # list of held days for sold positions
        trade_gains = []
        portfolio_curve_post = []
        portfolio_curve_pre = []
        dates_curve = []
        total_turnover_dollars = 0.0

        for idx, dt in enumerate(sim_eval_dates):
            cur_year = dt.year
            rf_daily = get_rf_rate(cur_year) / 252.0
            rf_step = ((1.0 + rf_daily) ** 21.0) - 1.0

            # Step 0: Cash earns risk-free yield
            cash_pre *= (1.0 + rf_step)
            cash_post *= (1.0 + rf_step)

            date_scores = scores_by_date.get(dt, {})

            # Step 1: Check sells
            sold_tickers = []
            for t, pos in list(holdings_post.items()):
                p_now = close_df[t].loc[dt]
                if pd.isna(p_now) or p_now <= 0:
                    continue

                days_held = (dt - pos['entry_dt']).days
                t_score, all_med = date_scores.get(t, (0.0, False))

                should_sell = False
                if max_hold_days is not None:
                    # Mechanical exit baseline: sell if held >= max_hold_days
                    if days_held >= max_hold_days:
                        should_sell = True
                else:
                    # Dynamic Munger exit: MUST be held >= 365 days AND (score < m_sell or not all_med)
                    if days_held >= 365:
                        if m_sell is None:
                            # Sell if it fails any median hurdle
                            if not all_med:
                                should_sell = True
                        else:
                            if t_score < m_sell or not all_med:
                                should_sell = True

                if should_sell:
                    sold_tickers.append(t)
                    holding_durations.append(days_held)

                    # Post-tax sale
                    val_post = pos['shares'] * p_now
                    gain_post = val_post - pos['basis']
                    trade_gains.append(gain_post / pos['basis'])
                    tax_post = max(0.0, gain_post) * ltcg_tax_rate
                    cash_post += (val_post - tax_post)
                    total_turnover_dollars += val_post

                    # Pre-tax sale
                    pos_pre = holdings_pre[t]
                    val_pre = pos_pre['shares'] * p_now
                    cash_pre += val_pre

            for t in sold_tickers:
                del holdings_post[t]
                del holdings_pre[t]

            # Step 2: Check buys
            # We buy if we have cash and open position slots (< max_positions)
            open_slots = max_positions - len(holdings_post)
            if open_slots > 0 and cash_post > 10.0:
                # Find qualifying fresh candidates: M >= m_buy, all_med=True, not currently held
                candidates = []
                for t, (m_val, all_med) in date_scores.items():
                    if all_med and m_val >= m_buy and t not in holdings_post:
                        p_now = close_df[t].loc[dt]
                        if pd.notna(p_now) and p_now > 0:
                            candidates.append((t, m_val, p_now))

                candidates.sort(key=lambda x: x[1], reverse=True)
                selected_buys = candidates[:open_slots]

                if selected_buys:
                    # Allocate available cash equally among selected buys
                    alloc_post = cash_post / len(selected_buys)
                    alloc_pre = cash_pre / len(selected_buys)

                    for t_buy, m_val, p_buy in selected_buys:
                        sh_post = alloc_post / p_buy
                        holdings_post[t_buy] = {
                            'entry_dt': dt, 'entry_price': p_buy,
                            'shares': sh_post, 'basis': alloc_post
                        }
                        sh_pre = alloc_pre / p_buy
                        holdings_pre[t_buy] = {
                            'entry_dt': dt, 'entry_price': p_buy,
                            'shares': sh_pre
                        }

                    cash_post = 0.0
                    cash_pre = 0.0

            # Calculate total portfolio value at date dt
            val_holdings_post = sum(pos['shares'] * close_df[t].loc[dt] for t, pos in holdings_post.items() if pd.notna(close_df[t].loc[dt]))
            val_holdings_pre = sum(pos['shares'] * close_df[t].loc[dt] for t, pos in holdings_pre.items() if pd.notna(close_df[t].loc[dt]))
            tot_post = cash_post + val_holdings_post
            tot_pre = cash_pre + val_holdings_pre

            portfolio_curve_post.append(tot_post)
            portfolio_curve_pre.append(tot_pre)
            dates_curve.append(dt)

        # Terminal liquidation of remaining positions at final date
        final_dt = sim_eval_dates[-1]
        final_post_unrealized = portfolio_curve_post[-1]
        unrealized_gain_total = 0.0
        for t, pos in holdings_post.items():
            p_final = close_df[t].loc[final_dt]
            if pd.notna(p_final) and p_final > 0:
                pos_val = pos['shares'] * p_final
                unrealized_gain_total += max(0.0, pos_val - pos['basis'])

        final_post_liquidated = final_post_unrealized - (unrealized_gain_total * ltcg_tax_rate)
        final_pre = portfolio_curve_pre[-1]

        cagr_pre = (final_pre / 1000.0) ** (1.0 / years_total) - 1.0
        cagr_post = (final_post_liquidated / 1000.0) ** (1.0 / years_total) - 1.0
        avg_hold_yrs = (np.mean(holding_durations) / 365.25) if holding_durations else 1.0
        max_hold_yrs = (max(holding_durations) / 365.25) if holding_durations else 1.0
        num_trades = len(holding_durations)
        trade_win_rate = sum(1 for g in trade_gains if g > 0) / len(trade_gains) if trade_gains else 0.0

        return {
            "years": years_total,
            "final_pre": final_pre,
            "final_post": final_post_liquidated,
            "cagr_pre": cagr_pre,
            "cagr_post": cagr_post,
            "drag_bps": (cagr_pre - cagr_post) * 10000.0,
            "avg_hold_yrs": avg_hold_yrs,
            "max_hold_yrs": max_hold_yrs,
            "num_trades": num_trades,
            "trade_win_rate": trade_win_rate,
            "curve_post": portfolio_curve_post,
            "curve_pre": portfolio_curve_pre,
            "dates": dates_curve
        }

    # Test Sell Configurations:
    # 1. Mechanical 1Y: Sell at exactly 365 days regardless of M
    # 2. Dynamic M < 1.0: Hold >= 1Y, sell only if fails median
    # 3. Dynamic M < 1.5: Hold >= 1Y, sell if M < 1.5 (User Hypothesis)
    # 4. Dynamic M < 2.0: Hold >= 1Y, sell if M < 2.0
    # 5. Dynamic M < 3.0: Hold >= 1Y, sell if M < 3.0
    # 6. Dynamic M < 4.0: Hold >= 1Y, sell if M < 4.0
    sell_modes = [
        ("Mechanical 1Y Exit", 365, None, "Sell at exactly 365 days"),
        ("Dynamic M < 1.0", None, 1.0, "Hold >= 1Y, sell if fails any median"),
        ("Dynamic M < 1.5 (User)", None, 1.5, "Hold >= 1Y, sell if M < 1.5"),
        ("Dynamic M < 2.0", None, 2.0, "Hold >= 1Y, sell if M < 2.0"),
        ("Dynamic M < 3.0", None, 3.0, "Hold >= 1Y, sell if M < 3.0"),
        ("Dynamic M < 4.0", None, 4.0, "Hold >= 1Y, sell if M < 4.0")
    ]

    buy_thresholds = [4.0, 6.0]

    all_results = {}

    for start_label, start_dt_str in [("2008 GFC Peak (18.8Y)", "2008-01-02"), ("1971 Stagflation (55.5Y)", "1971-01-04")]:
        print(f"\nEvaluating sell criteria for {start_label}...")
        all_results[start_label] = {}

        for m_b in buy_thresholds:
            for mode_name, max_d, m_s, desc in sell_modes:
                res = simulate_dynamic_portfolio(start_dt_str, m_buy=m_b, m_sell=m_s, max_hold_days=max_d, max_positions=6)
                key_name = f"Buy M>={m_b:.0f}x | {mode_name}"
                all_results[start_label][key_name] = res

    # Print Summary Tables
    for start_label, mode_dict in all_results.items():
        print("\n" + "=" * 105)
        print(f"RESULTS FOR {start_label.upper()}")
        print("-" * 105)
        print(f"{'Strategy Configuration':<36} | {'Pre-Tax $':<13} | {'Pre CAGR':<9} | {'Post-LTCG $':<14} | {'Post CAGR':<10} | {'Avg Hold':<9} | {'Max Hold':<9} | {'Trades':<7}")
        print("-" * 105)

        # find best post cagr
        best_post = max(v['cagr_post'] for v in mode_dict.values())

        for name, res in mode_dict.items():
            f_pre = f"${res['final_pre']:,.0f}"
            c_pre = f"{res['cagr_pre']*100:.2f}%"
            f_post = f"${res['final_post']:,.0f}"
            c_post = f"{res['cagr_post']*100:.2f}%"
            avg_h = f"{res['avg_hold_yrs']:.1f} yrs"
            max_h = f"{res['max_hold_yrs']:.1f} yrs"
            trds = f"{res['num_trades']}"
            star = " [OPTIMAL]" if res['cagr_post'] == best_post else ""
            print(f"{name:<36} | {f_pre:<13} | {c_pre:<9} | {f_post:<14} | {c_post:<10} | {avg_h:<9} | {max_h:<9} | {trds:<7}{star}")

    # Save to JSON
    out_dir = Path("data")
    save_obj = {}
    for sl, md in all_results.items():
        save_obj[sl] = {
            k: {
                "final_pre": v["final_pre"], "final_post": v["final_post"],
                "cagr_pre": v["cagr_pre"], "cagr_post": v["cagr_post"],
                "drag_bps": v["drag_bps"], "avg_hold_yrs": v["avg_hold_yrs"],
                "max_hold_yrs": v["max_hold_yrs"], "num_trades": v["num_trades"],
                "trade_win_rate": v["trade_win_rate"]
            } for k, v in md.items()
        }

    with open(out_dir / "munger_sell_criteria_results.json", "w", encoding="utf-8") as f:
        json.dump(save_obj, f, indent=2)

    # ----------------------------------------------------
    # GENERATE PUBLICATION-QUALITY FIGURES
    # ----------------------------------------------------
    print("\nGenerating publication figure...")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    # Left: 2008 Comparison
    res_2008 = all_results["2008 GFC Peak (18.8Y)"]
    names_2008 = [k.replace("Buy M>=6x | ", "") for k in res_2008.keys() if "Buy M>=6x" in k]
    posts_2008 = [res_2008[k]["final_post"] for k in res_2008.keys() if "Buy M>=6x" in k]
    pres_2008 = [res_2008[k]["final_pre"] for k in res_2008.keys() if "Buy M>=6x" in k]
    cagrs_2008 = [res_2008[k]["cagr_post"]*100 for k in res_2008.keys() if "Buy M>=6x" in k]

    x = np.arange(len(names_2008))
    width = 0.35
    ax1.bar(x - width/2, pres_2008, width, label='Pre-Tax Wealth ($)', color='#9ecae1', edgecolor='black', alpha=0.85)
    bars2 = ax1.bar(x + width/2, posts_2008, width, label='Post-LTCG Wealth ($23.8% Tax)', color='#1f77b4', edgecolor='black')

    ax1.set_title("2008–2026: Sell Criteria Comparison (Buy M >= 6.0x, $1,000 Start)", fontsize=12, fontweight='bold')
    ax1.set_xticks(x)
    ax1.set_xticklabels(names_2008, rotation=25, ha='right', fontsize=9.5)
    ax1.set_ylabel("Terminal Wealth ($)")
    ax1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax1.grid(True, linestyle=':', alpha=0.6)
    ax1.legend(loc='upper left', fontsize=9.5)

    for b, c_val in zip(bars2, cagrs_2008):
        ax1.annotate(f"{c_val:.1f}%\nCAGR",
                     xy=(b.get_x() + b.get_width() / 2, b.get_height()),
                     xytext=(0, 4), textcoords="offset points",
                     ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    # Right: 1971 Multi-Decade Compounding (Log Scale)
    res_1971 = all_results["1971 Stagflation (55.5Y)"]
    names_1971 = [k.replace("Buy M>=6x | ", "") for k in res_1971.keys() if "Buy M>=6x" in k]
    posts_1971 = [res_1971[k]["final_post"] for k in res_1971.keys() if "Buy M>=6x" in k]
    pres_1971 = [res_1971[k]["final_pre"] for k in res_1971.keys() if "Buy M>=6x" in k]
    cagrs_1971 = [res_1971[k]["cagr_post"]*100 for k in res_1971.keys() if "Buy M>=6x" in k]

    ax2.bar(x - width/2, pres_1971, width, label='Pre-Tax Wealth ($ Log Scale)', color='#a1d99b', edgecolor='black', alpha=0.85)
    bars4 = ax2.bar(x + width/2, posts_1971, width, label='Post-LTCG Wealth ($ Log Scale)', color='#2ca02c', edgecolor='black')

    ax2.set_yscale('log')
    ax2.set_title("1971–2026: Multi-Decade Wealth (Buy M >= 6.0x, $1,000 Start, 55.5 Years)", fontsize=12, fontweight='bold')
    ax2.set_xticks(x)
    ax2.set_xticklabels(names_1971, rotation=25, ha='right', fontsize=9.5)
    ax2.set_ylabel("Terminal Wealth ($ Log Scale)")
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
    ax2.grid(True, which='both', linestyle=':', alpha=0.6)
    ax2.legend(loc='upper left', fontsize=9.5)

    for b, c_val in zip(bars4, cagrs_1971):
        ax2.annotate(f"{c_val:.1f}%\nCAGR",
                     xy=(b.get_x() + b.get_width() / 2, b.get_height()),
                     xytext=(0, 4), textcoords="offset points",
                     ha='center', va='bottom', fontsize=8.5, fontweight='bold')

    plt.suptitle("MUNGER Dynamic Sell Criteria Optimization: Hold >= 1 Year AND M < M_sell\nValidating Charlie Munger's Principle: 'Let Great Compounders Run and Defer Taxes'", fontsize=14, fontweight='bold', y=1.01)
    plt.tight_layout()

    fig_dir = Path("docs/figures")
    fig_path = fig_dir / "munger_sell_criteria_optimization.png"
    plt.savefig(fig_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved sell criteria chart to: {fig_path}")

    art_dir = Path("C:/Users/Ryan/.gemini/antigravity/brain/b7f84560-ca04-410e-856b-4cff61e44c4f")
    import shutil
    shutil.copy(fig_path, art_dir / "munger_sell_criteria_optimization.png")
    print(f"Copied chart to artifact folder: {art_dir}")

if __name__ == "__main__":
    run_sell_criteria_study()
