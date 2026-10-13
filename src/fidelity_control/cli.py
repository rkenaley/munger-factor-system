"""Command-line interface for Fidelity Control Center."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path
from .control_center import run_pipeline


def print_banner(title: str):
    print("=" * 80)
    print(f" {title.upper()}")
    print("=" * 80)


def format_currency(val: float) -> str:
    return f"${val:,.2f}"


def main():
    parser = argparse.ArgumentParser(
        prog="fidelity-control",
        description="Fidelity Control Center: Extremizing Indicators, Sentiment, Munger Gating, and Daily Orders"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # 1. Run all
    run_parser = subparsers.add_parser("run", help="Run full pipeline and export all Fidelity files")
    run_parser.add_argument("--config", "-c", type=str, default=None, help="Path to config.json")
    run_parser.add_argument("--output", "-o", type=str, default=None, help="Output directory")

    # 2. Daily trades
    trades_parser = subparsers.add_parser("daily-trades", help="Display today's executable Fidelity orders")
    trades_parser.add_argument("--config", "-c", type=str, default=None)

    # 3. Screener
    screen_parser = subparsers.add_parser("screen", help="Run extremizing multi-factor stock screener")
    screen_parser.add_argument("--config", "-c", type=str, default=None)

    # 4. Backtest
    bt_parser = subparsers.add_parser("backtest", help="Run historical backtest of screener qualifiers and forward returns")
    bt_parser.add_argument("--mode", "-m", choices=["strict", "relaxed", "dislocation", "1sd", "2sd", "multiplier"], default="relaxed", help="Backtest mode")
    bt_parser.add_argument("--period", "-p", type=str, default="2y", help="Historical window (e.g. 1y, 2y)")
    bt_parser.add_argument("--sales", type=float, default=15.0, help="Minimum 5Y sales growth %")
    bt_parser.add_argument("--de", type=float, default=120.0, help="Maximum D/E ratio %")
    bt_parser.add_argument("--cutoff", type=float, default=10.0, help="Multiplier metric cutoff threshold (e.g. 10.0, 12.0)")
    bt_parser.add_argument("--universe", "-u", choices=["expanded", "core"], default="expanded", help="Universe scope (expanded: S&P 100 + Nasdaq 100 [177 stocks], core: 31 stocks)")

    # 5. Options
    opt_parser = subparsers.add_parser("options", help="Display actionable Long Call Alpha and Bear Put / Collar Hedge tickets")
    opt_parser.add_argument("--config", "-c", type=str, default=None)

    args = parser.parse_args()
    
    if args.command in ["run", None]:
        print_banner("Fidelity Control Center - Daily Quantitative Cycle")
        print(" [*] Ingesting market metrics, technical moving averages, and sentiment...")
        res = run_pipeline(getattr(args, 'config', None), getattr(args, 'output', None))
        
        print(f"\n [+] Total Portfolio Balance Sheet: {format_currency(res['total_net_worth'])}")
        print(f" [+] Generated {res['orders_count']} Actionable Fidelity Orders")
        print(f" [+] Configured {res['alerts_count']} Fidelity Alert Thresholds")
        print(f" [+] {res['passing_screener_count']} Candidates Passed the Extremizing Screener")
        
        print("\n [!] Exported Files:")
        print(f"   • Fidelity Daily Orders CSV: {res['orders_csv']}")
        print(f"   • Fidelity Technical Alerts CSV: {res['alerts_csv']}")
        print(f"   • Fidelity Watchlist CSV: {res['watchlist_csv']}")
        print(f"   • Dashboard JSON State: {res['json_report']}")
        
        # Read and print the daily orders for instant visibility
        import csv
        with open(res['orders_csv'], 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            headers = next(reader)
            print_banner("Today's Executable Fidelity Order Tickets")
            for r in reader:
                print(f" [{r[0]}] {r[1]} -> {r[3]} {r[6]} shares of {r[2]} @ LIMIT ${r[5]} (Est: ${float(r[7]):,.2f})")
                print(f"     Action: {r[10]}")
                print(f"     Rationale: {r[8]}\n")
                
    elif args.command == "daily-trades":
        res = run_pipeline(getattr(args, 'config', None))
        import csv
        with open(res['orders_csv'], 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            headers = next(reader)
            print_banner("Today's Executable Fidelity Order Tickets")
            for r in reader:
                print(f" [{r[0]}] {r[1]} -> {r[3]} {r[6]} shs {r[2]} @ LIMIT ${r[5]} (Total: ${float(r[7]):,.2f})")
                print(f"     Instructions: {r[10]}")
                print(f"     Tier: {r[8]}\n")
                
    elif args.command == "screen":
        res = run_pipeline(getattr(args, 'config', None))
        import json
        with open(res['json_report'], 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        scr = data['screener']
        print_banner("Extremizing Stock Screener Results")
        print(f" Universe Stats: Q1 P/E = {scr['stats']['q1_pe']:.1f} | Q1 P/B = {scr['stats']['q1_pb']:.2f} | Median D/E = {scr['stats']['median_de']:.1f}%")
        print(f" Filter: Sales Growth 5Y >= {scr['stats']['sales_growth_min']}% | Max D/E <= {scr['stats']['max_de_limit']}%\n")
        
        print(f" Passing Securities ({len(scr['passing_candidates'])}):")
        print(f" {'Ticker':<8} | {'Company Name':<24} | {'Theme':<30} | {'P/E':<6} | {'P/B':<6} | {'D/E':<6} | {'Sales5Y':<8}")
        print("-" * 95)
        for c in scr['passing_candidates']:
            print(f" {c['ticker']:<8} | {c['name']:<24} | {c['theme']:<30} | {c['pe']:<6.1f} | {c['pb']:<6.2f} | {c['de']:<6.1f}% | {c['sales5y']:<7.1f}%")

    elif args.command == "backtest":
        from .backtester import run_screener_backtest
        title_suffix = f"Cutoff >={args.cutoff}x" if args.mode == "multiplier" else args.period
        univ_label = "S&P 100 + Nasdaq 100 (177 Stocks)" if args.universe == "expanded" else "Core Portfolio (31 Stocks)"
        print_banner(f"Extremizing Screener Backtest [{args.mode.upper()} Mode - {univ_label}]")
        print(f" Testing parameters across historical trading days...")
        res = run_screener_backtest(
            period=args.period,
            mode=args.mode,
            sales_growth_threshold=args.sales,
            max_de_threshold=args.de,
            multiplier_cutoff=args.cutoff,
            universe=args.universe
        )
        s = res["summary"]
        print(f" [+] Universe Tested: {res.get('universe_count', 177)} Equities ({res.get('universe', args.universe)})")
        print(f" [+] Total Signals Triggered: {s['total_qualifying_signals']}")
        print(f" [+] Unique Qualifying Tickers ({len(s['unique_qualifying_tickers'])}): {', '.join(s['unique_qualifying_tickers'][:12])}{'...' if len(s['unique_qualifying_tickers']) > 12 else ''}")
        print(f" [+] 3-Month Win Rate (>0%): {s['win_rate_3m_pct']}%")
        print(f" [+] Beat S&P 500 (SPY) Rate: {s['beat_spy_rate_3m_pct']}%")
        ret1_str = f"{s['avg_forward_1m_ret_pct']:+.2f}%" if s['avg_forward_1m_ret_pct'] is not None else "N/A"
        ret3_str = f"{s['avg_forward_3m_ret_pct']:+.2f}%" if s['avg_forward_3m_ret_pct'] is not None else "N/A"
        spy3_str = f"{s['avg_spy_3m_ret_pct']:+.2f}%" if s['avg_spy_3m_ret_pct'] is not None else "N/A"
        alp_str  = f"{s['avg_alpha_3m_pct']:+.2f}%" if s['avg_alpha_3m_pct'] is not None else "N/A"
        ret6_str = f"{s['avg_forward_6m_ret_pct']:+.2f}%" if s['avg_forward_6m_ret_pct'] is not None else "N/A"
        mdd_str  = f"{s['avg_max_drawdown_3m_pct']:.2f}%" if s['avg_max_drawdown_3m_pct'] is not None else "N/A"

        print(f" [+] Avg Forward 1-Month Return: {ret1_str}")
        print(f" [+] Avg Forward 3-Month Return: {ret3_str} (vs SPY: {spy3_str})")
        print(f" [+] Avg Forward 3-Month Alpha:  {alp_str}")
        print(f" [+] Avg Forward 6-Month Return: {ret6_str}")
        print(f" [+] Avg Maximum Drawdown (3M):  {mdd_str}\n")

        if res.get("cutoff_curve"):
            print(" Extremized Multiplier Cutoff Performance Curve:")
            print(f" {'Cutoff':<8} | {'Signals':<8} | {'Win Rate':<10} | {'3M Ret':<8} | {'Alpha':<8} | {'6M Ret':<8} | {'Max DD':<8} | {'Tickers'}")
            print("-" * 92)
            for c in res["cutoff_curve"]:
                print(f" >={c['cutoff']:<5.1f}x | {c['signals']:<8} | {c['win_rate']:>5.1f}%    | {c['ret_3m']:>+6.1f}% | {c['alpha']:>+6.1f}% | {c['ret_6m']:>+6.1f}% | {c['max_dd']:>5.1f}% | {', '.join(c['tickers'])}")
            print()

        events = res.get("sample_qualifying_events", [])
        if events:
            score_col = " | Score  " if args.mode == "multiplier" else ""
            print(" Sample Historical Trigger Events:")
            print(f" {'Date':<10} | {'Ticker':<5} | {'Company Name':<22} | {'Price':<8} | {'P/E':<5}{score_col} | {'1M Ret':<8} | {'3M Ret':<8} | {'SPY 3M':<8} | {'Alpha':<8}")
            print("-" * (102 if args.mode == "multiplier" else 92))
            for e in events[:12]:
                r1m = f"{e['ret_1m']:+.1f}%" if e['ret_1m'] is not None else "N/A"
                r3m = f"{e['ret_3m']:+.1f}%" if e['ret_3m'] is not None else "N/A"
                spy3 = f"{e['spy_ret_3m']:+.1f}%" if e['spy_ret_3m'] is not None else "N/A"
                alp = f"{e['alpha_3m']:+.1f}%" if e['alpha_3m'] is not None else "N/A"
                sc_str = f" | {e.get('composite_score', 0):>6.1f}x" if args.mode == "multiplier" else ""
                print(f" {e['date']:<10} | {e['ticker']:<5} | {e['name'][:22]:<22} | ${e['price_at_signal']:>7.2f} | {e['pe_at_signal']:>5.1f}{sc_str} | {r1m:>8} | {r3m:>8} | {spy3:>8} | {alp:>8}")

    elif args.command == "options":
        from .options_engine import generate_options_recommendations
        import json
        print_banner("Fidelity Control Center - Options Alpha & Hedging Desk")
        rep_file = Path("output/daily_report.json")
        curr_prices = {}
        if rep_file.exists():
            try:
                with open(rep_file, "r", encoding="utf-8") as rf:
                    d = json.load(rf)
                    for m in d.get("metrics", []):
                        curr_prices[m["ticker"]] = m.get("current_price", 100.0)
            except Exception:
                pass
        if not curr_prices or 'NVDA' not in curr_prices:
            curr_prices.update({'NVDA': 140.0, 'MU': 110.0, 'GEV': 280.0, 'WDC': 75.0, 'DASH': 155.0, 'SNPS': 560.0, 'IDXX': 490.0})
        
        opt_data = generate_options_recommendations(curr_prices, {})
        print(f" [*] Generated {opt_data['total_tickets']} Actionable Option & Collar Tickets\n")
        
        print(" 1. LONG CALL ALPHA (LEAPS & Bull Vertical Spreads):")
        print(f" {'ID':<17} | {'Ticker':<5} | {'Strategy':<16} | {'Target Account':<25} | {'Order Syntax'}")
        print("-" * 115)
        for t in opt_data['tickets']:
            if 'CALL' in t['strategy_type']:
                max_rew = "Unlimited" if t['max_reward_usd'] > 900000 else f"${t['max_reward_usd']:,.2f}"
                print(f" {t['ticket_id']:<17} | {t['ticker']:<5} | {t['strategy_type']:<16} | {t['account_designation'][:25]:<25} | {t['fidelity_order_syntax']}")
                print(f"    --> Rationale: {t['rationale']}")
                print(f"    --> Max Risk: ${t['max_risk_usd']:,.2f} | Max Reward: {max_rew} | Breakeven: ${t['breakeven_price']:.2f}\n")

        print(" 2. INVERTED PUT STRATEGIES (Fragility Spreads & Concentrated Stock Collars):")
        print(f" {'ID':<17} | {'Ticker':<5} | {'Strategy':<16} | {'Target Account':<32} | {'Order Syntax'}")
        print("-" * 115)
        for t in opt_data['tickets']:
            if 'CALL' not in t['strategy_type']:
                print(f" {t['ticket_id']:<17} | {t['ticker']:<5} | {t['strategy_type']:<16} | {t['account_designation'][:32]:<32} | {t['fidelity_order_syntax']}")
                print(f"    --> Rationale: {t['rationale']}")
                print(f"    --> Max Risk: ${t['max_risk_usd']:,.2f} | Max Reward: ${t['max_reward_usd']:,.2f} | Breakeven: ${t['breakeven_price']:.2f}\n")


if __name__ == "__main__":
    main()
