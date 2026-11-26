"""Comprehensive adversarial & integration test suite for src/ibkr_bot."""

from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from ibkr_bot.broker import (
    SimulatedBroker,
    build_ibkr_combo_order_spec,
    compute_adaptive_step_bound,
    validate_whatif_order_state,
)
from ibkr_bot.config import (
    AccountType,
    BotConfig,
    ExecutionMode,
    LIVE_ENV_TOKEN_NAME,
    LIVE_ENV_TOKEN_VALUE,
    build_default_config,
)
from ibkr_bot.engine import DualTrancheBotEngine
from ibkr_bot.ledger import StateLedger
from ibkr_bot.models import (
    AccountSnapshot,
    AssetClass,
    ExecutionResult,
    IntentType,
    MarketRegime,
    OpenSpreadPosition,
    OptionLegSpec,
    OrderIntent,
    TaxLot,
    is_irs_long_term,
)
from ibkr_bot.risk import COIConfigError, RiskFirewall
from ibkr_bot.signals import (
    build_advisory_munger_digest,
    generate_spread_exit_intents,
    generate_xsp_overlay_intent,
)


def _sample_regime(
    vix: float = 22.5,
    vix3m: float = 23.0,
    rv21: float = 16.0,
    xsp_spot: float = 600.0,
    quote_age: float = 10.0,
) -> MarketRegime:
    return MarketRegime(
        as_of=datetime.now(timezone.utc),
        spy_spot=xsp_spot,
        xsp_spot=xsp_spot,
        vix=vix,
        vix3m=vix3m,
        rv21_spy_pct=rv21,
        xsp_atm_iv30_pct=vix,
        tbill_yield_pct=4.20,
        quote_age_seconds=quote_age,
        prices={
            "SPY": xsp_spot,
            "XSP": xsp_spot,
            "ITOT": 130.0,
            "SCHB": 23.0,
            "SCHG": 28.0,
            "VUG": 410.0,
            "SGOV": 100.5,
            "VOO": 550.0,
            "VGT": 630.0,
            "USFR": 50.4,
            "VTI": 375.0,
            "LLY": 850.0,
            "SIBN": 24.0,
        },
    )


def _sample_xsp_puts(spot: float = 600.0) -> pd.DataFrame:
    # Realistic 45-DTE XSP put chain with clean $5/$10 strikes, tight bid-ask, high OI
    rows = [
        {"strike": 590.0, "bid": 7.40, "ask": 7.46, "openInterest": 1200, "impliedVolatility": 0.22, "contractSize": "REGULAR"},
        {"strike": 575.0, "bid": 4.20, "ask": 4.25, "openInterest": 1800, "impliedVolatility": 0.24, "contractSize": "REGULAR"},
        {"strike": 570.0, "bid": 3.45, "ask": 3.50, "openInterest": 2500, "impliedVolatility": 0.25, "contractSize": "REGULAR"},
        {"strike": 565.0, "bid": 2.75, "ask": 2.80, "openInterest": 2200, "impliedVolatility": 0.26, "contractSize": "REGULAR"},
        {"strike": 560.0, "bid": 2.15, "ask": 2.20, "openInterest": 3100, "impliedVolatility": 0.27, "contractSize": "REGULAR"},
        {"strike": 555.0, "bid": 1.65, "ask": 1.70, "openInterest": 1900, "impliedVolatility": 0.28, "contractSize": "REGULAR"},
        {"strike": 550.0, "bid": 1.10, "ask": 1.14, "openInterest": 2400, "impliedVolatility": 0.29, "contractSize": "REGULAR"},
        {"strike": 545.0, "bid": 0.72, "ask": 0.76, "openInterest": 1700, "impliedVolatility": 0.30, "contractSize": "REGULAR"},
    ]
    return pd.DataFrame(rows)


def _make_cfg(tmp_path: Path, mode: ExecutionMode = ExecutionMode.DRY_RUN, **kwargs) -> BotConfig:
    cfg = build_default_config(mode=mode, **kwargs)
    cfg.db_path = tmp_path / f"test_{mode.value}.sqlite3"
    cfg.report_dir = tmp_path / "reports"
    # Keep Fidelity CSV age check satisfied relative to fixed test trade dates (CSV is Nov-12-2026)
    cfg.risk.fidelity_csv_max_age_days = 365
    return cfg


def _make_xsp_open_intent(
    account_id: str = "DU_TAXABLE_SIM",
    account_type: AccountType = AccountType.TAXABLE,
    trade_date: date = date(2026, 11, 24),
    k_short: float = 570.0,
    k_long: float = 560.0,
    credit: float = 1.25,
    qty: int = 1,
    expiry: str = "2027-01-08",
    dte: int = 45,
    is_proxy: bool = False,
) -> OrderIntent:
    legs = [
        OptionLegSpec("XSP", expiry, k_short, "P", "SELL", bid=3.45, ask=3.50, open_interest=2500, delta=-0.20, is_european=True),
        OptionLegSpec("XSP", expiry, k_long, "P", "BUY", bid=2.15, ask=2.20, open_interest=3100, delta=-0.12, is_european=True),
    ]
    return OrderIntent(
        account_id=account_id,
        account_type=account_type,
        intent_type=IntentType.XSP_OPEN_CREDIT_SPREAD,
        asset_class=AssetClass.OPTION_COMBO,
        symbol="XSP",
        action="BUY",
        quantity=qty,
        limit_price=credit,
        estimated_mid_price=credit + 0.02,
        rationale="Test XSP open",
        trade_date=trade_date,
        is_risk_reducing=False,
        legs=legs,
        spread_id=f"XSP-{expiry}-{k_short:.2f}P-{k_long:.2f}P-{trade_date.isoformat()}",
        max_loss_total_usd=(k_short - k_long - credit) * 100.0 * qty,
        net_credit_per_share=credit,
        dte_days=dte,
        is_proxy_chain=is_proxy,
    )


# =============================================================================
# 1. COI & FIDELITY SHADOW COMPLIANCE TESTS (FAIL-CLOSED)
# =============================================================================
def test_coi_hard_block_and_fail_closed_missing_file(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    with StateLedger(cfg.effective_db_path, mode=cfg.mode.value) as ledger:
        fw = RiskFirewall(cfg, ledger)
        assert "LLY" in fw.coi_blocked
        assert "SIBN" in fw.coi_blocked  # MedTech review blocked by default
        assert "ISRG" in fw.coi_blocked

        snap = AccountSnapshot("DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 20_000.0, 20_000.0, 20_000.0)
        regime = _sample_regime()
        for bad_sym in ["LLY", "SIBN"]:
            intent = OrderIntent(
                account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
                intent_type=IntentType.CORE_BUY, asset_class=AssetClass.EQUITY,
                symbol=bad_sym, action="BUY", quantity=10, limit_price=25.0,
                rationale="COI test", trade_date=date(2026, 11, 24),
            )
            dec = fw.evaluate(intent, snap, regime)
            assert not dec.approved
            assert any("COI HARD BLOCK" in r for r in dec.reasons)

        # Advisory digest also filters MedTech review tickers (Critic #2 Fix #27)
        digest = build_advisory_munger_digest(cfg, fw.coi_blocked, max_rows=50)
        assert all(d["symbol"] not in fw.coi_blocked for d in digest)

    # Missing COI file MUST raise COIConfigError (fail-closed!)
    cfg_missing = _make_cfg(tmp_path)
    cfg_missing.risk.coi_exclusions_path = tmp_path / "nonexistent_coi.json"
    with StateLedger(cfg_missing.effective_db_path, mode=cfg_missing.mode.value) as ledger2:
        with pytest.raises(COIConfigError):
            RiskFirewall(cfg_missing, ledger2)


def test_fidelity_segregation_and_stale_csv_fail_closed(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    cfg.risk.fidelity_csv_max_age_days = 5  # CSV is Nov-12-2026 -> stale on Nov-24-2026 (12d old)!
    with StateLedger(cfg.effective_db_path, mode=cfg.mode.value) as ledger:
        fw = RiskFirewall(cfg, ledger)
        assert "VTI" in fw.fidelity_shadow
        assert "QQQM" in fw.fidelity_shadow
        assert "ITOT" not in fw.fidelity_shadow

        # Stale Fidelity CSV blocks Taxable TLH_SELL fail-closed
        snap = AccountSnapshot("DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 10_000.0, 10_000.0, 10_000.0, positions={"ITOT": 100.0})
        tlh_sell = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.TLH_SELL, asset_class=AssetClass.EQUITY,
            symbol="ITOT", action="SELL", quantity=100, limit_price=120.0,
            rationale="TLH test", trade_date=date(2026, 11, 24), is_risk_reducing=True, lot_id="L1",
        )
        dec = fw.evaluate(tlh_sell, snap, _sample_regime())
        assert not dec.approved
        assert any("FIDELITY SHADOW STALE" in r for r in dec.reasons)

        # Trying to buy Fidelity-held VTI in IBKR is blocked
        buy_vti = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.CORE_BUY, asset_class=AssetClass.EQUITY,
            symbol="VTI", action="BUY", quantity=5, limit_price=375.0,
            rationale="Fidelity overlap test", trade_date=date(2026, 11, 24),
        )
        dec2 = fw.evaluate(buy_vti, snap, _sample_regime())
        assert not dec2.approved
        assert any("FIDELITY SEGREGATION GUARD" in r for r in dec2.reasons)


# =============================================================================
# 2. IRS REV. RUL. 2008-5 FULL 61-DAY WASH-SALE + LTCG + FIFO + QCC TESTS
# =============================================================================
def test_irs_61_day_cross_account_wash_sale_forward_and_backward(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    # Temporarily add ITOT to Roth allowlist solely to prove Rev. Rul. 2008-5 blocks Roth buys!
    cfg.tranches[1].tlh_rings = [["ITOT"]]
    with StateLedger(cfg.effective_db_path, mode=cfg.mode.value) as ledger:
        fw = RiskFirewall(cfg, ledger)
        # 1) FORWARD 30-day block: Taxable closes ITOT lot at a loss on Nov 20 -> blocks Roth buy on Nov 24!
        lot = TaxLot("LOT-LOSS", "DU_TAXABLE_SIM", AccountType.TAXABLE, "ITOT", 100.0, 145.0, date(2026, 10, 1))
        ledger.add_tax_lot(lot)
        pnl = ledger.close_tax_lot("LOT-LOSS", date(2026, 11, 20), 130.0, wash_window_days=31)
        assert pnl == pytest.approx(-1500.0)

        roth_snap = AccountSnapshot("DU_ROTH_SIM", AccountType.ROTH_IRA, 15_000.0, 5_000.0, 5_000.0, 5_000.0)
        roth_buy_itot = OrderIntent(
            account_id="DU_ROTH_SIM", account_type=AccountType.ROTH_IRA,
            intent_type=IntentType.CORE_BUY, asset_class=AssetClass.EQUITY,
            symbol="ITOT", action="BUY", quantity=10, limit_price=130.0,
            rationale="Cross-account wash test", trade_date=date(2026, 11, 24),
        )
        dec_fwd = fw.evaluate(roth_buy_itot, roth_snap, _sample_regime())
        assert not dec_fwd.approved
        assert any("Rev. Rul. 2008-5" in r for r in dec_fwd.reasons)

        # 2) BACKWARD 30-day block: Roth acquired SCHG on Nov 10 -> Taxable TLH_SELL of older SCHG lot on Nov 24 BLOCKED!
        ledger.add_tax_lot(TaxLot("LOT-ROTH-RECENT", "DU_ROTH_SIM", AccountType.ROTH_IRA, "SCHG", 50.0, 29.0, date(2026, 11, 10)))
        old_lot = TaxLot("LOT-TAX-OLD", "DU_TAXABLE_SIM", AccountType.TAXABLE, "SCHG", 200.0, 34.0, date(2026, 6, 1))
        ledger.add_tax_lot(old_lot)
        tax_snap = AccountSnapshot(
            "DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 10_000.0, 10_000.0, 10_000.0,
            positions={"SCHG": 200.0}, lots=[old_lot],
        )
        tlh_sell = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.TLH_SELL, asset_class=AssetClass.EQUITY,
            symbol="SCHG", action="SELL", quantity=200, limit_price=28.0,
            rationale="Backward wash test", trade_date=date(2026, 11, 24),
            is_risk_reducing=True, lot_id="LOT-TAX-OLD", lot_acquired_date=date(2026, 6, 1),
        )
        dec_bwd = fw.evaluate(tlh_sell, tax_snap, _sample_regime())
        assert not dec_bwd.approved
        assert any("Backward 30d" in r for r in dec_bwd.reasons)


def test_leap_year_ltcg_and_ibkr_fifo_tlh_guard(tmp_path: Path) -> None:
    # Exact IRS IRC §1222(3) leap-year check: 2024-02-28 -> 2025-02-28 is 366 days but STILL short-term!
    assert not is_irs_long_term(date(2024, 2, 28), date(2025, 2, 28))
    assert is_irs_long_term(date(2024, 2, 28), date(2025, 3, 1))
    assert not is_irs_long_term(date(2024, 2, 29), date(2025, 2, 28))
    assert is_irs_long_term(date(2024, 2, 29), date(2025, 3, 1))

    cfg = _make_cfg(tmp_path)
    with StateLedger(cfg.effective_db_path, mode=cfg.mode.value) as ledger:
        fw = RiskFirewall(cfg, ledger)
        older_gain = TaxLot("LOT-OLD-GAIN", "DU_TAXABLE_SIM", AccountType.TAXABLE, "ITOT", 100.0, 100.0, date(2024, 1, 10))
        newer_loss = TaxLot("LOT-NEW-LOSS", "DU_TAXABLE_SIM", AccountType.TAXABLE, "ITOT", 100.0, 145.0, date(2026, 9, 1))
        snap = AccountSnapshot(
            "DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 10_000.0, 10_000.0, 10_000.0,
            positions={"ITOT": 200.0}, lots=[older_gain, newer_loss],
        )
        # IBKR FIFO trap MUST block partial-position TLH when older appreciated lot exists!
        tlh_sell = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.TLH_SELL, asset_class=AssetClass.EQUITY,
            symbol="ITOT", action="SELL", quantity=100, limit_price=130.0,
            rationale="FIFO trap test", trade_date=date(2026, 11, 24),
            is_risk_reducing=True, lot_id="LOT-NEW-LOSS", lot_acquired_date=date(2026, 9, 1),
        )
        dec_fifo = fw.evaluate(tlh_sell, snap, _sample_regime())
        assert not dec_fifo.approved
        assert any("IBKR FIFO TAX TRAP" in r for r in dec_fifo.reasons)

        # Fail-CLOSED Taxable CORE_REBALANCE_SELL when lot metadata omitted OR <= 1yr appreciated
        rebal_missing_meta = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.CORE_REBALANCE_SELL, asset_class=AssetClass.EQUITY,
            symbol="ITOT", action="SELL", quantity=50, limit_price=130.0,
            rationale="Missing lot metadata", trade_date=date(2026, 11, 24), is_risk_reducing=True,
        )
        dec_missing = fw.evaluate(rebal_missing_meta, snap, _sample_regime())
        assert not dec_missing.approved
        assert any("TAXABLE LTCG FAIL-CLOSED" in r for r in dec_missing.reasons)


def test_qcc_ex_div_and_earnings_defense_in_depth_guards(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    with StateLedger(cfg.effective_db_path, mode=cfg.mode.value) as ledger:
        fw = RiskFirewall(cfg, ledger)
        regime = _sample_regime()
        regime.prices["MSFT"] = 500.0
        regime.earnings_calendar["MSFT"] = date(2026, 12, 10)
        regime.ex_dividend_calendar["MSFT"] = {"ex_date": date(2026, 11, 27), "amount": 2.00}
        young_lot = TaxLot("L-MSFT", "DU_TAXABLE_SIM", AccountType.TAXABLE, "MSFT", 100.0, 480.0, date(2026, 6, 1))
        snap = AccountSnapshot("DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 20_000.0, 20_000.0, 20_000.0, lots=[young_lot])

        # Short American call on MSFT before earnings & ex-div with low extrinsic on <1yr Taxable lot
        bad_opt = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.SINGLE_STOCK_OPTION, asset_class=AssetClass.OPTION_COMBO,
            symbol="MSFT", action="BUY", quantity=1, limit_price=1.50,
            rationale="Defense-in-depth test", trade_date=date(2026, 11, 24),
            dte_days=30,
            legs=[
                OptionLegSpec("MSFT", "2026-12-24", 495.0, "C", "SELL", bid=6.00, ask=6.20, is_european=False),
                OptionLegSpec("MSFT", "2026-12-24", 505.0, "C", "BUY", bid=2.00, ask=2.10, is_european=False),
            ],
        )
        dec = fw.evaluate(bad_opt, snap, regime)
        assert not dec.approved
        joined = " | ".join(dec.reasons)
        assert "CRITIC #1 HARD BLOCK" in joined
        assert "EARNINGS BLACKOUT" in joined
        assert "QCC GUARD" in joined
        assert "EX-DIV ASSIGNMENT GUARD" in joined


# =============================================================================
# 3. IBKR BAG SIGN CONVENTION, ADAPTIVE STEPPING CAPS & WHATIF FAIL-CLOSED
# =============================================================================
def test_ibkr_bag_sign_convention_and_adaptive_step_caps(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    open_intent = _make_xsp_open_intent(credit=1.25, k_short=570.0, k_long=560.0)
    open_spec = build_ibkr_combo_order_spec(open_intent)
    assert open_spec["order_action"] == "BUY"
    assert open_spec["signed_limit_price"] == -1.25  # MUST BE NEGATIVE FOR CREDIT!
    assert open_spec["guaranteed_combo"] is True
    assert open_spec["trading_class"] == "XSP"
    assert open_spec["legs"][0]["action"] == "SELL" and open_spec["legs"][0]["strike"] == 570.0
    assert open_spec["legs"][1]["action"] == "BUY" and open_spec["legs"][1]["strike"] == 560.0

    # Adaptive stepping bound for OPEN credit spread MUST stay <= -min_net_credit (-0.80) and < 0!
    open_intent.limit_price = 0.82
    open_intent.max_step_limit_price = 0.80
    bound_open = compute_adaptive_step_bound(open_intent, cfg)
    assert bound_open == -0.80
    assert bound_open < 0.0

    # Closing credit spread -> POSITIVE limit price (maximum debit paid!)
    close_intent = OrderIntent(
        account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
        intent_type=IntentType.XSP_CLOSE_CREDIT_SPREAD, asset_class=AssetClass.OPTION_COMBO,
        symbol="XSP", action="BUY", quantity=1, limit_price=0.55,
        rationale="Close spread", trade_date=date(2026, 12, 10), is_risk_reducing=True,
        spread_id="SP1",
        legs=[
            OptionLegSpec("XSP", "2027-01-08", 570.0, "P", "BUY", is_european=True),
            OptionLegSpec("XSP", "2027-01-08", 560.0, "P", "SELL", is_european=True),
        ],
    )
    close_spec = build_ibkr_combo_order_spec(close_intent)
    assert close_spec["order_action"] == "BUY"
    assert close_spec["signed_limit_price"] == +0.55  # MUST BE POSITIVE FOR DEBIT!
    assert close_spec["guaranteed_combo"] is True
    assert compute_adaptive_step_bound(close_intent, cfg) <= 9.95


def test_whatif_order_fail_closed_on_empty_list_and_unset_double() -> None:
    # Critic #2 Fix #1: ib_async returns [] on Error 201 -> MUST REJECT!
    ok_empty, _, _ = validate_whatif_order_state([], is_risk_reducing=False)
    assert not ok_empty

    # UNSET_DOUBLE sentinel (1.7976931348623157e+308) -> MUST REJECT!
    unset = SimpleNamespace(status="PreSubmitted", initMarginChange="1.7976931348623157E308", initMarginAfter="1.7976931348623157E308", equityWithLoanAfter="1.7976931348623157E308")
    ok_unset, _, _ = validate_whatif_order_state(unset, is_risk_reducing=False)
    assert not ok_unset

    # >95% margin utilization -> REJECT risk-adding open, but ALLOW risk-reducing stop-loss exit!
    tight_open = SimpleNamespace(status="PreSubmitted", initMarginChange="850.0", initMarginAfter="68000.0", equityWithLoanAfter="70000.0")
    ok_tight_open, _, _ = validate_whatif_order_state(tight_open, is_risk_reducing=False)
    assert not ok_tight_open

    tight_close = SimpleNamespace(status="PreSubmitted", initMarginChange="-850.0", initMarginAfter="67150.0", equityWithLoanAfter="70000.0")
    ok_tight_close, _, _ = validate_whatif_order_state(tight_close, is_risk_reducing=True)
    assert ok_tight_close


# =============================================================================
# 4. RISK CAPS, KILL SWITCH, MODE ISOLATION, RETRY & PARTIAL FILLS
# =============================================================================
def test_xsp_risk_caps_nonstandard_strike_and_kill_switch(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    with StateLedger(cfg.effective_db_path, mode=cfg.mode.value) as ledger:
        fw = RiskFirewall(cfg, ledger)
        regime = _sample_regime(vix=22.5, vix3m=23.0, rv21=16.0)

        # $12.8k Roth IRA: 2.5% NAV cap = $320 -> 1x $10-wide XSP spread ($875 max loss) MUST be rejected/sized to 0!
        roth_snap = AccountSnapshot("DU_ROTH_SIM", AccountType.ROTH_IRA, 15_000.0, 5_000.0, 5_000.0, 5_000.0)
        xsp_roth = _make_xsp_open_intent(account_id="DU_ROTH_SIM", account_type=AccountType.ROTH_IRA)
        dec_roth = fw.evaluate(xsp_roth, roth_snap, regime)
        assert not dec_roth.approved
        assert any("MAX-RISK-PER-TRADE CAP" in r for r in dec_roth.reasons)

        # Adjusted fractional strike (579.78) -> BLOCKED!
        tax_snap = AccountSnapshot("DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 15_000.0, 15_000.0, 15_000.0)
        bad_strike = _make_xsp_open_intent(k_short=579.78, k_long=569.78)
        dec_strike = fw.evaluate(bad_strike, tax_snap, regime)
        assert not dec_strike.approved
        assert any("Adjusted/non-standard strike" in r for r in dec_strike.reasons)

        # Daily -3% kill switch: blocks XSP open AND TLH_SELL, but ALLOWS verified XSP close!
        ledger.record_daily_nav(date(2026, 11, 23), "DU_TAXABLE_SIM", 70_000.0, 15_000.0, 875.0)
        sp = OpenSpreadPosition("SP-LIVE-1", "DU_TAXABLE_SIM", AccountType.TAXABLE, "XSP", "2027-01-08", 570.0, 560.0, "P", 1, 1.25, 875.0, date(2026, 11, 1))
        crashed_snap = AccountSnapshot(
            "DU_TAXABLE_SIM", AccountType.TAXABLE, 67_200.0, 15_000.0, 15_000.0, 15_000.0,
            positions={"ITOT": 100.0}, open_spreads=[sp],
        )  # -4.0% daily drop!
        dec_open_ks = fw.evaluate(_make_xsp_open_intent(), crashed_snap, regime)
        assert not dec_open_ks.approved
        assert any("DAILY KILL SWITCH" in r for r in dec_open_ks.reasons)

        tlh_ks = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.TLH_SELL, asset_class=AssetClass.EQUITY,
            symbol="ITOT", action="SELL", quantity=100, limit_price=120.0,
            rationale="Kill switch TLH symmetry", trade_date=date(2026, 11, 24),
            is_risk_reducing=True, lot_id="L1",
        )
        dec_tlh_ks = fw.evaluate(tlh_ks, crashed_snap, regime)
        assert not dec_tlh_ks.approved
        assert any("DAILY KILL SWITCH" in r for r in dec_tlh_ks.reasons)

        xsp_close = OrderIntent(
            account_id="DU_TAXABLE_SIM", account_type=AccountType.TAXABLE,
            intent_type=IntentType.XSP_CLOSE_CREDIT_SPREAD, asset_class=AssetClass.OPTION_COMBO,
            symbol="XSP", action="BUY", quantity=1, limit_price=0.55,
            rationale="Emergency close", trade_date=date(2026, 11, 24),
            is_risk_reducing=True, spread_id="SP-LIVE-1",
            legs=[
                OptionLegSpec("XSP", "2027-01-08", 570.0, "P", "BUY", is_european=True),
                OptionLegSpec("XSP", "2027-01-08", 560.0, "P", "SELL", is_european=True),
            ],
        )
        dec_close_ks = fw.evaluate(xsp_close, crashed_snap, regime)
        assert dec_close_ks.approved


def test_mode_isolation_retry_after_cancel_and_partial_fill_split(tmp_path: Path) -> None:
    shared_db = tmp_path / "shared.sqlite3"
    # Dry-run writes wash-sale block & open spread -> Paper mode on SAME db file MUST NOT see them!
    with StateLedger(shared_db, mode="dry-run") as dry_l, StateLedger(shared_db, mode="paper") as pap_l:
        dry_l.record_wash_sale_block("ITOT", "DU_TAXABLE_SIM", date(2026, 11, 20), 500.0)
        assert dry_l.get_active_wash_sale_block("ITOT", date(2026, 11, 24)) is not None
        assert pap_l.get_active_wash_sale_block("ITOT", date(2026, 11, 24)) is None

        # Retry after CANCELLED with filled_qty=0 MUST succeed (Critic #2 Fix #7)!
        intent = _make_xsp_open_intent()
        assert pap_l.reserve_order(intent) is True
        pap_l.finalize_order(intent, ExecutionResult(intent.idempotency_key, status="CANCELLED", filled_qty=0))
        assert pap_l.has_active_or_completed_order(intent.idempotency_key) is False
        assert pap_l.reserve_order(intent) is True  # Retry unlocked!

        # Partial fill (1 of 2) locks idempotency AND splits equity tax lot cleanly!
        pap_l.finalize_order(intent, ExecutionResult(intent.idempotency_key, status="PARTIALLY_FILLED", filled_qty=1, avg_fill_price=1.25))
        assert pap_l.has_active_or_completed_order(intent.idempotency_key) is True
        assert pap_l.reserve_order(intent) is False

        pap_l.add_tax_lot(TaxLot("LOT-100", "DU_TAXABLE_SIM", AccountType.TAXABLE, "ITOT", 100.0, 140.0, date(2026, 5, 1)))
        pnl_part = pap_l.close_tax_lot("LOT-100", date(2026, 11, 24), 130.0, sold_qty=40.0)
        assert pnl_part == pytest.approx(-400.0)
        rem_lots = pap_l.get_open_tax_lots("DU_TAXABLE_SIM", "ITOT")
        assert len(rem_lots) == 1 and rem_lots[0].quantity == pytest.approx(60.0)


def test_live_safety_guards_and_paper_no_fake_bsm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_ENV_TOKEN_NAME, raising=False)
    # Live mode on DU* paper account without token/flag/soak -> MUST fail with all violations!
    cfg_live = _make_cfg(tmp_path, mode=ExecutionMode.LIVE, port=4001)
    errs = cfg_live.validate_live_safety(paper_days_completed=5)
    assert len(errs) >= 4
    assert any("paper account" in e.lower() for e in errs)
    assert any("--confirm-live" in e for e in errs)
    assert any(LIVE_ENV_TOKEN_NAME in e for e in errs)
    assert any("Paper soak incomplete" in e for e in errs)

    # Paper mode refuses synthetic BSM fallback marks when live_spread_marks is None (Critic #2 Fix #3)
    cfg_pap = _make_cfg(tmp_path, mode=ExecutionMode.PAPER, port=4002)
    sp = OpenSpreadPosition("SP-PAP", "DU_TAXABLE_SIM", AccountType.TAXABLE, "XSP", "2026-12-10", 570.0, 560.0, "P", 1, 1.25, 875.0, date(2026, 11, 1))
    snap = AccountSnapshot("DU_TAXABLE_SIM", AccountType.TAXABLE, 75_000.0, 20_000.0, 20_000.0, 20_000.0, open_spreads=[sp])
    exits_no_marks = generate_spread_exit_intents(cfg_pap, cfg_pap.tranches[0], snap, _sample_regime(), date(2026, 11, 24), live_spread_marks=None)
    assert exits_no_marks == []
    # With live IBKR marks hitting 50% TP (debit $0.50 vs $1.25 entry credit) -> emits close!
    exits_live = generate_spread_exit_intents(cfg_pap, cfg_pap.tranches[0], snap, _sample_regime(), date(2026, 11, 24), live_spread_marks={"SP-PAP": (0.50, 0.54)})
    assert len(exits_live) == 1
    assert exits_live[0].intent_type == IntentType.XSP_CLOSE_CREDIT_SPREAD


# =============================================================================
# 5. END-TO-END DUAL-TRANCHE DRY-RUN & IDEMPOTENT RESTART TEST
# =============================================================================
def test_end_to_end_dual_tranche_engine_tlh_core_sweep_xsp_and_idempotency(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, roth_nav_usd=15_000.0, taxable_nav_usd=75_000.0)
    trade_dt = date(2026, 11, 24)
    regime = _sample_regime(vix=22.5, vix3m=23.0, rv21=16.0, xsp_spot=600.0)
    puts_df = _sample_xsp_puts(600.0)
    chain_tuple = ("2027-01-08", 45, puts_df, False)

    # Seed Taxable with an underwater ITOT lot (eligible for TLH -> SCHB!) + an open XSP spread hitting 50% TP!
    broker = SimulatedBroker(
        cfg,
        initial_positions={"DU_TAXABLE_SIM": {"ITOT": 100.0}, "DU_ROTH_SIM": {}},
        initial_cash={"DU_TAXABLE_SIM": 62_000.0, "DU_ROTH_SIM": 15_000.0},
    )
    with DualTrancheBotEngine(cfg, broker=broker) as engine:
        engine.ledger.add_tax_lot(
            TaxLot("LOT-ITOT-UNDERWATER", "DU_TAXABLE_SIM", AccountType.TAXABLE, "ITOT", 100.0, 145.0, date(2026, 9, 1))
        )
        engine.ledger.record_open_spread(
            OpenSpreadPosition("SP-TP-HIT", "DU_TAXABLE_SIM", AccountType.TAXABLE, "XSP", "2027-01-08", 565.0, 555.0, "P", 1, 1.50, 850.0, date(2026, 11, 1))
        )
        # Also seed an EXPIRED spread to verify auto-reconciliation!
        engine.ledger.record_open_spread(
            OpenSpreadPosition("SP-EXPIRED", "DU_TAXABLE_SIM", AccountType.TAXABLE, "XSP", "2026-11-20", 560.0, 550.0, "P", 1, 1.20, 880.0, date(2026, 10, 5))
        )

        report1 = engine.run_cycle(
            trade_date=trade_dt,
            regime=regime,
            xsp_chain_tuple=chain_tuple,
            live_spread_marks={"SP-TP-HIT": (0.60, 0.64)},  # >50% TP!
            write_reports=True,
        )

        tax_rep = [t for t in report1["tranches"] if t["account_type"] == "TAXABLE"][0]
        roth_rep = [t for t in report1["tranches"] if t["account_type"] == "ROTH_IRA"][0]

        tax_types = [e["intent_type"] for e in tax_rep["executed"]]
        # 1) Expired spread auto-reconciled; TP spread closed; TLH sold ITOT & bought SCHB; deployed core & SGOV; opened new XSP!
        assert any("EXPIRED" in n for n in tax_rep["reconcile_notes"])
        assert "XSP_CLOSE_CREDIT_SPREAD" in tax_types
        assert "TLH_SELL" in tax_types
        assert "TLH_BUY" in tax_types
        assert "SCHB" in tax_rep["post_positions"]
        assert "ITOT" not in tax_rep["post_positions"]  # Harvested & wash-locked!
        assert "SGOV" in tax_rep["post_positions"]
        assert "XSP_OPEN_CREDIT_SPREAD" in tax_types
        assert engine.ledger.get_active_wash_sale_block("ITOT", trade_dt) is not None

        # 2) Roth ($12.8k): deployed VOO + VGT + USFR; XSP auto-sized to 0 contracts (2.5% cap = $320 < $875 max loss!)
        assert "VOO" in roth_rep["post_positions"]
        assert "VGT" in roth_rep["post_positions"]
        assert "USFR" in roth_rep["post_positions"]
        assert "Sized to 0 contracts" in roth_rep["xsp_gate_status"]
        assert roth_rep["open_spreads_count"] == 0

        # 3) Immediate same-day restart MUST execute ZERO duplicate orders!
        report2 = engine.run_cycle(
            trade_date=trade_dt,
            regime=regime,
            xsp_chain_tuple=chain_tuple,
            live_spread_marks={},
            write_reports=False,
        )
        for tr in report2["tranches"]:
            assert tr["executed"] == [], f"Unexpected duplicate execution on restart: {tr['executed']}"
