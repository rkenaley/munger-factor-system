"""
Compare Munger Strategy with Value Trap Filter ON vs. OFF (1971-2026).
Computes:
  - Final pre-tax and post-tax NAV
  - Net CAGR, Sharpe ratio, Max Drawdown
  - Trade-by-trade win rate
  - Frequency and severity of big losers (trades down > 20%, > 30%, > 50%)
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from munger_engine import (
    BacktestConfig,
    load_or_generate_dataset,
    compute_vectorized_munger_scores,
    run_portfolio_simulation,
    compute_performance_metrics,
)


def evaluate_run(label: str, cfg: BacktestConfig, scores_df: pd.DataFrame, med_df: pd.DataFrame, close_df: pd.DataFrame, gspc_close: pd.Series, eval_dates: pd.DatetimeIndex):
    res = run_portfolio_simulation(close_df, gspc_close, eval_dates, scores_df, med_df, cfg)
    nav_pre = res["nav_pre"]
    nav_post = res["nav_post"]
    met_pre = compute_performance_metrics(nav_pre)
    met_post = compute_performance_metrics(nav_post)

    # Detailed trade analysis by running detailed position tracker
    # Track trade holding returns
    prices_mat = close_df.reindex(eval_dates, method="ffill").values
    raw_active_mat = (prices_mat > 0) & np.isfinite(prices_mat)
    scores_mat = scores_df.values
    med_mat = med_df.values
    slippage = cfg.slippage_bps / 10000.0

    holdings = {}
    trade_returns = []

    for step_idx, dt in enumerate(eval_dates):
        cur_year = int(dt.year)
        # Sell checks
        for t_idx in list(holdings.keys()):
            pos = holdings[t_idx]
            p_now = prices_mat[step_idx, t_idx]
            is_active = raw_active_mat[step_idx, t_idx] and np.isfinite(p_now) and (p_now > 0)

            if not is_active:
                future_valid = raw_active_mat[step_idx:, t_idx]
                if not np.any(future_valid):
                    p_exit = pos["last_price"] * (1.0 - cfg.delisting_loss_haircut)
                    ret = (p_exit / pos["cost_basis"]) - 1.0
                    trade_returns.append(ret)
                    del holdings[t_idx]
                continue

            pos["last_price"] = float(p_now)
            days_held = (dt - pos["entry_date"]).days
            t_score = scores_mat[step_idx, t_idx]
            is_above_med = bool(med_mat[step_idx, t_idx])

            should_sell = False
            if cfg.max_hold_days is not None and days_held >= cfg.max_hold_days:
                should_sell = True
            elif days_held >= cfg.min_hold_days:
                if cfg.m_sell_threshold is not None and (np.isnan(t_score) or t_score < cfg.m_sell_threshold):
                    should_sell = True
                if cfg.require_above_median and not is_above_med:
                    should_sell = True

            if should_sell:
                p_exit = float(p_now) * (1.0 - slippage)
                ret = (p_exit / pos["cost_basis"]) - 1.0
                trade_returns.append(ret)
                del holdings[t_idx]

        # Buys
        open_slots = 7 - len(holdings)
        if open_slots > 0:
            row_scores = scores_mat[step_idx]
            row_prices = prices_mat[step_idx]
            valid_mask = (
                raw_active_mat[step_idx]
                & np.isfinite(row_scores)
                & (row_scores >= cfg.m_buy_threshold)
                & np.isfinite(row_prices)
                & (row_prices > 0)
            )
            candidate_indices = np.where(valid_mask)[0]
            candidate_indices = [idx for idx in candidate_indices if idx not in holdings]
            candidate_indices.sort(key=lambda idx: row_scores[idx], reverse=True)

            for idx in candidate_indices[:open_slots]:
                p_entry = float(row_prices[idx]) * (1.0 + slippage)
                holdings[idx] = {
                    "entry_date": dt,
                    "cost_basis": p_entry,
                    "last_price": p_entry,
                }

    # Add open positions to trade returns
    for pos in holdings.values():
        ret = (pos["last_price"] / pos["cost_basis"]) - 1.0
        trade_returns.append(ret)

    tr_arr = np.array(trade_returns)
    win_rate = float(np.mean(tr_arr > 0.0)) * 100.0 if len(tr_arr) > 0 else 0.0
    loss_over_20 = float(np.mean(tr_arr < -0.20)) * 100.0 if len(tr_arr) > 0 else 0.0
    loss_over_30 = float(np.mean(tr_arr < -0.30)) * 100.0 if len(tr_arr) > 0 else 0.0
    loss_over_50 = float(np.mean(tr_arr < -0.50)) * 100.0 if len(tr_arr) > 0 else 0.0
    worst_trade = float(np.min(tr_arr)) * 100.0 if len(tr_arr) > 0 else 0.0
    best_trade = float(np.max(tr_arr)) * 100.0 if len(tr_arr) > 0 else 0.0
    avg_win = float(np.mean(tr_arr[tr_arr > 0])) * 100.0 if np.any(tr_arr > 0) else 0.0
    avg_loss = float(np.mean(tr_arr[tr_arr < 0])) * 100.0 if np.any(tr_arr < 0) else 0.0

    return {
        "label": label,
        "final_pre": float(nav_pre.iloc[-1]),
        "cagr_pre": met_pre["cagr"] * 100.0,
        "final_post": float(nav_post.iloc[-1]),
        "cagr_post": met_post["cagr"] * 100.0,
        "sharpe_post": met_post["sharpe"],
        "max_dd_post": met_post["max_drawdown"] * 100.0,
        "trade_count": res["trade_count"],
        "delistings": res["delisting_events"],
        "win_rate": win_rate,
        "worst_trade": worst_trade,
        "best_trade": best_trade,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "loss_over_20": loss_over_20,
        "loss_over_30": loss_over_30,
        "loss_over_50": loss_over_50,
    }


def main():
    close_df, gspc_close, fund_dict, eps_dict, _ = load_or_generate_dataset("data")
    eval_dates = close_df.index[::21]

    # Run 1: Value Trap Filter ON
    cfg_on = BacktestConfig(
        initial_capital=1000.0,
        max_pos_pct=0.15,
        min_hold_days=365,
        m_buy_threshold=2.0,
        require_above_median=True,
        slippage_bps=5.0,
        use_historical_tax_regimes=True,
        delisting_loss_haircut=0.30,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
        winsorize_cross_section=True,
        enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )
    scores_on, med_on, _ = compute_vectorized_munger_scores(
        close_df, eval_dates, fund_dict, eps_dict,
        reporting_lag_days=45, allow_legacy_lookahead=False,
        winsorize_cross_section=True, enforce_full_cycle_profitability=True,
        use_cyclically_adjusted_ey=True,
    )
    stats_on = evaluate_run("Value Trap Filter ON (Winsorized + Full-Cycle Gated)", cfg_on, scores_on, med_on, close_df, gspc_close, eval_dates)

    # Run 2: Value Trap Filter OFF
    cfg_off = BacktestConfig(
        initial_capital=1000.0,
        max_pos_pct=0.15,
        min_hold_days=365,
        m_buy_threshold=2.0,
        require_above_median=True,
        slippage_bps=5.0,
        use_historical_tax_regimes=True,
        delisting_loss_haircut=0.30,
        reporting_lag_days=45,
        allow_legacy_lookahead=False,
        winsorize_cross_section=False,
        enforce_full_cycle_profitability=False,
        use_cyclically_adjusted_ey=False,
    )
    scores_off, med_off, _ = compute_vectorized_munger_scores(
        close_df, eval_dates, fund_dict, eps_dict,
        reporting_lag_days=45, allow_legacy_lookahead=False,
        winsorize_cross_section=False, enforce_full_cycle_profitability=False,
        use_cyclically_adjusted_ey=False,
    )
    stats_off = evaluate_run("Value Trap Filter OFF (Raw Multipliers / Cyclical Traps Allowed)", cfg_off, scores_off, med_off, close_df, gspc_close, eval_dates)

    print("=" * 88)
    print(" MUNGER FACTOR SYSTEM: VALUE TRAP FILTER AUDIT (1971-2026, 55.5 YEARS)")
    print("=" * 88)
    print(f"{'Metric':<38} | {'Value Trap Filter ON':<22} | {'Value Trap Filter OFF':<22} | {'Difference / Uplift'}")
    print("-" * 105)
    print(f"{'Post-Tax Terminal Wealth ($1k start)':<38} | ${stats_on['final_post']:>19,.2f} | ${stats_off['final_post']:>19,.2f} | {((stats_on['final_post']/stats_off['final_post'])-1)*100:+.1f}%")
    print(f"{'Post-Tax Net CAGR':<38} | {stats_on['cagr_post']:>21.2f}% | {stats_off['cagr_post']:>21.2f}% | {stats_on['cagr_post']-stats_off['cagr_post']:+.2f}% (+{int(round((stats_on['cagr_post']-stats_off['cagr_post'])*100))} bps)")
    print(f"{'Pre-Tax Terminal Wealth':<38} | ${stats_on['final_pre']:>19,.2f} | ${stats_off['final_pre']:>19,.2f} | {((stats_on['final_pre']/stats_off['final_pre'])-1)*100:+.1f}%")
    print(f"{'Pre-Tax Net CAGR':<38} | {stats_on['cagr_pre']:>21.2f}% | {stats_off['cagr_pre']:>21.2f}% | {stats_on['cagr_pre']-stats_off['cagr_pre']:+.2f}%")
    print(f"{'Sharpe Ratio (Post-Tax)':<38} | {stats_on['sharpe_post']:>22.2f} | {stats_off['sharpe_post']:>22.2f} | {stats_on['sharpe_post']-stats_off['sharpe_post']:+.2f}")
    print(f"{'Maximum Portfolio Drawdown':<38} | {stats_on['max_dd_post']:>21.2f}% | {stats_off['max_dd_post']:>21.2f}% | {stats_on['max_dd_post']-stats_off['max_dd_post']:+.2f}%")
    print("-" * 105)
    print(f"{'Trade Win Rate (% of trades > 0)':<38} | {stats_on['win_rate']:>21.1f}% | {stats_off['win_rate']:>21.1f}% | {stats_on['win_rate']-stats_off['win_rate']:+.1f}%")
    print(f"{'Average Winner Return':<38} | +{stats_on['avg_win']:>20.1f}% | +{stats_off['avg_win']:>20.1f}% | {stats_on['avg_win']-stats_off['avg_win']:+.1f}%")
    print(f"{'Average Loser Return':<38} | {stats_on['avg_loss']:>21.1f}% | {stats_off['avg_loss']:>21.1f}% | {stats_on['avg_loss']-stats_off['avg_loss']:+.1f}%")
    print(f"{'Catastrophic Losers (loss > 50%)':<38} | {stats_on['loss_over_50']:>21.1f}% | {stats_off['loss_over_50']:>21.1f}% | {stats_on['loss_over_50']-stats_off['loss_over_50']:+.1f}%")
    print(f"{'Severe Losers (loss > 30%)':<38} | {stats_on['loss_over_30']:>21.1f}% | {stats_off['loss_over_30']:>21.1f}% | {stats_on['loss_over_30']-stats_off['loss_over_30']:+.1f}%")
    print(f"{'Moderate Losers (loss > 20%)':<38} | {stats_on['loss_over_20']:>21.1f}% | {stats_off['loss_over_20']:>21.1f}% | {stats_on['loss_over_20']-stats_off['loss_over_20']:+.1f}%")
    print(f"{'Worst Single Trade Drawdown':<38} | {stats_on['worst_trade']:>21.1f}% | {stats_off['worst_trade']:>21.1f}% | {stats_on['worst_trade']-stats_off['worst_trade']:+.1f}%")
    print(f"{'Total Trades Executed':<38} | {stats_on['trade_count']:>22} | {stats_off['trade_count']:>22} | {stats_on['trade_count']-stats_off['trade_count']:+d}")
    print("=" * 88)

    # Sub-period: 2008-2026
    start_08 = "2008-01-01"
    eval_dates_08 = eval_dates[eval_dates >= pd.to_datetime(start_08)]
    scores_on_08 = scores_on.loc[eval_dates_08]
    med_on_08 = med_on.loc[eval_dates_08]
    scores_off_08 = scores_off.loc[eval_dates_08]
    med_off_08 = med_off.loc[eval_dates_08]

    stats_on_08 = evaluate_run("2008 ON", cfg_on, scores_on_08, med_on_08, close_df.loc[start_08:], gspc_close.loc[start_08:], eval_dates_08)
    stats_off_08 = evaluate_run("2008 OFF", cfg_off, scores_off_08, med_off_08, close_df.loc[start_08:], gspc_close.loc[start_08:], eval_dates_08)

    print("\n" + "=" * 88)
    print(" MODERN GFC-TO-PRESENT AUDIT (2008-2026, 18.8 YEARS)")
    print("=" * 88)
    print(f"{'Metric':<38} | {'Value Trap Filter ON':<22} | {'Value Trap Filter OFF':<22} | {'Difference / Uplift'}")
    print("-" * 105)
    print(f"{'Post-Tax Terminal Wealth ($1k start)':<38} | ${stats_on_08['final_post']:>19,.2f} | ${stats_off_08['final_post']:>19,.2f} | {((stats_on_08['final_post']/stats_off_08['final_post'])-1)*100:+.1f}%")
    print(f"{'Post-Tax Net CAGR':<38} | {stats_on_08['cagr_post']:>21.2f}% | {stats_off_08['cagr_post']:>21.2f}% | {stats_on_08['cagr_post']-stats_off_08['cagr_post']:+.2f}% (+{int(round((stats_on_08['cagr_post']-stats_off_08['cagr_post'])*100))} bps)")
    print(f"{'Maximum Portfolio Drawdown':<38} | {stats_on_08['max_dd_post']:>21.2f}% | {stats_off_08['max_dd_post']:>21.2f}% | {stats_on_08['max_dd_post']-stats_off_08['max_dd_post']:+.2f}%")
    print(f"{'Sharpe Ratio (Post-Tax)':<38} | {stats_on_08['sharpe_post']:>22.2f} | {stats_off_08['sharpe_post']:>22.2f} | {stats_on_08['sharpe_post']-stats_off_08['sharpe_post']:+.2f}")
    print("-" * 105)
    print(f"{'Trade Win Rate (% of trades > 0)':<38} | {stats_on_08['win_rate']:>21.1f}% | {stats_off_08['win_rate']:>21.1f}% | {stats_on_08['win_rate']-stats_off_08['win_rate']:+.1f}%")
    print(f"{'Severe Losers (loss > 30%)':<38} | {stats_on_08['loss_over_30']:>21.1f}% | {stats_off_08['loss_over_30']:>21.1f}% | {stats_on_08['loss_over_30']-stats_off_08['loss_over_30']:+.1f}%")
    print(f"{'Catastrophic Losers (loss > 50%)':<38} | {stats_on_08['loss_over_50']:>21.1f}% | {stats_off_08['loss_over_50']:>21.1f}% | {stats_on_08['loss_over_50']-stats_off_08['loss_over_50']:+.1f}%")
    print(f"{'Total Trades Executed':<38} | {stats_on_08['trade_count']:>22} | {stats_off_08['trade_count']:>22} | {stats_on_08['trade_count']-stats_off_08['trade_count']:+d}")
    print("=" * 88)


if __name__ == "__main__":
    main()

