"""CLI entry point for the Dual-Tranche Tax-Aware IBKR Bot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .config import ExecutionMode, build_default_config
from .engine import DualTrancheBotEngine


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m ibkr_bot.cli",
        description="Dual-Tranche Tax-Aware IBKR Trading Bot (defaults strictly to dry-run).",
    )
    p.add_argument(
        "--mode",
        choices=[m.value for m in ExecutionMode],
        default=ExecutionMode.DRY_RUN.value,
        help="Execution mode: dry-run (default local simulation), paper (IB Gateway 4002), or live (4001).",
    )
    p.add_argument("--roth-account", default="DU_ROTH_SIM", help="IBKR Roth IRA account ID.")
    p.add_argument("--taxable-account", default="DU_TAXABLE_SIM", help="IBKR Taxable account ID.")
    p.add_argument("--roth-equity", type=float, default=25_000.0, help="Dry-run starting Roth NAV ($).")
    p.add_argument("--taxable-equity", type=float, default=75_000.0, help="Dry-run starting Taxable NAV ($).")
    p.add_argument("--state-tax-rate", type=float, default=0.06, help="Marginal state income tax rate (e.g. 0.06).")
    p.add_argument("--port", type=int, default=None, help="IB Gateway/TWS port (4002 paper / 4001 live).")
    p.add_argument("--db-path", type=str, default=None, help="Override mode-segregated SQLite ledger file path.")
    p.add_argument("--confirm-live", action="store_true", help="Required explicit opt-in flag for live mode.")
    p.add_argument("--override-paper-soak", action="store_true", help="Override 90-day paper soak requirement.")
    p.add_argument("--json", action="store_true", help="Print full JSON report to stdout.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    cfg = build_default_config(
        mode=ExecutionMode(args.mode),
        roth_account_id=args.roth_account,
        taxable_account_id=args.taxable_account,
        roth_nav_usd=args.roth_equity,
        taxable_nav_usd=args.taxable_equity,
        state_tax_rate=args.state_tax_rate,
        port=args.port,
        confirm_live=args.confirm_live,
        override_paper_soak=args.override_paper_soak,
    )
    if args.db_path:
        cfg.db_path = Path(args.db_path)

    with DualTrancheBotEngine(cfg) as engine:
        report = engine.run_cycle(write_reports=True)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        mkt = report["market_regime"]
        vrp_s = f"{mkt['vrp_vol_pts']:+.2f}" if mkt["vrp_vol_pts"] is not None else "N/A"
        rv_s = f"{mkt['rv21_spy_pct']:.2f}%" if mkt["rv21_spy_pct"] is not None else "N/A"
        tr_s = f"{mkt['vix_term_ratio']:.3f}" if mkt["vix_term_ratio"] is not None else "N/A"
        print("=" * 96)
        print(f" IBKR DUAL-TRANCHE BOT | {report['trade_date']} | MODE: {report['mode'].upper()}")
        print("=" * 96)
        print(
            f" Market: SPY=${mkt['spy_spot']:.2f} | XSP=${mkt['xsp_spot']:.2f} | "
            f"VIX={mkt['vix']:.2f} | VIX3M={mkt['vix3m']:.2f} (Ratio={tr_s}) | "
            f"RV21={rv_s} | VRP={vrp_s} pts"
        )
        print(
            f" Compliance: COI Blocked={report['compliance']['coi_blocked_count']} | "
            f"Fidelity Shadow={len(report['compliance']['fidelity_shadow_tickers'])} tickers "
            f"(CSV {report['compliance']['fidelity_export_date']}) | "
            f"Paper Soak={report['paper_soak']['paper_days_logged']}/90d"
        )
        for tr in report["tranches"]:
            print("-" * 96)
            print(
                f" [{tr['account_type']} | {tr['account_id']}] "
                f"NAV=${tr['post_nav_usd']:,.2f} | Cash=${tr['post_cash_usd']:,.2f} | "
                f"Positions={tr['post_positions']} | OpenXSP={tr['open_spreads_count']}"
            )
            print(f"   XSP Gate : {tr['xsp_gate_status']}")
            for ex in tr["executed"]:
                print(
                    f"   [EXEC {ex['status']}] {ex['intent_type']} {ex['action']} "
                    f"{ex['filled_qty']} {ex['symbol']} @ ${ex['avg_fill_price']:.2f} "
                    f"(comm ${ex['commission_usd']:.2f}) -> {ex['rationale']}"
                )
            for rj in tr["rejected"]:
                print(f"   [BLOCKED] {rj['intent_type']} {rj['symbol']} -> {'; '.join(rj['reasons'])}")
        print("=" * 96)
        print(f" Reports written to: {cfg.resolve_path(cfg.report_dir) / 'ibkr_bot_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
