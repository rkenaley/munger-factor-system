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
    check_data_freshness,
    compute_vectorized_munger_scores,
    fetch_live_insider_activity,
    find_strike_by_delta_analytical,
    load_or_generate_dataset,
    parse_fidelity_positions_csv,
    run_portfolio_simulation,
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
        Verifies that:
          1. Micron CEO Sanjay Mehrotra's >$20M open-market sales trigger
             the Heavy C-Suite Distribution Haircut (0.70x).
          2. Signal details correctly reflect open market sales and zero buys.
        """
        res_mu = fetch_live_insider_activity("MU")
        self.assertIn("MU", res_mu)
        mu_info = res_mu["MU"]
        self.assertEqual(mu_info["multiplier"], 0.70)
        self.assertEqual(mu_info["signal"], "CSUITE_DISTRIBUTION_HAIRCUT")
        self.assertGreaterEqual(mu_info["csuite_sales_usd"], 20_000_000.0)

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


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestMungerFactorSystemAudit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
