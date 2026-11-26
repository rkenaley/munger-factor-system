"""Main Dual-Tranche IBKR Bot orchestrator, broker reconciler, and report generator."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .broker import BrokerInterface, IBKRBroker, SimulatedBroker
from .config import BotConfig, ExecutionMode, TrancheConfig
from .ledger import StateLedger
from .models import (
    AccountSnapshot,
    AssetClass,
    ExecutionResult,
    IntentType,
    MarketRegime,
    OpenSpreadPosition,
    OrderIntent,
    RiskDecision,
    TaxLot,
)
from .risk import RiskFirewall
from .signals import (
    build_advisory_munger_digest,
    build_paired_tlh_buy_intent,
    current_ny_date,
    fetch_live_market_regime,
    fetch_xsp_put_chain,
    generate_cash_sweep_intent,
    generate_core_buy_intents,
    generate_roth_rebalance_sell_intents,
    generate_spread_exit_intents,
    generate_taxable_tlh_sell_intents,
    generate_xsp_overlay_intent,
)


class DualTrancheBotEngine:
    """
    Hardened end-to-end execution cycle (all Critic #2 fixes implemented):
      - Context-manager (`with DualTrancheBotEngine(...)`) closes owned SQLite WAL handle
      - Mode-segregated DB & mode-to-account-prefix verification BEFORE connecting
      - Broker-to-SQLite `OPT` spread reconciliation (auto-closes expired & manually closed spreads)
      - Sequential tier execution (refreshing snapshot & re-sizing after EVERY tier & fill!):
          Tier 1: Risk-reducing XSP exits (live IBKR marks required in Paper/Live!)
          Tier 2: Taxable TLH_SELL -> immediately followed by dynamically sized paired TLH_BUY
          Tier 3a: Roth 0%-tax drift trims -> Tier 3b: Core ETF buys -> Tier 3c: SGOV/USFR cash sweep
          Tier 4: Strictly VRP-gated European XSP Put Credit Spread
      - Exception-safe order submission (never leaves orders stuck `RESERVED`)
      - Full partial-fill reconciliation (`result.filled_qty > 0` regardless of status!)
      - FIFO tax-lot consumption on non-lot-specific equity sells & `conId` persistence on spreads
    """

    def __init__(
        self,
        config: BotConfig,
        broker: Optional[BrokerInterface] = None,
        ledger: Optional[StateLedger] = None,
    ) -> None:
        self.config = config
        self._owns_ledger = ledger is None
        self.ledger = ledger or StateLedger(config.effective_db_path, mode=config.mode.value)
        self.firewall = RiskFirewall(config, self.ledger)
        if broker is not None:
            self.broker = broker
        elif config.mode == ExecutionMode.DRY_RUN:
            self.broker = SimulatedBroker(config)
        else:
            self.broker = IBKRBroker(config)

    def __enter__(self) -> "DualTrancheBotEngine":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_ledger:
            self.ledger.close()

    def _reconcile_broker_options(
        self,
        tranche: TrancheConfig,
        snap: AccountSnapshot,
        run_date: date,
    ) -> List[str]:
        """
        Critic #2 Fix #6: Reconciles SQLite `open_spreads` against actual broker `OPT`
        positions BEFORE generating any close orders!
        - Auto-closes expired spreads (`exp_dt < run_date`) so bot never spams expired closes.
        - Auto-closes spreads whose legs are absent from IBKR `broker_option_positions`
          (manually closed in TWS) so bot NEVER accidentally opens a reverse debit spread!
        - Flags untracked broker option legs as warnings.
        """
        notes: List[str] = []
        broker_leg_keys = {
            (
                str(p["symbol"]).upper(),
                str(p["expiry"]).replace("-", ""),
                round(float(p["strike"]), 2),
                str(p["right"]).upper(),
            ): float(p["position"])
            for p in snap.broker_option_positions
        }
        tracked_keys = set()

        for sp in list(snap.open_spreads):
            exp_ib = sp.expiry.replace("-", "")
            s_key = (sp.symbol.upper(), exp_ib, round(sp.short_strike, 2), sp.right.upper())
            l_key = (sp.symbol.upper(), exp_ib, round(sp.long_strike, 2), sp.right.upper())
            tracked_keys.add(s_key)
            tracked_keys.add(l_key)

            try:
                exp_dt = date.fromisoformat(sp.expiry)
            except ValueError:
                exp_dt = run_date

            if exp_dt < run_date:
                self.ledger.close_spread(sp.spread_id, run_date, 0.0, terminal_status="EXPIRED")
                msg = f"Reconciled EXPIRED spread {sp.spread_id} (expiry {sp.expiry} < {run_date})."
                self.ledger.log_event("SPREAD_EXPIRED", msg, account_id=tranche.account_id, symbol=sp.symbol)
                notes.append(msg)
                continue

            if self.config.mode != ExecutionMode.DRY_RUN:
                s_pos = broker_leg_keys.get(s_key, 0.0)
                l_pos = broker_leg_keys.get(l_key, 0.0)
                if s_pos >= 0.0 and l_pos <= 0.0:
                    self.ledger.close_spread(sp.spread_id, run_date, 0.0, terminal_status="MANUAL_CLOSED")
                    msg = f"Reconciled MANUAL_CLOSED spread {sp.spread_id} (absent from IBKR positions)."
                    self.ledger.log_event("SPREAD_MANUAL_CLOSED", msg, level="WARN", account_id=tranche.account_id, symbol=sp.symbol)
                    notes.append(msg)

        for key, pos in broker_leg_keys.items():
            if key not in tracked_keys and abs(pos) > 0.0:
                msg = f"UNTRACKED IBKR OPTION POSITION detected in {tranche.account_id}: {key} qty={pos}."
                self.ledger.log_event("UNTRACKED_OPTION", msg, level="WARN", account_id=tranche.account_id, symbol=key[0])
                notes.append(msg)

        return notes

    def _execute_intent(
        self,
        intent: OrderIntent,
        tranche: TrancheConfig,
        mkt: MarketRegime,
        run_date: date,
        executed: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
    ) -> Optional[ExecutionResult]:
        """Single hardened execution pipeline: snapshot -> firewall -> reserve -> try/submit -> finalize -> reconcile."""
        cur_snap = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
        decision: RiskDecision = self.firewall.evaluate(intent, cur_snap, mkt)
        if not decision.approved:
            self.ledger.log_event(
                "RISK_REJECT",
                "; ".join(decision.reasons),
                level="WARN",
                account_id=tranche.account_id,
                symbol=intent.symbol,
                details={"intent_type": intent.intent_type.value},
            )
            rejected.append({
                "intent_type": intent.intent_type.value,
                "symbol": intent.symbol,
                "action": intent.action,
                "quantity": intent.quantity,
                "limit_price": intent.limit_price,
                "reasons": decision.reasons,
            })
            return None

        if not self.ledger.reserve_order(intent):
            rejected.append({
                "intent_type": intent.intent_type.value,
                "symbol": intent.symbol,
                "reasons": [f"Idempotency lock prevented duplicate submit ({intent.idempotency_key})."],
            })
            return None

        # Critic #2 Fix #11: Wrap broker.submit_order in try/except so orders NEVER stick in RESERVED!
        try:
            result = self.broker.submit_order(intent, mkt)
        except Exception as exc:
            result = ExecutionResult(
                idempotency_key=intent.idempotency_key,
                status="ERROR",
                message=f"Broker exception during submit_order: {exc}",
            )
            self.ledger.log_event(
                "BROKER_ERROR",
                result.message,
                level="ERROR",
                account_id=tranche.account_id,
                symbol=intent.symbol,
            )

        self.ledger.finalize_order(intent, result)
        self._reconcile_post_fill(intent, result, run_date)

        executed.append({
            "idempotency_key": intent.idempotency_key,
            "intent_type": intent.intent_type.value,
            "symbol": intent.symbol,
            "action": intent.action,
            "quantity": intent.quantity,
            "limit_price": intent.limit_price,
            "status": result.status,
            "filled_qty": result.filled_qty,
            "avg_fill_price": result.avg_fill_price,
            "commission_usd": result.commission_usd,
            "rationale": intent.rationale,
            "message": result.message,
            "raw_order_spec": result.raw_order_spec,
            "warnings": decision.warnings,
        })
        return result

    def run_cycle(
        self,
        trade_date: Optional[date] = None,
        regime: Optional[MarketRegime] = None,
        xsp_chain_tuple: Optional[Tuple[Optional[str], Optional[int], Optional[Any], bool]] = None,
        live_spread_marks: Optional[Dict[str, Tuple[float, float]]] = None,
        write_reports: bool = True,
    ) -> Dict[str, Any]:
        # Critic #2 Fix #21: Always default to US/Eastern trading date
        run_date = trade_date or current_ny_date()

        # Fail-closed mode/account & LIVE safety checks BEFORE opening any socket!
        mode_errors = self.config.validate_mode_account_safety()
        soak = self.ledger.get_paper_soak_metrics(self.config.paper_soak_db_path)
        live_errors = self.config.validate_live_safety(soak["paper_days_logged"])
        all_boot_errors = list(dict.fromkeys(mode_errors + live_errors))
        if all_boot_errors:
            self.ledger.log_event("BOOT_SAFETY_BLOCK", "; ".join(all_boot_errors), level="ERROR")
            raise PermissionError("SAFETY BLOCK:\n  - " + "\n  - ".join(all_boot_errors))

        self.broker.connect()
        try:
            mkt = regime or fetch_live_market_regime(self.config)
            chain_tuple = (
                xsp_chain_tuple
                if xsp_chain_tuple is not None
                else fetch_xsp_put_chain(self.config, mkt, run_date)
            )

            tranche_reports: List[Dict[str, Any]] = []
            for tranche in self.config.tranches:
                snap = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                reconcile_notes = self._reconcile_broker_options(tranche, snap, run_date)
                # Refresh snapshot after option reconciliation
                snap = self.broker.get_account_snapshot(tranche, mkt, self.ledger)

                self.ledger.record_daily_nav(
                    trade_date=run_date,
                    account_id=tranche.account_id,
                    nav_usd=snap.nav_usd,
                    cash_usd=snap.cash_usd,
                    options_risk_usd=snap.total_open_options_risk_usd,
                    vix=mkt.vix,
                    vrp_pts=mkt.vrp_vol_pts,
                )

                executed: List[Dict[str, Any]] = []
                rejected: List[Dict[str, Any]] = []

                # --------------------------------------------------------------
                # TIER 1: Risk-Reducing XSP Exits (Live IBKR Marks in Paper/Live)
                # --------------------------------------------------------------
                marks = live_spread_marks
                if marks is None and snap.open_spreads:
                    broker_marks = self.broker.get_live_spread_marks(snap.open_spreads)
                    if broker_marks:
                        marks = broker_marks
                        mkt.live_spread_marks_verified = True

                for exit_intent in generate_spread_exit_intents(
                    self.config, tranche, snap, mkt, run_date, live_spread_marks=marks
                ):
                    self._execute_intent(exit_intent, tranche, mkt, run_date, executed, rejected)

                # --------------------------------------------------------------
                # TIER 2: Automated Taxable Cross-Index TLH (Sell -> Paired Buy)
                # --------------------------------------------------------------
                snap_t2 = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                for tlh_sell in generate_taxable_tlh_sell_intents(
                    self.config, tranche, snap_t2, mkt, self.ledger, run_date
                ):
                    sell_res = self._execute_intent(tlh_sell, tranche, mkt, run_date, executed, rejected)
                    if sell_res is not None and sell_res.filled_qty > 0:
                        filled_proceeds = sell_res.filled_qty * sell_res.avg_fill_price - sell_res.commission_usd
                        tlh_buy = build_paired_tlh_buy_intent(tlh_sell, filled_proceeds, mkt, run_date)
                        if tlh_buy is not None:
                            self._execute_intent(tlh_buy, tranche, mkt, run_date, executed, rejected)

                # --------------------------------------------------------------
                # TIER 3a: Roth 0%-Tax Drift Trims (Execute BEFORE buys!)
                # --------------------------------------------------------------
                snap_t3a = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                for trim_intent in generate_roth_rebalance_sell_intents(
                    self.config, tranche, snap_t3a, mkt, run_date
                ):
                    self._execute_intent(trim_intent, tranche, mkt, run_date, executed, rejected)

                # --------------------------------------------------------------
                # TIER 3b: Core ETF Deployment (Re-sized on real post-sell cash!)
                # --------------------------------------------------------------
                snap_t3b = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                for buy_intent in generate_core_buy_intents(
                    self.config, tranche, snap_t3b, mkt, self.ledger, run_date
                ):
                    self._execute_intent(buy_intent, tranche, mkt, run_date, executed, rejected)

                # --------------------------------------------------------------
                # TIER 3c: Residual Cash Sweep into SGOV / USFR (Post-buy cash!)
                # --------------------------------------------------------------
                snap_t3c = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                sweep_intent = generate_cash_sweep_intent(
                    self.config, tranche, snap_t3c, mkt, self.ledger, run_date
                )
                if sweep_intent is not None:
                    self._execute_intent(sweep_intent, tranche, mkt, run_date, executed, rejected)

                # --------------------------------------------------------------
                # TIER 4: Strictly VRP-Gated European XSP Put Credit Spread
                # --------------------------------------------------------------
                snap_t4 = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                xsp_intent, xsp_diag = generate_xsp_overlay_intent(
                    self.config, tranche, snap_t4, mkt, run_date, chain_tuple
                )
                if xsp_intent is not None:
                    self._execute_intent(xsp_intent, tranche, mkt, run_date, executed, rejected)

                post_snap = self.broker.get_account_snapshot(tranche, mkt, self.ledger)
                daily_dd, dd_30 = self.ledger.get_rolling_drawdowns(
                    tranche.account_id, post_snap.nav_usd, run_date
                )
                tranche_reports.append({
                    "account_id": tranche.account_id,
                    "account_type": tranche.account_type.value,
                    "pre_nav_usd": round(snap.nav_usd, 2),
                    "post_nav_usd": round(post_snap.nav_usd, 2),
                    "post_cash_usd": round(post_snap.cash_usd, 2),
                    "post_positions": post_snap.positions,
                    "open_spreads_count": len(post_snap.open_spreads),
                    "open_options_risk_usd": round(post_snap.total_open_options_risk_usd, 2),
                    "daily_dd_pct": round(daily_dd * 100.0, 2),
                    "rolling_30d_dd_pct": round(dd_30 * 100.0, 2),
                    "xsp_gate_status": xsp_diag,
                    "reconcile_notes": reconcile_notes,
                    "executed": executed,
                    "rejected": rejected,
                })

            advisory = build_advisory_munger_digest(self.config, self.firewall.coi_blocked)
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "trade_date": run_date.isoformat(),
                "mode": self.config.mode.value,
                "db_path": str(self.config.effective_db_path),
                "paper_soak": self.ledger.get_paper_soak_metrics(self.config.paper_soak_db_path),
                "tax_profile": {
                    "combined_stcg_pct": round(self.config.tax.combined_stcg_rate * 100.0, 1),
                    "combined_ltcg_pct": round(self.config.tax.combined_ltcg_rate * 100.0, 1),
                    "sec1256_blended_pct": round(self.config.tax.sec1256_blended_rate * 100.0, 1),
                    "tbill_state_exempt_pct": round(self.config.tax.tbill_effective_rate * 100.0, 1),
                },
                "market_regime": {
                    "spy_spot": round(mkt.spy_spot, 2),
                    "xsp_spot": round(mkt.xsp_spot, 2),
                    "vix": round(mkt.vix, 2),
                    "vix3m": round(mkt.vix3m, 2),
                    "vix_term_ratio": round(mkt.vix_term_ratio, 3) if mkt.vix3m > 0 else None,
                    "rv21_spy_pct": round(mkt.rv21_spy_pct, 2) if mkt.rv21_spy_pct < 1e6 else None,
                    "effective_iv30_pct": round(mkt.effective_iv30_pct, 2),
                    "vrp_vol_pts": round(mkt.vrp_vol_pts, 2) if abs(mkt.vrp_vol_pts) < 1e6 else None,
                    "quote_age_seconds": round(mkt.quote_age_seconds, 1) if mkt.quote_age_seconds < 1e9 else None,
                    "etf_prices": {
                        k: round(v, 2)
                        for k, v in sorted(mkt.prices.items())
                        if k in {"ITOT", "SCHB", "SCHG", "VUG", "SGOV", "VOO", "VGT", "USFR", "SPY", "XSP"}
                    },
                },
                "compliance": {
                    "coi_blocked_count": len(self.firewall.coi_blocked),
                    "medtech_blocked": sorted(self.firewall.medtech_tickers) if self.config.risk.block_medtech_review else [],
                    "fidelity_shadow_tickers": sorted(self.firewall.fidelity_shadow.keys()),
                    "fidelity_export_date": self.firewall.fidelity_export_date.isoformat() if self.firewall.fidelity_export_date else None,
                },
                "tranches": tranche_reports,
                "advisory_munger_watchlist": advisory,
            }

            if write_reports:
                self._write_reports(report)
            return report
        finally:
            self.broker.disconnect()

    def _reconcile_post_fill(self, intent: OrderIntent, result: ExecutionResult, run_date: date) -> None:
        """
        Critic #2 Fixes #2, #17, #22, #27:
        Reconciles ANY `result.filled_qty > 0` regardless of terminal status (including
        PARTIALLY_FILLED or CANCELLED-after-partial-fill!), splits partial lots cleanly,
        consumes FIFO lots on non-lot-specific equity sells, and persists qualified conIds!
        """
        if result.filled_qty <= 0 or result.status == "ERROR":
            return

        if intent.asset_class == AssetClass.EQUITY:
            if intent.action == "BUY":
                lot = TaxLot(
                    lot_id=f"LOT-{intent.symbol.upper()}-{run_date.isoformat()}-{uuid.uuid4().hex[:6]}",
                    account_id=intent.account_id,
                    account_type=intent.account_type,
                    symbol=intent.symbol.upper(),
                    quantity=float(result.filled_qty),
                    cost_basis_per_share=float(result.avg_fill_price),
                    acquired_at=run_date,
                )
                self.ledger.add_tax_lot(lot)
            elif intent.action == "SELL":
                if intent.lot_id:
                    self.ledger.close_tax_lot(
                        lot_id=intent.lot_id,
                        close_date=run_date,
                        close_price=float(result.avg_fill_price),
                        sold_qty=float(result.filled_qty),
                        wash_window_days=self.config.risk.wash_sale_window_days,
                    )
                else:
                    self.ledger.close_tax_lots_fifo(
                        account_id=intent.account_id,
                        symbol=intent.symbol.upper(),
                        sold_qty=float(result.filled_qty),
                        close_date=run_date,
                        close_price=float(result.avg_fill_price),
                        wash_window_days=self.config.risk.wash_sale_window_days,
                    )
        elif intent.intent_type == IntentType.XSP_OPEN_CREDIT_SPREAD:
            s_leg = [l for l in intent.legs if l.action == "SELL"][0]
            l_leg = [l for l in intent.legs if l.action == "BUY"][0]
            width = s_leg.strike - l_leg.strike
            max_loss = max(0.0, (width - result.avg_fill_price) * 100.0 * result.filled_qty)
            short_cid = result.raw_order_spec.get("short_con_id", s_leg.con_id)
            long_cid = result.raw_order_spec.get("long_con_id", l_leg.con_id)
            sp = OpenSpreadPosition(
                spread_id=intent.spread_id or f"XSP-{s_leg.expiry}-{s_leg.strike:.2f}P-{l_leg.strike:.2f}P-{run_date.isoformat()}",
                account_id=intent.account_id,
                account_type=intent.account_type,
                symbol=intent.symbol.upper(),
                expiry=s_leg.expiry,
                short_strike=s_leg.strike,
                long_strike=l_leg.strike,
                right="P",
                contracts=result.filled_qty,
                entry_credit=result.avg_fill_price,
                max_loss_total=max_loss,
                opened_at=run_date,
                short_con_id=short_cid,
                long_con_id=long_cid,
            )
            self.ledger.record_open_spread(sp)
        elif intent.intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD and intent.spread_id:
            entry_cr = float(intent.metadata.get("entry_credit", 0.0))
            realized = (entry_cr - result.avg_fill_price) * 100.0 * result.filled_qty - result.commission_usd
            self.ledger.close_spread(
                intent.spread_id,
                run_date,
                realized,
                closed_contracts=result.filled_qty,
            )

    def _write_reports(self, report: Dict[str, Any]) -> None:
        out_dir = self.config.resolve_path(self.config.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "ibkr_bot_report.json"
        md_path = out_dir / "ibkr_bot_report.md"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        mkt = report["market_regime"]
        vrp_str = f"{mkt['vrp_vol_pts']:+.2f}" if mkt["vrp_vol_pts"] is not None else "N/A"
        rv_str = f"{mkt['rv21_spy_pct']:.2f}%" if mkt["rv21_spy_pct"] is not None else "N/A"
        tr_str = f"{mkt['vix_term_ratio']:.3f}" if mkt["vix_term_ratio"] is not None else "N/A"

        lines: List[str] = [
            f"# IBKR Dual-Tranche Bot Digest ({report['trade_date']} | Mode: `{report['mode']}`)",
            "",
            "## Market & Volatility Regime",
            f"- **SPY / XSP Spot:** `${mkt['spy_spot']:.2f}` / `${mkt['xsp_spot']:.2f}`",
            f"- **VIX / VIX3M / Ratio:** `{mkt['vix']:.2f}` / `{mkt['vix3m']:.2f}` / `{tr_str}` (Gate: `VIX >= 20`, `Ratio <= 1.05`)",
            f"- **Live IV30 vs SPY RV21 (VRP):** `{mkt['effective_iv30_pct']:.2f}%` vs `{rv_str}` -> **`{vrp_str}` vol pts** (Gate: `>= +4.0` pts)",
            f"- **Paper Soak Progress:** `{report['paper_soak']['paper_days_logged']}/90` paper trading days logged",
            f"- **COI Hard-Blocked Tickers:** `{report['compliance']['coi_blocked_count']}` (MedTech review blocked: `{report['compliance']['medtech_blocked']}`)",
            f"- **Fidelity Shadow CSV ({report['compliance']['fidelity_export_date']}):** `{', '.join(report['compliance']['fidelity_shadow_tickers'])}`",
            "",
        ]
        for tr in report["tranches"]:
            lines.append(f"## Tranche: {tr['account_type']} (`{tr['account_id']}`)")
            lines.append(
                f"- **NAV:** `${tr['post_nav_usd']:,.2f}` | **Cash:** `${tr['post_cash_usd']:,.2f}` | "
                f"**Open XSP Spreads:** `{tr['open_spreads_count']}` (Risk: `${tr['open_options_risk_usd']:,.2f}`)"
            )
            lines.append(f"- **XSP Overlay Status:** {tr['xsp_gate_status']}")
            lines.append(f"- **Positions:** `{tr['post_positions']}`")
            if tr["reconcile_notes"]:
                lines.append(f"- **Reconciliation Notes:** {'; '.join(tr['reconcile_notes'])}")
            if tr["executed"]:
                lines.append("\n### Executed / Simulated Orders")
                lines.append("| Type | Action | Symbol | Qty | Fill | Comm | Rationale |")
                lines.append("|---|---|---|---:|---:|---:|---|")
                for ex in tr["executed"]:
                    lines.append(
                        f"| `{ex['intent_type']}` | `{ex['action']}` | `{ex['symbol']}` | "
                        f"{ex['filled_qty']} | `${ex['avg_fill_price']:.2f}` | `${ex['commission_usd']:.2f}` | {ex['rationale']} |"
                    )
            if tr["rejected"]:
                lines.append("\n### Risk-Firewall Rejections / Skips")
                for rj in tr["rejected"]:
                    lines.append(f"- `{rj['intent_type']}` `{rj['symbol']}`: {'; '.join(rj['reasons'])}")
            lines.append("")

        if report["advisory_munger_watchlist"]:
            lines.append("## Advisory Munger Watchlist (READ-ONLY — ZERO AUTO-CHURN)")
            for item in report["advisory_munger_watchlist"]:
                lines.append(f"- **`{item['symbol']}`** (`{item['status']}`): {item['note']}")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
