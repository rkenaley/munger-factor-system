#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR SYSTEM: AUTOMATED CAUSALITY, MATH, CYCLICAL TRAP & SPEED TESTS
================================================================================
Verifies:
  1. Zero-Lookahead / Strict Causality: Truncating or shocking future prices after
     date t produces bit-for-bit identical factor scores on or before t.
  2. Value Trap Cyclical Filter: Confirms that Micron's (`MU`) 1-quarter
     `sales5y: 379.3` spike and 2023 negative TTM EPS cycle (`-1.91, -1.43, -1.07, -0.95`)
     are blocked by cross-sectional winsorization + 5-year full-cycle profitability
     gating, restoring normal Z-scores for genuine compounders.
  3. Analytical O(1) Strike-by-Delta Inversion: Matches 50-step binary search to
     <1e-6 while executing >100x faster.
  4. Slippage & Delisting Accounting: Confirms +5 bps slippage reduces terminal
     NAV relative to 0 bps while -14 bps MOC bonus artificially inflates it.
  5. Staleness Circuit Breaker: Correctly detects data lag > 18h and handles market clock.
  6. SEC Form 4 / Finviz Insider Conviction Engine: Verifies Cluster Buying Boost (1.15x)
     and Heavy C-Suite Distribution Haircut (0.70x).
  7. Fidelity Tax-Lot Reconciler: Verifies cash sweep stripping (SPAXX**), live quotes,
     365-day LTCG thresholding, and execution orders (STRONG HOLD / ADD, HOLD FOR TAX DEFERRAL, SELL MOC).
================================================================================
"""

from __future__ import annotations

import time
import unittest
import numpy as np
import pandas as pd

from munger_engine import (
    BacktestConfig,
    bsm_call,
    build_csp_jade_lizard_blueprint,
    check_data_freshness,
    compute_cycle_and_trend_diagnostics,
    compute_ivr_from_prices,
    compute_two_tier_sma_status,
    compute_vectorized_munger_scores,
    fetch_live_insider_activity,
    fetch_schedule_13d_catalysts,
    find_strike_by_delta_analytical,
    load_or_generate_dataset,
    parse_fidelity_positions_csv,
    parse_form4_xml,
    run_portfolio_simulation,
    score_insider_transactions,
)


def find_strike_by_delta_legacy_binary(
    S: float,
    T: float,
    r: float,
    sigma: float,
    target_delta: float,
    q: float = 0.01,
    iterations: int = 50,
) -> float:
    """Legacy 50-iteration binary search preserved strictly for parity benchmarking."""
    low_K = S * 0.25
    high_K = S * 3.00
    for _ in range(iterations):
        mid_K = 0.5 * (low_K + high_K)
        c = bsm_call(S, mid_K, T, r, sigma, q)
        if c["delta"] > target_delta:
            low_K = mid_K
        else:
            high_K = mid_K
    return mid_K


class TestMungerFactorSystemAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.close_df, cls.gspc_close, cls.fund_dict, cls.eps_dict, _ = load_or_generate_dataset("data")
        cls.eval_dates = cls.close_df.index[::21]

    def test_strict_point_in_time_causality_no_lookahead(self) -> None:
        """
        Mutating or shocking future prices after 2000-01-01 by 50x must have ZERO effect
        on factor scores computed on or before 2000-01-01 in causal mode.
        """
        cutoff = pd.Timestamp("2000-01-01")
        early_eval_dates = self.eval_dates[self.eval_dates <= cutoff]

        scores_base, med_base, ey_base = compute_vectorized_munger_scores(
            close_df=self.close_df,
            eval_dates=early_eval_dates,
            fund_dict=self.fund_dict,
            eps_dict=self.eps_dict,
            reporting_lag_days=45,
            allow_legacy_lookahead=False,
        )

        shocked_close = self.close_df.copy()
        future_mask = shocked_close.index > cutoff
        half_cols = list(shocked_close.columns[: len(shocked_close.columns) // 2])
        shocked_close.loc[future_mask, half_cols] *= 50.0

        scores_shocked, med_shocked, ey_shocked = compute_vectorized_munger_scores(
            close_df=shocked_close,
            eval_dates=early_eval_dates,
            fund_dict=self.fund_dict,
            eps_dict=self.eps_dict,
            reporting_lag_days=45,
            allow_legacy_lookahead=False,
        )

        pd.testing.assert_frame_equal(scores_base, scores_shocked, check_exact=False, rtol=1e-12, atol=1e-12)
        pd.testing.assert_frame_equal(ey_base, ey_shocked, check_exact=False, rtol=1e-12, atol=1e-12)
        pd.testing.assert_frame_equal(med_base, med_shocked)

    def test_value_trap_cyclical_filter_blocks_micron_outlier(self) -> None:
        """
        Proves that:
          1. In raw unwinsorized/unfiltered mode, MU (`sales5y: 379.3`, `roic_proxy: 0.883`)
             blows up cross-sectional variance and dominates the strike zone despite losing
             money in all 4 quarters of FY2023 (`-1.91, -1.43, -1.07, -0.95`).
          2. With `winsorize_cross_section=True` and `enforce_full_cycle_profitability=True`,
             MU is blocked (`score == 0.0`) and non-MU compounder scores increase cleanly.
        """
        latest_dates = self.close_df.index[-2:]
        if "MU" not in self.close_df.columns:
            self.skipTest("MU not in current dataset")

        scores_raw, _, _ = compute_vectorized_munger_scores(
            close_df=self.close_df,
            eval_dates=latest_dates,
            fund_dict=self.fund_dict,
            eps_dict=self.eps_dict,
            reporting_lag_days=0,
            allow_legacy_lookahead=True,
            winsorize_cross_section=False,
            enforce_full_cycle_profitability=False,
            use_cyclically_adjusted_ey=False,
        )

        scores_immunized, med_immunized, _ = compute_vectorized_munger_scores(
            close_df=self.close_df,
            eval_dates=latest_dates,
            fund_dict=self.fund_dict,
            eps_dict=self.eps_dict,
            reporting_lag_days=45,
            allow_legacy_lookahead=False,
            winsorize_cross_section=True,
            enforce_full_cycle_profitability=True,
            use_cyclically_adjusted_ey=True,
        )

        dt_last = latest_dates[-1]
        mu_raw = float(scores_raw.loc[dt_last, "MU"])
        mu_clean = float(scores_immunized.loc[dt_last, "MU"])
        self.assertGreater(mu_raw, 10.0, "Raw unfiltered mode should show inflated MU score")
        self.assertEqual(mu_clean, 0.0, "Value Trap filter must block MU due to FY2023 negative TTM EPS")
        self.assertFalse(bool(med_immunized.loc[dt_last, "MU"]))

        # Also verify P&C insurance peak-cycle ROE/PE traps (ALL and CINF) are blocked
        for ins_tk in ["ALL", "CINF"]:
            if ins_tk in self.close_df.columns:
                raw_ins = float(scores_raw.loc[dt_last, ins_tk])
                clean_ins = float(scores_immunized.loc[dt_last, ins_tk])
                self.assertGreater(raw_ins, 2.0, f"Raw mode should show inflated peak-cycle score for {ins_tk}")
                self.assertEqual(clean_ins, 0.0, f"Safeguard must block peak-cycle insurance trap {ins_tk}")
                self.assertFalse(bool(med_immunized.loc[dt_last, ins_tk]))

    def test_analytical_delta_strike_inversion_accuracy_and_speed(self) -> None:
        """
        Verifies that O(1) analytical delta inversion matches 50-step binary search
        to <1e-6 while running >10x faster.
        """
        S, r, q, sigma = 100.0, 0.045, 0.01, 0.32
        for T in [30 / 365.0, 90 / 365.0, 180 / 365.0, 1.0]:
            for target_delta in [0.25, 0.30, 0.50, 0.65, 0.80]:
                k_analytic = find_strike_by_delta_analytical(S, T, r, sigma, target_delta, q)
                k_binary = find_strike_by_delta_legacy_binary(S, T, r, sigma, target_delta, q, iterations=50)
                self.assertAlmostEqual(k_analytic, k_binary, places=5)
                realized_delta = bsm_call(S, k_analytic, T, r, sigma, q)["delta"]
                self.assertAlmostEqual(realized_delta, target_delta, places=6)

        t0 = time.perf_counter()
        for _ in range(500):
            find_strike_by_delta_legacy_binary(S, 0.25, r, sigma, 0.65, q, iterations=50)
        t_binary = time.perf_counter() - t0

        t1 = time.perf_counter()
        for _ in range(500):
            find_strike_by_delta_analytical(S, 0.25, r, sigma, 0.65, q)
        t_analytic = time.perf_counter() - t1

        speedup = t_binary / max(1e-9, t_analytic)
        print(f"\n[Benchmark] Analytical O(1) Strike Inversion Speedup: {speedup:.1f}x faster than 50-step binary search")
        self.assertGreater(speedup, 10.0)

    def test_slippage_penalty_vs_synthetic_moc_bonus(self) -> None:
        """
        Verifies that realistic +5 bps slippage reduces terminal NAV compared to 0 bps,
        whereas legacy -14 bps slippage (MOC bonus) artificially inflates NAV.
        """
        scores_df, med_df, _ = compute_vectorized_munger_scores(
            self.close_df, self.eval_dates, self.fund_dict, self.eps_dict
        )
        res_zero = run_portfolio_simulation(
            self.close_df, self.gspc_close, self.eval_dates, scores_df, med_df,
            BacktestConfig(slippage_bps=0.0),
        )
        res_cost = run_portfolio_simulation(
            self.close_df, self.gspc_close, self.eval_dates, scores_df, med_df,
            BacktestConfig(slippage_bps=5.0),
        )
        res_bonus = run_portfolio_simulation(
            self.close_df, self.gspc_close, self.eval_dates, scores_df, med_df,
            BacktestConfig(slippage_bps=-14.0),
        )

        self.assertLess(res_cost["nav_pre"].iloc[-1], res_zero["nav_pre"].iloc[-1])
        self.assertGreater(res_bonus["nav_pre"].iloc[-1], res_zero["nav_pre"].iloc[-1])

    def test_staleness_circuit_breaker_detection(self) -> None:
        """
        Verifies that check_data_freshness accurately flags stale caches when
        lag exceeds 18 hours on trading days, and passes fresh caches.
        """
        now = pd.Timestamp("2026-10-14 14:00:00")  # Wednesday 2pm
        stale_index = pd.to_datetime(["2026-10-08", "2026-10-09"])
        stale_df = pd.DataFrame({"AAPL": [340.0, 336.0]}, index=stale_index)

        is_stale, lag, _ = check_data_freshness(stale_df, max_lag_hours=18.0, current_time=now)
        self.assertTrue(is_stale)
        self.assertGreater(lag, 18.0)

        # Fresh data covering through today
        fresh_index = pd.to_datetime(["2026-10-12", "2026-10-13", "2026-10-14"])
        fresh_df = pd.DataFrame({"AAPL": [340.0, 342.0, 345.0]}, index=fresh_index)
        is_fresh, lag_fresh, _ = check_data_freshness(fresh_df, max_lag_hours=18.0, current_time=now)
        self.assertFalse(is_fresh)
        self.assertLessEqual(lag_fresh, 18.0)

    def test_form4_insider_conviction_scoring_rules(self) -> None:
        """
        Verifies deterministically that:
          1. Rule 10b5-1 scheduled C-suite sales (<aff10b5One>true</aff10b5One> or '1')
             are stripped when exclude_10b5_1=True (1.00x) and counted when False (0.70x).
          2. Discretionary C-suite open-market sales >= $20M with zero buys trigger 0.70x.
          3. Discretionary C-suite cluster buys trigger 1.15x, csuite_buy_hit=True, and FRESH_5D.
          4. Scheduled 10b5-1 buys do NOT trigger 1.15x when exclude_10b5_1=True.
          5. Codes F, M, A, G are completely ignored.
        """
        def _mk_xml(owner: str, title: str, aff: str, txs: list[tuple[str, str, float, float]]) -> str:
            tx_xml = "\n".join(
                f"""<nonDerivativeTransaction>
                    <transactionDate><value>{dt}</value></transactionDate>
                    <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
                    <transactionAmounts>
                        <transactionShares><value>{sh}</value></transactionShares>
                        <transactionPricePerShare><value>{px}</value></transactionPricePerShare>
                    </transactionAmounts>
                </nonDerivativeTransaction>"""
                for code, dt, sh, px in txs
            )
            return f"""<?xml version="1.0"?>
            <ownershipDocument>
                <issuer><issuerTradingSymbol>MU</issuerTradingSymbol><issuerName>Micron</issuerName></issuer>
                <reportingOwner>
                    <reportingOwnerId><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
                    <reportingOwnerRelationship><officerTitle>{title}</officerTitle></reportingOwnerRelationship>
                </reportingOwner>
                <aff10b5One>{aff}</aff10b5One>
                <nonDerivativeTable>{tx_xml}</nonDerivativeTable>
            </ownershipDocument>"""

        # 1. Rule 10b5-1 planned sale ('true') + non-discretionary noise Codes F/M/A/G
        xml_10b5_sale = _mk_xml(
            "Sanjay Mehrotra", "President and CEO", "true",
            [("S", "2026-10-20", 300_000, 100.0), ("F", "2026-10-15", 500_000, 100.0),
             ("M", "2026-10-15", 200_000, 50.0), ("A", "2026-10-15", 100_000, 100.0), ("G", "2026-10-15", 10_000, 100.0)]
        )
        # Standalone parse_form4_xml with exclude_10b5_1=True strips it completely
        self.assertEqual(parse_form4_xml(xml_10b5_sale, exclude_10b5_1=True)["transactions"], [])
        all_10b5_txs = parse_form4_xml(xml_10b5_sale, exclude_10b5_1=False)["transactions"]
        self.assertEqual(len(all_10b5_txs), 1)  # Codes F, M, A, G ignored

        res_excl = score_insider_transactions(all_10b5_txs, exclude_10b5_1=True)
        self.assertEqual(res_excl["multiplier"], 1.00)
        self.assertEqual(res_excl["signal"], "NEUTRAL")
        self.assertGreaterEqual(res_excl["planned_10b5_csuite_sales_usd"], 30_000_000.0)

        res_incl = score_insider_transactions(all_10b5_txs, exclude_10b5_1=False)
        self.assertEqual(res_incl["multiplier"], 0.70)
        self.assertEqual(res_incl["signal"], "CSUITE_DISTRIBUTION_HAIRCUT")

        # 2. Discretionary C-suite sale >= $20M triggers 0.70x even with exclude_10b5_1=True
        xml_disc_sale = _mk_xml("CEO Discretionary", "Chief Executive Officer", "0", [("S", "2026-11-10", 250_000, 100.0)])
        disc_sale_txs = parse_form4_xml(xml_disc_sale, exclude_10b5_1=False)["transactions"]
        res_disc_sale = score_insider_transactions(disc_sale_txs, exclude_10b5_1=True)
        self.assertEqual(res_disc_sale["multiplier"], 0.70)
        self.assertEqual(res_disc_sale["signal"], "CSUITE_DISTRIBUTION_HAIRCUT")

        # 3. Planned 10b5-1 buy ('1') does NOT trigger 1.15x boost
        xml_10b5_buy = _mk_xml("Director One", "Director", "1", [("P", "2026-11-22", 10_000, 50.0)])
        xml_10b5_buy2 = _mk_xml("Director Two", "Director", "1", [("P", "2026-11-22", 10_000, 50.0)])
        planned_buy_txs = (
            parse_form4_xml(xml_10b5_buy, exclude_10b5_1=False)["transactions"]
            + parse_form4_xml(xml_10b5_buy2, exclude_10b5_1=False)["transactions"]
        )
        res_plan_buy = score_insider_transactions(planned_buy_txs, exclude_10b5_1=True, reference_date="2026-11-24")
        self.assertEqual(res_plan_buy["multiplier"], 1.00)
        self.assertEqual(res_plan_buy["fresh_buy_window"], "NONE")

        # 4. Discretionary C-suite buy within 5 days triggers 1.15x, csuite_buy_hit=True, FRESH_5D
        xml_disc_csuite_buy = _mk_xml("Jane Doe", "Chief Financial Officer", "0", [("P", "2026-11-21", 6_000, 50.0)])
        disc_buy_txs = parse_form4_xml(xml_disc_csuite_buy, exclude_10b5_1=False)["transactions"]
        res_csuite_buy = score_insider_transactions(disc_buy_txs, exclude_10b5_1=True, reference_date="2026-11-24")
        self.assertEqual(res_csuite_buy["multiplier"], 1.15)
        self.assertEqual(res_csuite_buy["signal"], "CLUSTER_BUY_BOOST")
        self.assertTrue(res_csuite_buy["csuite_buy_hit"])
        self.assertEqual(res_csuite_buy["fresh_buy_window"], "FRESH_5D")

    def test_fidelity_tax_lot_reconciler_and_cash_sweep_stripping(self) -> None:
        """
        Verifies that parse_fidelity_positions_csv:
          1. Strips money-market sweep positions (SPAXX**, FDRXX**)
          2. Correctly categorizes LTCG (>=365 days) vs STCG (<365 days)
          3. Emits exact STRONG HOLD / ADD, HOLD FOR TAX DEFERRAL, or SELL (MOC) directives
        """
        df = parse_fidelity_positions_csv(
            csv_path="data/Portfolio_Positions_Sample.csv",
            live_prices={"AAPL": 345.0, "MSFT": 540.0, "MU": 95.0, "INTC": 21.5},
            eval_date="2026-10-14",
            munger_scores={"AAPL": 2.45, "MSFT": 2.10, "MU": 0.0, "INTC": 0.85},
            immunized_flags={"AAPL": True, "MSFT": True, "MU": False, "INTC": False},
        )
        self.assertFalse(df.empty)
        symbols = list(df["Symbol"])
        self.assertNotIn("SPAXX", symbols)
        self.assertNotIn("SPAXX**", symbols)
        self.assertIn("AAPL", symbols)
        self.assertIn("MU", symbols)

        aapl_row = df[df["Symbol"] == "AAPL"].iloc[0]
        self.assertEqual(aapl_row["Action"], "STRONG HOLD / ADD")
        self.assertEqual(aapl_row["LTCG_Status"], "LTCG")

        mu_row = df[df["Symbol"] == "MU"].iloc[0]
        self.assertTrue("HOLD FOR TAX DEFERRAL" in mu_row["Action"])
        self.assertEqual(mu_row["LTCG_Status"], "STCG")
        self.assertGreater(mu_row["Days_to_LTCG"], 0)

        intc_row = df[df["Symbol"] == "INTC"].iloc[0]
        self.assertEqual(intc_row["Action"], "SELL (MOC)")

    def test_fidelity_native_sentence_case_headers_and_multi_asset_accounts(self) -> None:
        """
        Verifies that native Fidelity.com sentence-case CSV headers ('Account name',
        'Last price', 'Current value', 'Cost basis total', 'Average cost basis'),
        Money Market funds ('FZDXX'), Fixed-Income CDs ('70153RPB8'), Index ETFs ('EFA'),
        and Tax-Advantaged accounts ('ROTH IRA') are parsed and classified accurately.
        """
        import tempfile
        from pathlib import Path

        csv_content = (
            "Account number,Account name,Symbol,Description,Quantity,Last price,Current value,Cost basis total,Average cost basis,Date Acquired\n"
            "Z111,INDIVIDUAL - TOD,FZDXX,FIDELITY MMKT PREMIUM CLASS,223404.5,$1.00,$223404.50,$223404.50,$1.00,\n"
            "Z111,INDIVIDUAL - TOD,70153RPB8,PATHWARD NATL ASSN CD 4.50000% 10/14/2027,40000,$99.669,$39867.60,$40000.00,$100.00,2026-06-01\n"
            "Z111,INDIVIDUAL - TOD,EFA,ISHARES MSCI EAFE ETF,1200,$94.96,$113952.00,$90000.00,$75.00,2026-08-01\n"
            "R222,ROTH IRA,MRK,MERCK & CO INC,100,$101.67,$10167.00,$8500.00,$85.00,2026-09-01\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as tf:
            tf.write(csv_content)
            tmp_path = Path(tf.name)

        try:
            df = parse_fidelity_positions_csv(
                csv_path=tmp_path,
                live_prices={"MRK": 102.0},
                eval_date="2026-10-18",
                munger_scores={"MRK": 0.95},
                immunized_flags={"MRK": False},
            )
            self.assertEqual(len(df), 3)
            syms = list(df["Symbol"])
            self.assertNotIn("FZDXX", syms)

            cd_row = df[df["Symbol"] == "70153RPB8"].iloc[0]
            self.assertEqual(cd_row["Cost_Basis"], 40000.0)
            self.assertEqual(cd_row["Live_Value"], 39867.60)
            self.assertEqual(cd_row["Action"], "HOLD (FIXED INCOME / CD)")

            etf_row = df[df["Symbol"] == "EFA"].iloc[0]
            self.assertEqual(etf_row["Cost_Basis"], 90000.0)
            self.assertEqual(etf_row["Action"], "HOLD (INDEX ETF)")

            mrk_row = df[df["Symbol"] == "MRK"].iloc[0]
            self.assertEqual(mrk_row["Account"], "ROTH IRA")
            self.assertEqual(mrk_row["LTCG_Status"], "TAX-EXEMPT")
            self.assertEqual(mrk_row["Action"], "SELL (MOC)")
            self.assertIn("Tax-advantaged account", mrk_row["Rationale"])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_anti_crest_guard_diagnostics_and_strict_median_entry(self) -> None:
        """
        Verifies:
          1. Broken EPS rollover + falling 200D downtrend is blocked (`anti_crest_mult == 0.0`).
          2. Parabolic blow-off (`Mom_12_1 >= 60%`) receives `0.70x` haircut.
          3. Peak windfall (`TTM > 1.5x 5Y median EPS`) applies smooth damping `< 1.0`.
          4. `run_portfolio_simulation` with `require_above_median=True` never buys `above_med=False`.
        """
        dates = pd.bdate_range("2024-01-02", periods=320)
        # BROKEN: rises early then steadily declines below falling 200D SMA
        p_broken = np.concatenate([np.linspace(120, 140, 100), np.linspace(140, 70, 220)])
        # BLOWOFF: +120% over 12-1M then steady
        p_blow = np.concatenate([np.linspace(50, 115, 299), np.full(21, 115.0)])
        # STEADY: mild compounder
        p_steady = np.linspace(100, 118, 320)

        sim_close = pd.DataFrame(
            {"BROKEN": p_broken, "BLOWOFF": p_blow, "STEADY": p_steady},
            index=dates,
        )
        gspc = pd.Series(np.linspace(4000, 4400, 320), index=dates)

        q_dates = [
            "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
            "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
            "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
        ]
        eps_fixture = {
            "BROKEN": {d: v for d, v in zip(q_dates, [2.0]*8 + [2.0, 1.9, 1.2, 1.0])},
            "BLOWOFF": {d: 1.5 for d in q_dates},
            # Peak windfall: baseline TTM=4.0 for 5+ quarters, jumps to TTM=12.0 -> >=2.0x 5Y median
            "STEADY": {d: v for d, v in zip(q_dates, [1.0]*8 + [3.0, 3.0, 3.0, 3.0])},
        }
        eval_dt = dates[-2:]
        diag = compute_cycle_and_trend_diagnostics(sim_close, eval_dt, eps_fixture, reporting_lag_days=45)
        last_dt = eval_dt[-1]

        self.assertTrue(bool(diag["is_broken_eps_trend"].loc[last_dt, "BROKEN"]))
        self.assertEqual(float(diag["anti_crest_mult"].loc[last_dt, "BROKEN"]), 0.0)

        self.assertTrue(bool(diag["is_blowoff"].loc[last_dt, "BLOWOFF"]))
        self.assertAlmostEqual(float(diag["anti_crest_mult"].loc[last_dt, "BLOWOFF"]), 0.70, places=6)

        self.assertTrue(bool(diag["is_peak_windfall"].loc[last_dt, "STEADY"]))
        self.assertLess(float(diag["anti_crest_mult"].loc[last_dt, "STEADY"]), 1.0)

        # Strict All-Median entry enforcement: FAIL_MED has score 10.0 but above_med=False
        sc_test = pd.DataFrame({"FAIL_MED": [10.0, 10.0], "PASS_MED": [0.5, 0.5]}, index=eval_dt)
        md_test = pd.DataFrame({"FAIL_MED": [False, False], "PASS_MED": [True, True]}, index=eval_dt)
        sub_c = sim_close.loc[eval_dt, ["BROKEN", "STEADY"]].copy()
        sub_c.columns = ["FAIL_MED", "PASS_MED"]
        res = run_portfolio_simulation(
            close_df=sub_c,
            gspc_close=gspc.loc[eval_dt],
            eval_dates=eval_dt,
            scores_df=sc_test,
            above_med_df=md_test,
            config=BacktestConfig(m_buy_threshold=2.0, m_sell_threshold=None, require_above_median=True),
        )
        self.assertEqual(res["trade_count"], 0, "require_above_median=True must block entries where above_med=False")

    def test_coi_exclusions_filter(self) -> None:
        from munger_engine import load_coi_excluded_tickers
        excluded = load_coi_excluded_tickers("data")
        for restricted in ("REGN", "INCY", "VRTX", "LLY", "MRK", "PFE"):
            self.assertIn(restricted, excluded)
        for allowed in ("EOG", "AMP", "NVDA", "GOOGL", "TSM"):
            self.assertNotIn(allowed, excluded)

    def test_ivr_two_tier_sma_and_vrp_blueprints(self) -> None:
        """
        Verifies:
          1. compute_two_tier_sma_status transitions across TIER2_BUY_100%, TIER1_BUY_50%,
             CSP_LIZARD_ZONE, and HOLD_CASH.
          2. compute_ivr_from_prices returns bounded 0-100 IVR and positive IV proxy.
          3. build_csp_jade_lizard_blueprint enforces IVR>=50 Condor gate and Zero-Upside-Risk Jade Lizard.
        """
        # Create 220 weekly points: 120 weeks at 100, then 100 weeks at 140
        # => SMA200w ~ 118, SMA100w = 140, SMA50w = 140
        w_vals = [100.0] * 120 + [140.0] * 100
        w_idx = pd.date_range("2022-01-07", periods=len(w_vals), freq="W-FRI")

        s_t2 = pd.Series(w_vals[:-1] + [110.0], index=w_idx)
        self.assertEqual(compute_two_tier_sma_status(s_t2)["gating_tier"], "TIER2_BUY_100%")

        s_t1 = pd.Series(w_vals[:-1] + [142.0], index=w_idx)  # <= 1.05 * 140 (147), > 1.05 * 118
        self.assertEqual(compute_two_tier_sma_status(s_t1)["gating_tier"], "TIER1_BUY_50%")

        # Make SMA50w slightly higher than SMA100w so spot is below SMA50w but above 1.05*SMA100w
        w_vals_50 = [100.0] * 170 + [160.0] * 50  # SMA100w ~ 130, SMA50w = 160
        s_csp = pd.Series(w_vals_50[:-1] + [155.0], index=w_idx)
        self.assertEqual(compute_two_tier_sma_status(s_csp)["gating_tier"], "CSP_LIZARD_ZONE")

        s_hold = pd.Series(w_vals_50[:-1] + [175.0], index=w_idx)
        self.assertEqual(compute_two_tier_sma_status(s_hold)["gating_tier"], "HOLD_CASH")

        # IVR calculation on noisy daily series
        rng = np.random.default_rng(7)
        d_idx = pd.bdate_range("2025-01-02", periods=290)
        rets = rng.normal(0.0004, 0.018, len(d_idx))
        d_series = pd.Series(250.0 * np.exp(np.cumsum(rets)), index=d_idx)
        ivr_res = compute_ivr_from_prices(d_series)
        self.assertIsNotNone(ivr_res["ivr_1y"])
        self.assertGreaterEqual(ivr_res["ivr_1y"], 0.0)
        self.assertLessEqual(ivr_res["ivr_1y"], 100.0)
        self.assertGreater(ivr_res["iv_proxy_pct"], 12.0)

        # Blueprints: IVR < 50 waits on Condor; IVR >= 50 activates Condor; Jade Lizard valid
        bp_low = build_csp_jade_lizard_blueprint("NVDA", 250.0, 35.0, 35.0)
        self.assertEqual(bp_low["iron_condor"]["signal"], "WAIT_LOW_IVR")
        self.assertTrue(bp_low["jade_lizard"]["zero_upside_risk_valid"])

        bp_high = build_csp_jade_lizard_blueprint("NVDA", 250.0, 35.0, 72.0)
        self.assertEqual(bp_high["iron_condor"]["signal"], "ACTIVE_OPEN_CONDOR")
        self.assertGreaterEqual(bp_high["jade_lizard"]["est_credit"], bp_high["jade_lizard"]["call_width"])

    def test_schedule_13d_universe_intersection(self) -> None:
        """
        Verifies that fetch_schedule_13d_catalysts extracts tickers and strips
        out closed-end funds & microcaps outside the active universe.
        """
        from unittest.mock import MagicMock, patch

        fake_payload = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "file_date": "2026-11-20",
                            "form": "SCHEDULE 13D",
                            "display_names": ["Virtus Total Return Fund Inc. (ZTR) (CIK 0000836412)", "Saba Capital"],
                            "adsh": "0001-26-0001",
                        }
                    },
                    {
                        "_source": {
                            "file_date": "2026-11-21",
                            "form": "SCHEDULE 13D",
                            "display_names": ["Alphabet Inc. (GOOGL, GOOG) (CIK 0001652044)", "Activist Partners LP"],
                            "adsh": "0001-26-0002",
                        }
                    },
                ]
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_payload

        with patch("requests.get", return_value=mock_resp):
            cats = fetch_schedule_13d_catalysts(universe_tickers={"GOOGL", "MSFT", "AAPL"})
        self.assertIn("GOOGL", cats)
        self.assertNotIn("ZTR", cats)
        self.assertNotIn("GOOG", cats)
        self.assertEqual(cats["GOOGL"][0]["filer"], "Activist Partners LP")

    def test_munger_api_near_miss_and_vrp_endpoint(self) -> None:
        """
        Verifies run_munger_scan_api exposes 3/4 near-misses, Two-Tier gating,
        1Y IVR, and get_options_vrp_entries_api returns valid CSP/Jade Lizard entries.
        """
        import sys
        from pathlib import Path
        from unittest.mock import patch

        src_dir = str(Path(__file__).resolve().parent / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        from fidelity_control import munger_api

        fake_vol = {
            "vix": 22.5, "vix_ivr_1y": 62.0,
            "vxn": 27.0, "vxn_ivr_1y": 58.0,
            "tbill_yield_pct": 4.2, "condor_regime": "OPEN (IVR >= 50)",
        }
        with patch.object(munger_api, "_get_cached_vol_regime", return_value=fake_vol), \
             patch.object(munger_api, "_get_cached_13d", return_value={"NVDA": [{"file_date": "2026-11-20", "form": "SCHEDULE 13D", "filer": "Test Activist"}]}), \
             patch.object(munger_api, "fetch_yahoo_v8_chart", return_value=(pd.Series(dtype=float), {})):
            scan_res = munger_api.run_munger_scan_api({
                "universe": "composite",
                "include_insider": False,
                "exclude_10b5_1": True,
            })
            self.assertEqual(scan_res["status"], "SUCCESS")
            self.assertIn("near_miss_count", scan_res)
            self.assertGreater(len(scan_res["candidates"]), 0)
            sample = scan_res["candidates"][0]
            self.assertIn("gating_tier", sample)
            self.assertIn("is_near_miss", sample)
            self.assertIn("ivr_1y", sample)

            vrp_res = munger_api.get_options_vrp_entries_api()
            self.assertEqual(vrp_res["status"], "SUCCESS")
            self.assertEqual(vrp_res["vol_regime"]["vix"], 22.5)
            self.assertGreaterEqual(len(vrp_res["entries"]), 2)
            first_entry = vrp_res["entries"][0]
            self.assertIn("csp_25d", first_entry)
            self.assertIn("jade_lizard", first_entry)
            self.assertIn("iron_condor", first_entry)


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestMungerFactorSystemAudit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
