"""Execute strict, relaxed, and dislocation backtests and summarize forward returns."""

import json
from fidelity_control.backtester import run_screener_backtest


def main():
    print("=" * 80)
    print(" 1. STRICT EXTREMIZING SCREENER BACKTEST (2-Year Daily Trajectory)")
    print(" Parameters: PE <= Q1 (25th %), PB <= Q1 (25th %), Sales5Y >= 20%, D/E <= Median (<= 100%)")
    print("=" * 80)
    strict_res = run_screener_backtest(period="2y", mode="strict")
    print(f" Total Signals Triggered: {strict_res['summary']['total_qualifying_signals']}")
    print(f" Unique Qualifying Tickers: {strict_res['summary']['unique_qualifying_tickers']}")
    print(" Summary Metrics:")
    for k, v in strict_res['summary'].items():
        print(f"   • {k}: {v}")

    print("\n" + "=" * 80)
    print(" 2. RELAXED CONSTRAINTS (CLOSER TO MEDIAN) BACKTEST")
    print(" Parameters: PE <= Median (50th %), PB <= Median (50th %), Sales5Y >= 15%, D/E <= 120%")
    print("=" * 80)
    relaxed_res = run_screener_backtest(period="2y", mode="relaxed", sales_growth_threshold=15.0, max_de_threshold=120.0)
    print(f" Total Signals Triggered: {relaxed_res['summary']['total_qualifying_signals']}")
    print(f" Unique Qualifying Tickers: {relaxed_res['summary']['unique_qualifying_tickers']}")
    print(f" Win Rate (Forward 3-Month > 0%): {relaxed_res['summary']['win_rate_3m_pct']}%")
    print(f" Beat S&P 500 Benchmark Rate: {relaxed_res['summary']['beat_spy_rate_3m_pct']}%")
    print(f" Avg Forward 1-Month Return: {relaxed_res['summary']['avg_forward_1m_ret_pct']}%")
    print(f" Avg Forward 3-Month Return: {relaxed_res['summary']['avg_forward_3m_ret_pct']}% (vs SPY: {relaxed_res['summary']['avg_spy_3m_ret_pct']}%)")
    print(f" Avg Forward 3-Month Alpha: {relaxed_res['summary']['avg_alpha_3m_pct']}%")
    print(f" Avg Forward 6-Month Return: {relaxed_res['summary']['avg_forward_6m_ret_pct']}%")
    print(f" Avg Maximum Drawdown (3M): {relaxed_res['summary']['avg_max_drawdown_3m_pct']}%")

    print("\n Sample Qualifying Events (Relaxed):")
    print(f" {'Date':<10} | {'Ticker':<5} | {'Company Name':<22} | {'Price':<8} | {'P/E':<5} | {'P/B':<5} | {'1M Ret':<8} | {'3M Ret':<8} | {'SPY 3M':<8} | {'Alpha':<8} | {'MaxDD':<7}")
    print("-" * 110)
    for e in relaxed_res['sample_qualifying_events'][:15]:
        ret_1m_str = f"{e['ret_1m']:+.1f}%" if e['ret_1m'] is not None else "N/A"
        ret_3m_str = f"{e['ret_3m']:+.1f}%" if e['ret_3m'] is not None else "N/A"
        spy_3m_str = f"{e['spy_ret_3m']:+.1f}%" if e['spy_ret_3m'] is not None else "N/A"
        alpha_str = f"{e['alpha_3m']:+.1f}%" if e['alpha_3m'] is not None else "N/A"
        maxdd_str = f"{e['max_dd_3m']:.1f}%" if e['max_dd_3m'] is not None else "N/A"
        print(f" {e['date']:<10} | {e['ticker']:<5} | {e['name'][:22]:<22} | ${e['price_at_signal']:>7.2f} | {e['pe_at_signal']:>5.1f} | {e['pb_at_signal']:>5.2f} | {ret_1m_str:>8} | {ret_3m_str:>8} | {spy_3m_str:>8} | {alpha_str:>8} | {maxdd_str:>7}")

    print("\n" + "=" * 80)
    print(" 3. DISLOCATION QUALITY BACKTEST (High Growth/ROIC + Valuation Pullback)")
    print(" Parameters: Sales5Y >= 15%, P/E <= Median, D/E <= 120%")
    print("=" * 80)
    disloc_res = run_screener_backtest(period="2y", mode="dislocation", sales_growth_threshold=15.0, max_de_threshold=120.0)
    print(f" Total Signals Triggered: {disloc_res['summary']['total_qualifying_signals']}")
    print(f" Unique Qualifying Tickers: {disloc_res['summary']['unique_qualifying_tickers']}")
    print(f" Win Rate (Forward 3-Month > 0%): {disloc_res['summary']['win_rate_3m_pct']}%")
    print(f" Beat S&P 500 Benchmark Rate: {disloc_res['summary']['beat_spy_rate_3m_pct']}%")
    print(f" Avg Forward 1-Month Return: {disloc_res['summary']['avg_forward_1m_ret_pct']}%")
    print(f" Avg Forward 3-Month Return: {disloc_res['summary']['avg_forward_3m_ret_pct']}% (vs SPY: {disloc_res['summary']['avg_spy_3m_ret_pct']}%)")
    print(f" Avg Forward 3-Month Alpha: {disloc_res['summary']['avg_alpha_3m_pct']}%")
    print(f" Avg Forward 6-Month Return: {disloc_res['summary']['avg_forward_6m_ret_pct']}%")
    print(f" Avg Maximum Drawdown (3M): {disloc_res['summary']['avg_max_drawdown_3m_pct']}%")


if __name__ == "__main__":
    main()
