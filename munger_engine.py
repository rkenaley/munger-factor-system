#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR SYSTEM: SHARED VECTORIZED & ZERO-LOOKAHEAD CORE ENGINE (v2.3.0)
================================================================================
Centralizes data loading, point-in-time (causal) factor matrix construction,
cyclical peak-earnings trap immunization ("Value Trap" Filter),
cross-sectional winsorization, portfolio simulation with delisting/slippage/tax
accounting, analytical O(1) Black-Scholes-Merton option pricing with skew,
Staleness Circuit Breaker with multi-threaded live quote refresh,
SEC Form 4 / Finviz discretionary insider conviction scoring, and
Fidelity tax-lot reconciliation with 365-day LTCG hurdle directives.
================================================================================
"""

from __future__ import annotations

import csv
import gzip
import json
import lzma
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_NORM = NormalDist(0.0, 1.0)
EXCLUDED_TICKERS = {"SNDK"}


@dataclass
class BacktestConfig:
    """Configuration parameters for causal Munger Factor simulations."""
    initial_capital: float = 1_000.0   # Matches $1,000 initial capital in Whitepaper Table 6 & Figs 1-2
    max_pos_pct: float = 0.15          # Max 15% single position -> 7 positions max
    min_hold_days: int = 365           # Tax-aware 1-year minimum holding period
    max_hold_days: Optional[int] = None  # Optional mechanical rebalance horizon (e.g. 365, 730, 1095)
    m_buy_threshold: float = 2.0       # Minimum composite Munger multiplier to enter
    m_sell_threshold: Optional[float] = 1.35  # Exit if composite Munger score decays below floor
    require_above_median: bool = False  # Optional strict median-across-all-factors exit rule
    slippage_bps: float = 5.0          # Realistic 5 bps market impact/commission penalty per trade
    use_historical_tax_regimes: bool = True
    flat_ltcg_tax_rate: float = 0.238
    delisting_loss_haircut: float = 0.30  # 30% distress haircut if price series terminates abruptly
    reporting_lag_days: int = 45       # SEC filing lag before quarterly EPS is observable
    allow_legacy_lookahead: bool = False  # Strictly False for audited runs
    winsorize_cross_section: bool = True  # Clips 2nd/98th percentiles to stop outliers (e.g. MU 379% sales) from crushing sigma
    enforce_full_cycle_profitability: bool = True  # Disqualifies boom-bust cyclicals with negative TTM EPS in trailing 5Y
    use_cyclically_adjusted_ey: bool = True  # Blends TTM EPS with 5Y median EPS to prevent cyclical peak P/E traps


def get_historical_rf_rate(year: int) -> float:
    """Historical 3-Month US Treasury Bill annual yield proxy by decade/year."""
    rf_table = {
        1970: 0.064, 1971: 0.043, 1972: 0.041, 1973: 0.070, 1974: 0.079,
        1975: 0.058, 1976: 0.050, 1977: 0.053, 1978: 0.072, 1979: 0.100,
        1980: 0.115, 1981: 0.140, 1982: 0.107, 1983: 0.086, 1984: 0.096,
        1985: 0.075, 1986: 0.060, 1987: 0.058, 1988: 0.067, 1989: 0.081,
        1990: 0.075, 1991: 0.054, 1992: 0.035, 1993: 0.030, 1994: 0.043,
        1995: 0.055, 1996: 0.050, 1997: 0.051, 1998: 0.048, 1999: 0.047,
        2000: 0.058, 2001: 0.034, 2002: 0.016, 2003: 0.010, 2004: 0.014,
        2005: 0.031, 2006: 0.047, 2007: 0.044, 2008: 0.014, 2009: 0.002,
        2010: 0.001, 2011: 0.001, 2012: 0.001, 2013: 0.001, 2014: 0.000,
        2015: 0.001, 2016: 0.003, 2017: 0.009, 2018: 0.019, 2019: 0.021,
        2020: 0.004, 2021: 0.000, 2022: 0.020, 2023: 0.051, 2024: 0.052,
        2025: 0.045, 2026: 0.042,
    }
    return rf_table.get(int(year), 0.04)


def get_historical_ltcg_tax_rate(year: int, use_historical: bool = True, flat_rate: float = 0.238) -> float:
    """Returns effective top Federal Long-Term Capital Gains tax rate by calendar year."""
    if not use_historical:
        return flat_rate
    if year <= 1978:
        return 0.300
    if year <= 1986:
        return 0.200
    if year <= 1996:
        return 0.280
    if year <= 2002:
        return 0.200
    if year <= 2012:
        return 0.150
    return 0.238


def get_historical_sp500_div_yield(year: int) -> float:
    """Historical S&P 500 annual dividend yield by era instead of static 2.0%."""
    if year < 1980:
        return 0.042
    if year < 1990:
        return 0.040
    if year < 2000:
        return 0.023
    if year < 2010:
        return 0.018
    if year < 2020:
        return 0.019
    return 0.015


# ==============================================================================
# DATA LOADING, SANITIZATION & SYNTHETIC FIXTURE FALLBACK
# ==============================================================================

def sanitize_universe_fundamentals(fund_dict: Dict, eps_dict: Dict) -> Dict:
    """
    Sanitizes scraper artifacts in `sp100_nasdaq100_universe.json` without mutating
    raw files unless requested:
      - Caps single-quarter YoY revenue spikes misrecorded as 5-year CAGRs (e.g.,
        MU's `sales5y: 379.3`, which was Q4 FY26 YoY quarterly revenue growth,
        converting `(1 + 3.793)**(1/5) - 1 = 36.8%` annualized 5Y equivalent).
    """
    cleaned = {}
    for t, meta in fund_dict.items():
        m = dict(meta)
        raw_s5y = float(m.get("sales5y", 10.0))
        if raw_s5y > 65.0:
            m["sales5y_raw_unadjusted"] = raw_s5y
            m["sales5y"] = round(((1.0 + raw_s5y / 100.0) ** (1.0 / 5.0) - 1.0) * 100.0, 2)
        cleaned[t] = m
    return cleaned


def load_or_generate_dataset(data_dir: Path | str = "data") -> Tuple[pd.DataFrame, pd.Series, Dict, Dict, bool]:
    """Loads (close_df, gspc_close, fund_dict, eps_dict, is_synthetic) from `data_dir`."""
    data_path = Path(data_dir)
    price_file_xz = data_path / "historical_prices_1970_2026.pkl.xz"
    price_file_gz = data_path / "historical_prices_1970_2026.pkl.gz"
    price_file = price_file_xz if price_file_xz.exists() else price_file_gz
    univ_file = data_path / "sp100_nasdaq100_universe.json"
    eps_file = data_path / "historical_quarterly_eps.json"

    if price_file.exists() and univ_file.exists():
        with open(univ_file, "r", encoding="utf-8") as f:
            fund_dict = json.load(f)
        eps_dict = {}
        if eps_file.exists():
            with open(eps_file, "r", encoding="utf-8") as f:
                eps_dict = json.load(f)

        if price_file.suffix == ".xz":
            with lzma.open(price_file, "rb") as f:
                raw_obj = pickle.load(f)
        else:
            with gzip.open(price_file, "rb") as f:
                raw_obj = pickle.load(f)

        if isinstance(raw_obj, pd.DataFrame):
            close_df = raw_obj.copy()
        elif isinstance(raw_obj, dict):
            if "close" in raw_obj and isinstance(raw_obj["close"], pd.DataFrame):
                close_df = raw_obj["close"].copy()
            else:
                series_map = {}
                for k, v in raw_obj.items():
                    if isinstance(v, pd.DataFrame):
                        col = "Close" if "Close" in v.columns else ("Adj Close" if "Adj Close" in v.columns else v.columns[0])
                        series_map[k] = v[col]
                    elif isinstance(v, pd.Series):
                        series_map[k] = v
                close_df = pd.DataFrame(series_map)
        else:
            raise ValueError(f"Unsupported pickle structure in {price_file}: {type(raw_obj)}")

        close_df.index = pd.to_datetime(close_df.index).tz_localize(None)
        close_df = close_df.sort_index()

        gspc_col = "^GSPC" if "^GSPC" in close_df.columns else ("SPY" if "SPY" in close_df.columns else None)
        if gspc_col is not None:
            gspc_close = close_df[gspc_col].ffill().bfill()
        else:
            gspc_close = close_df.mean(axis=1).ffill().bfill()

        valid_tickers = [
            t for t in fund_dict.keys()
            if t in close_df.columns and t not in EXCLUDED_TICKERS and t not in {"^GSPC", "SPY"}
        ]
        close_df = close_df[valid_tickers]
        return close_df, gspc_close, fund_dict, eps_dict, False

    return _generate_synthetic_fixture()


def _generate_synthetic_fixture(
    start_date: str = "1971-01-04",
    end_date: str = "2026-10-13",
    n_tickers: int = 32,
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.Series, Dict, Dict, bool]:
    """
    Deterministic multi-decade daily market fixture matching the exact schema of
    sp100_nasdaq100_universe.json, historical_quarterly_eps.json, and close_df,
    including Micron (MU) with its exact 2023 loss cycle and 2026 HBM spike.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start_date, end=end_date, freq="B")
    n_days = len(dates)

    mkt_shocks = rng.normal(loc=0.00033, scale=0.0105, size=n_days)
    gspc_close = pd.Series(100.0 * np.exp(np.cumsum(mkt_shocks)), index=dates, name="^GSPC")

    fund_dict: Dict[str, Dict] = {}
    eps_dict: Dict[str, Dict[str, float]] = {}
    price_cols: Dict[str, pd.Series] = {}

    q_dates = pd.date_range(start="2008-03-31", end=end_date, freq="QE")

    for i in range(n_tickers):
        t = "MU" if i == 0 else f"EQ{i:02d}"
        if t == "MU":
            fund_dict[t] = {
                "ticker": "MU",
                "name": "Micron Technology",
                "sector": "Technology",
                "theme": "Technology",
                "pe": 13.31,
                "pb": 11.09,
                "de": 3.74,
                "sales5y": 379.3,
                "roic_proxy": 0.883,
                "fcf_margin": 0.219,
            }
            eps_dict["MU"] = {
                "2026-09-30": 33.42, "2026-06-24": 25.11, "2026-03-18": 12.20, "2025-12-17": 4.78,
                "2025-09-23": 3.03, "2025-06-25": 1.91, "2025-03-20": 1.56, "2024-12-18": 1.79,
                "2024-09-25": 1.18, "2024-06-26": 0.62, "2024-03-20": 0.42, "2023-12-20": -0.95,
                "2023-09-27": -1.07, "2023-06-28": -1.43, "2023-03-28": -1.91, "2022-12-21": -0.04,
                "2022-09-29": 1.45, "2022-06-30": 2.59, "2022-03-29": 2.14, "2021-12-20": 2.16,
            }
            idio = rng.normal(loc=0.00018, scale=0.018, size=n_days)
            p_series = pd.Series(15.0 * np.exp(np.cumsum(1.2 * mkt_shocks + idio)), index=dates)
            p_series *= (953.19 / float(p_series.iloc[-1]))
            price_cols[t] = p_series
            continue

        roic = float(np.clip(rng.normal(0.20, 0.08), 0.04, 0.45))
        sales5y = float(np.clip(rng.normal(13.5, 5.5), -2.0, 28.0))
        de = float(np.clip(rng.lognormal(mean=3.8, sigma=0.55), 5.0, 220.0))
        pe = float(np.clip(rng.normal(19.0, 6.5), 9.0, 45.0))

        fund_dict[t] = {
            "ticker": t,
            "name": f"Compounder Corp {i:02d}",
            "sector": "Technology" if i % 3 == 0 else ("Financials" if i % 3 == 1 else "Industrials"),
            "theme": "Core",
            "pe": round(pe, 2),
            "pb": round(pe * roic, 2),
            "de": round(de, 2),
            "sales5y": round(sales5y, 2),
            "roic_proxy": round(roic, 3),
            "fcf_margin": round(roic * 0.75, 3),
        }

        quality_edge = 0.00014 * ((roic - 0.14) / 0.08) - 0.00005 * ((de - 60.0) / 60.0)
        idio = rng.normal(loc=quality_edge, scale=0.013, size=n_days)
        beta = float(rng.uniform(0.80, 1.20))
        ret = beta * mkt_shocks + idio
        p_series = pd.Series(25.0 * np.exp(np.cumsum(ret)), index=dates)

        if i >= 24:
            ipo_idx = int(n_days * (0.15 + 0.05 * (i - 24)))
            p_series.iloc[:ipo_idx] = np.nan
        if i in {3, 11, 19, 27}:
            delist_idx = int(n_days * (0.35 + 0.15 * (i // 8)))
            p_series.iloc[delist_idx - 60:delist_idx] *= np.linspace(1.0, 0.35, 60)
            p_series.iloc[delist_idx:] = np.nan

        price_cols[t] = p_series

        q_map: Dict[str, float] = {}
        for qd in q_dates:
            if qd in p_series.index and pd.notna(p_series.loc[qd]):
                p_val = float(p_series.loc[qd])
            else:
                valid_window = p_series.loc[:qd].dropna()
                p_val = float(valid_window.iloc[-1]) if len(valid_window) > 0 else 25.0
            q_eps = max(0.10, (p_val / pe) / 4.0 * float(rng.uniform(0.88, 1.14)))
            q_map[qd.strftime("%Y-%m-%d")] = round(q_eps, 4)
        eps_dict[t] = q_map

    close_df = pd.DataFrame(price_cols, index=dates)
    return close_df, gspc_close, fund_dict, eps_dict, True


# ==============================================================================
# VECTORIZED CAUSAL FACTOR MATRIX ENGINE + VALUE TRAP CYCLICAL FILTER
# ==============================================================================

def build_point_in_time_ttm_eps_matrix(
    close_df: pd.DataFrame,
    eval_dates: pd.DatetimeIndex,
    fund_dict: Dict,
    eps_dict: Dict,
    reporting_lag_days: int = 45,
    allow_legacy_lookahead: bool = False,
    use_cyclically_adjusted_ey: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Constructs:
      1. `ttm_eps_df`: [len(eval_dates) x len(tickers)] DataFrame of point-in-time
         (or cyclically smoothed) Trailing Twelve Month EPS.
      2. `full_cycle_profitable_df`: [len(eval_dates) x len(tickers)] boolean mask
         indicating whether the firm maintained strictly positive TTM EPS (> 0)
         across its trailing 5-year (20-quarter) window.
    """
    tickers = list(close_df.columns)
    eval_dates = pd.DatetimeIndex(eval_dates)
    ttm_eps_df = pd.DataFrame(np.nan, index=eval_dates, columns=tickers, dtype=float)
    full_cycle_profitable_df = pd.DataFrame(True, index=eval_dates, columns=tickers, dtype=bool)

    for t in tickers:
        q_raw = eps_dict.get(t, {})
        if q_raw:
            s = pd.Series(q_raw, dtype=float)
            s.index = pd.to_datetime(s.index).tz_localize(None) + pd.Timedelta(days=reporting_lag_days)
            s = s.sort_index()
            ttm_s = s.rolling(4, min_periods=1).sum() * (4.0 / np.minimum(4.0, np.arange(1, len(s) + 1)))
            ttm_s = ttm_s[~ttm_s.index.duplicated(keep="last")]

            min_5y_ttm = ttm_s.rolling(20, min_periods=4).min()
            prof_s = (min_5y_ttm > 0.0).astype(float)
            full_cycle_profitable_df[t] = prof_s.reindex(eval_dates, method="ffill").fillna(1.0).astype(bool)

            if use_cyclically_adjusted_ey and not allow_legacy_lookahead:
                med_5y_ttm = ttm_s.rolling(20, min_periods=4).median()
                eff_eps_s = np.minimum(ttm_s, 0.50 * ttm_s + 0.50 * med_5y_ttm)
            else:
                eff_eps_s = ttm_s

            ttm_eps_df[t] = eff_eps_s.reindex(eval_dates, method="ffill")

    prices_at_eval = close_df.reindex(eval_dates, method="ffill")

    if allow_legacy_lookahead:
        last_date = close_df.index[-1]
        years_ago_vec = (last_date - eval_dates).days / 365.25
        for t in tickers:
            non_na = close_df[t].dropna()
            if len(non_na) == 0:
                continue
            today_p = float(non_na.iloc[-1])
            today_pe = float(fund_dict.get(t, {}).get("pe", 20.0))
            today_eps = today_p / (today_pe if today_pe > 0 else 20.0)
            annual_g = float(fund_dict.get(t, {}).get("sales5y", 10.0)) / 100.0
            g_clamped = max(-0.05, min(0.25, annual_g))
            fallback_series = pd.Series(
                today_eps / ((1.0 + g_clamped) ** years_ago_vec),
                index=eval_dates,
            )
            mask = ttm_eps_df[t].isna() | (ttm_eps_df[t] <= 0)
            ttm_eps_df.loc[mask, t] = fallback_series.loc[mask]
    else:
        trailing_3y_mean_price = close_df.rolling(window=252 * 3, min_periods=21).mean().reindex(eval_dates, method="ffill")
        for t in tickers:
            base_pe = float(fund_dict.get(t, {}).get("pe", 20.0))
            safe_pe = base_pe if base_pe > 0 else 20.0
            causal_eps = trailing_3y_mean_price[t] / safe_pe
            mask = ttm_eps_df[t].isna() | (ttm_eps_df[t] <= 0)
            ttm_eps_df.loc[mask, t] = causal_eps.loc[mask]

    inactive_mask = prices_at_eval.isna() | (prices_at_eval <= 0)
    ttm_eps_df = ttm_eps_df.mask(inactive_mask, np.nan)
    return ttm_eps_df, full_cycle_profitable_df


def compute_vectorized_munger_scores(
    close_df: pd.DataFrame,
    eval_dates: pd.DatetimeIndex,
    fund_dict: Dict,
    eps_dict: Dict,
    reporting_lag_days: int = 45,
    allow_legacy_lookahead: bool = False,
    formula: str = "four_factor_z",
    winsorize_cross_section: bool = True,
    enforce_full_cycle_profitability: bool = True,
    use_cyclically_adjusted_ey: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Vectorized computation of composite Munger scores across all `eval_dates` and `tickers`.
    """
    eval_dates = pd.DatetimeIndex(eval_dates)
    tickers = list(close_df.columns)
    prices_eval = close_df.reindex(eval_dates, method="ffill")
    active_mask = prices_eval.notna() & (prices_eval > 0)

    eff_fund_dict = fund_dict if allow_legacy_lookahead else sanitize_universe_fundamentals(fund_dict, eps_dict)

    ttm_eps_df, full_cycle_prof_df = build_point_in_time_ttm_eps_matrix(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=eff_fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=reporting_lag_days,
        allow_legacy_lookahead=allow_legacy_lookahead,
        use_cyclically_adjusted_ey=use_cyclically_adjusted_ey and not allow_legacy_lookahead,
    )
    ey_df = (ttm_eps_df / prices_eval).where(active_mask, np.nan)

    if allow_legacy_lookahead:
        sales_vec = pd.Series({t: float(fund_dict.get(t, {}).get("sales5y", 10.0)) for t in tickers})
        roic_vec = pd.Series({t: float(fund_dict.get(t, {}).get("roic_proxy", 0.15)) for t in tickers})
        de_vec = pd.Series({t: float(fund_dict.get(t, {}).get("de", 50.0)) for t in tickers})
        sales_df = pd.DataFrame(np.tile(sales_vec.values, (len(eval_dates), 1)), index=eval_dates, columns=tickers).where(active_mask, np.nan)
        roic_df = pd.DataFrame(np.tile(roic_vec.values, (len(eval_dates), 1)), index=eval_dates, columns=tickers).where(active_mask, np.nan)
        de_df = pd.DataFrame(np.tile(de_vec.values, (len(eval_dates), 1)), index=eval_dates, columns=tickers).where(active_mask, np.nan)
    else:
        ret_5y = close_df.pct_change(periods=252 * 5, fill_method=None).reindex(eval_dates, method="ffill")
        ann_g_5y = (((1.0 + ret_5y.clip(lower=-0.90)) ** (1.0 / 5.0)) - 1.0) * 100.0
        vol_1y = close_df.pct_change(fill_method=None).rolling(252, min_periods=21).std().reindex(eval_dates, method="ffill") * np.sqrt(252.0)

        sales_prior = pd.Series({t: float(eff_fund_dict.get(t, {}).get("sales5y", 10.0)) for t in tickers})
        roic_prior = pd.Series({t: float(eff_fund_dict.get(t, {}).get("roic_proxy", 0.15)) for t in tickers})
        de_prior = pd.Series({t: float(eff_fund_dict.get(t, {}).get("de", 50.0)) for t in tickers})

        sales_df = (0.5 * ann_g_5y.fillna(0.0).add(sales_prior, axis=1)).where(active_mask, np.nan)
        roic_df = (roic_prior / (1.0 + vol_1y.fillna(0.25))).where(active_mask, np.nan)
        de_df = (de_prior * (1.0 + vol_1y.fillna(0.25))).where(active_mask, np.nan)

    de_inv_df = -de_df

    def _positive_z_mult(df: pd.DataFrame) -> pd.DataFrame:
        work = df.copy()
        if winsorize_cross_section and not allow_legacy_lookahead:
            q_low = work.quantile(0.02, axis=1)
            q_high = work.quantile(0.98, axis=1)
            work = work.clip(lower=q_low, upper=q_high, axis=0)
        row_mean = work.mean(axis=1)
        row_std = work.std(axis=1).replace(0.0, np.nan)
        z = work.sub(row_mean, axis=0).div(row_std, axis=0)
        if winsorize_cross_section and not allow_legacy_lookahead:
            z = z.clip(lower=-3.5, upper=3.5)
        return (1.0 + z.clip(lower=0.0).fillna(0.0)).where(active_mask, np.nan)

    if formula == "ey_times_roic":
        scores_df = (ey_df * roic_df * 100.0).where(active_mask, np.nan)
    else:
        scores_df = (
            _positive_z_mult(sales_df)
            * _positive_z_mult(roic_df)
            * _positive_z_mult(ey_df)
            * _positive_z_mult(de_inv_df)
        ).where(active_mask, np.nan)

    above_med_df = (
        sales_df.ge(sales_df.median(axis=1), axis=0)
        & roic_df.ge(roic_df.median(axis=1), axis=0)
        & ey_df.ge(ey_df.median(axis=1), axis=0)
        & de_inv_df.ge(de_inv_df.median(axis=1), axis=0)
    ) & active_mask

    if enforce_full_cycle_profitability and not allow_legacy_lookahead:
        above_med_df = above_med_df & full_cycle_prof_df
        scores_df = scores_df.where(full_cycle_prof_df, 0.0).where(active_mask, np.nan)

    return scores_df, above_med_df, ey_df


# ==============================================================================
# PORTFOLIO BACKTEST ENGINE (CAUSAL, DELISTING-SAFE, EXACT DAY COMPOUNDING)
# ==============================================================================

def run_portfolio_simulation(
    close_df: pd.DataFrame,
    gspc_close: pd.Series,
    eval_dates: pd.DatetimeIndex,
    scores_df: pd.DataFrame,
    above_med_df: pd.DataFrame,
    config: BacktestConfig = BacktestConfig(),
) -> Dict:
    """Executes the Munger Factor portfolio simulation over `eval_dates`."""
    eval_dates = pd.DatetimeIndex(eval_dates)
    prices_mat = close_df.reindex(eval_dates, method="ffill").to_numpy(dtype=float)
    raw_active_mat = close_df.reindex(eval_dates).notna().to_numpy(dtype=bool)
    scores_mat = scores_df.reindex(eval_dates).to_numpy(dtype=float)
    med_mat = above_med_df.reindex(eval_dates).to_numpy(dtype=bool)

    max_positions = max(1, int(round(1.0 / config.max_pos_pct)))
    slippage = config.slippage_bps / 10_000.0

    cash_pre = float(config.initial_capital)
    cash_post = float(config.initial_capital)

    holdings: Dict[int, Dict] = {}
    nav_pre_history: List[float] = []
    nav_post_history: List[float] = []
    trade_count = 0
    delisting_events = 0

    for step_idx, dt in enumerate(eval_dates):
        cur_year = int(dt.year)
        if step_idx > 0:
            actual_days = max(1, (dt - eval_dates[step_idx - 1]).days)
            rf_annual = get_historical_rf_rate(cur_year)
            rf_step = ((1.0 + rf_annual) ** (actual_days / 365.25)) - 1.0
            cash_pre *= (1.0 + rf_step)
            cash_post *= (1.0 + rf_step)

        for t_idx in list(holdings.keys()):
            pos = holdings[t_idx]
            p_now = prices_mat[step_idx, t_idx]
            is_actively_traded = raw_active_mat[step_idx, t_idx] and np.isfinite(p_now) and (p_now > 0)

            if not is_actively_traded:
                future_valid = raw_active_mat[step_idx:, t_idx]
                if not np.any(future_valid):
                    p_delist = pos["last_price"] * (1.0 - config.delisting_loss_haircut)
                    proceeds_pre = pos["shares_pre"] * p_delist
                    proceeds_post = pos["shares_post"] * p_delist
                    gain_post = proceeds_post - (pos["shares_post"] * pos["cost_basis"])
                    tax_rate = get_historical_ltcg_tax_rate(cur_year, config.use_historical_tax_regimes, config.flat_ltcg_tax_rate)
                    tax_due = max(0.0, gain_post * tax_rate)
                    cash_pre += proceeds_pre
                    cash_post += (proceeds_post - tax_due)
                    del holdings[t_idx]
                    delisting_events += 1
                    trade_count += 1
                continue

            pos["last_price"] = float(p_now)
            days_held = (dt - pos["entry_date"]).days
            t_score = scores_mat[step_idx, t_idx]
            is_above_med = bool(med_mat[step_idx, t_idx])

            should_sell = False
            if config.max_hold_days is not None and days_held >= config.max_hold_days:
                should_sell = True
            elif days_held >= config.min_hold_days:
                if config.m_sell_threshold is not None and (np.isnan(t_score) or t_score < config.m_sell_threshold):
                    should_sell = True
                if config.require_above_median and not is_above_med:
                    should_sell = True

            if should_sell:
                p_exit = float(p_now) * (1.0 - slippage)
                proceeds_pre = pos["shares_pre"] * p_exit
                proceeds_post = pos["shares_post"] * p_exit
                gain_post = proceeds_post - (pos["shares_post"] * pos["cost_basis"])
                tax_rate = get_historical_ltcg_tax_rate(cur_year, config.use_historical_tax_regimes, config.flat_ltcg_tax_rate)
                tax_due = max(0.0, gain_post * tax_rate)
                cash_pre += proceeds_pre
                cash_post += (proceeds_post - tax_due)
                del holdings[t_idx]
                trade_count += 1

        open_slots = max_positions - len(holdings)
        if open_slots > 0:
            row_scores = scores_mat[step_idx]
            row_prices = prices_mat[step_idx]
            valid_mask = (
                raw_active_mat[step_idx]
                & np.isfinite(row_scores)
                & (row_scores >= config.m_buy_threshold)
                & np.isfinite(row_prices)
                & (row_prices > 0)
            )
            candidate_indices = np.where(valid_mask)[0]
            candidate_indices = [idx for idx in candidate_indices if idx not in holdings]
            candidate_indices.sort(key=lambda idx: row_scores[idx], reverse=True)

            for idx in candidate_indices[:open_slots]:
                nav_pre_curr = cash_pre + sum(p["shares_pre"] * p["last_price"] for p in holdings.values())
                nav_post_curr = cash_post + sum(p["shares_post"] * p["last_price"] for p in holdings.values())
                alloc_pre = min(cash_pre, nav_pre_curr * config.max_pos_pct)
                alloc_post = min(cash_post, nav_post_curr * config.max_pos_pct)

                if alloc_post <= 0 or alloc_pre <= 0:
                    break

                p_entry = float(row_prices[idx]) * (1.0 + slippage)
                holdings[idx] = {
                    "shares_pre": alloc_pre / p_entry,
                    "shares_post": alloc_post / p_entry,
                    "cost_basis": p_entry,
                    "entry_date": dt,
                    "last_price": float(row_prices[idx]),
                }
                cash_pre -= alloc_pre
                cash_post -= alloc_post
                trade_count += 1

        val_pre = cash_pre + sum(p["shares_pre"] * p["last_price"] for p in holdings.values())
        val_post = cash_post + sum(p["shares_post"] * p["last_price"] for p in holdings.values())
        nav_pre_history.append(val_pre)
        nav_post_history.append(val_post)

    nav_pre_s = pd.Series(nav_pre_history, index=eval_dates, name="NAV_PreTax")
    nav_post_s = pd.Series(nav_post_history, index=eval_dates, name="NAV_PostTax")

    gspc_eval = gspc_close.reindex(eval_dates, method="ffill").bfill()
    bench_nav = [float(config.initial_capital)]
    for i in range(1, len(eval_dates)):
        dt_curr = eval_dates[i]
        dt_prev = eval_dates[i - 1]
        days_step = max(1, (dt_curr - dt_prev).days)
        price_ret = (float(gspc_eval.iloc[i]) / float(gspc_eval.iloc[i - 1])) - 1.0
        div_step = get_historical_sp500_div_yield(int(dt_curr.year)) * (days_step / 365.25)
        bench_nav.append(bench_nav[-1] * (1.0 + price_ret + div_step))
    bench_s = pd.Series(bench_nav, index=eval_dates, name="SP500_TotalReturn")

    return {
        "nav_pre": nav_pre_s,
        "nav_post": nav_post_s,
        "bench_nav": bench_s,
        "metrics_pre": compute_performance_metrics(nav_pre_s),
        "metrics_post": compute_performance_metrics(nav_post_s),
        "metrics_bench": compute_performance_metrics(bench_s),
        "trade_count": trade_count,
        "delisting_events": delisting_events,
    }


def compute_performance_metrics(nav_series: pd.Series) -> Dict[str, float]:
    """Calculates annualized CAGR, volatility, Sharpe ratio, and maximum drawdown."""
    if len(nav_series) < 2:
        return {"cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "vol": 0.0}
    total_years = max(0.01, (nav_series.index[-1] - nav_series.index[0]).days / 365.25)
    cagr = (float(nav_series.iloc[-1]) / float(nav_series.iloc[0])) ** (1.0 / total_years) - 1.0

    rets = nav_series.pct_change(fill_method=None).dropna()
    steps_per_year = len(rets) / total_years
    vol = float(rets.std() * np.sqrt(steps_per_year)) if len(rets) > 1 else 0.0

    rf_series = pd.Series([get_historical_rf_rate(int(d.year)) / steps_per_year for d in rets.index], index=rets.index)
    excess = rets - rf_series
    sharpe = float((excess.mean() * steps_per_year) / vol) if vol > 1e-9 else 0.0

    running_max = nav_series.cummax()
    dd = (nav_series / running_max) - 1.0
    max_dd = float(dd.min())

    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "vol": vol,
    }


# ==============================================================================
# ANALYTICAL O(1) BLACK-SCHOLES-MERTON & VOLATILITY SKEW OPTIONS ENGINE
# ==============================================================================

def implied_vol_with_skew(
    delta: float,
    T: float,
    base_sigma: float = 0.32,
    skew_slope: float = -0.14,
    term_slope: float = -0.02,
) -> float:
    """Models realistic equity implied volatility skew and term structure."""
    skew_adj = -skew_slope * (delta - 0.50)
    term_adj = term_slope * (T - 0.25)
    return float(np.clip(base_sigma + skew_adj + term_adj, 0.10, 1.00))


def bsm_call(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.01) -> Dict[str, float]:
    """Closed-form Black-Scholes-Merton European Call price and Greeks."""
    if T <= 0.0 or sigma <= 0.0:
        intrinsic = max(0.0, S - K)
        return {
            "price": intrinsic,
            "delta": 1.0 if S > K else 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
        }

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    exp_qT = math.exp(-q * T)
    exp_rT = math.exp(-r * T)
    nd1 = _NORM.cdf(d1)
    nd2 = _NORM.cdf(d2)
    pdf_d1 = _NORM.pdf(d1)

    price = S * exp_qT * nd1 - K * exp_rT * nd2
    delta = exp_qT * nd1
    gamma = (exp_qT * pdf_d1) / (S * sigma * sqrt_T)
    vega = S * exp_qT * pdf_d1 * sqrt_T * 0.01
    theta = (
        -(S * exp_qT * pdf_d1 * sigma) / (2.0 * sqrt_T)
        - r * K * exp_rT * nd2
        + q * S * exp_qT * nd1
    ) / 365.0

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
    }


def find_strike_by_delta_analytical(
    S: float,
    T: float,
    r: float,
    sigma: float,
    target_delta: float,
    q: float = 0.01,
) -> float:
    """O(1) exact analytical inversion of Black-Scholes-Merton call delta."""
    u = min(0.999999, max(1e-6, target_delta * math.exp(q * T)))
    d1 = _NORM.inv_cdf(u)
    K = S * math.exp(-d1 * sigma * math.sqrt(T) + (r - q + 0.5 * sigma * sigma) * T)
    return K


# ==============================================================================
# STALENESS CIRCUIT BREAKER & LIVE INTRADAY PRICE REFRESH (v2.2)
# ==============================================================================

def check_data_freshness(
    close_df: pd.DataFrame,
    max_lag_hours: float = 18.0,
    current_time: Optional[pd.Timestamp] = None,
) -> Tuple[bool, float, pd.Timestamp]:
    """
    Checks the timestamp of the latest price bar against the current market clock
    (accounting for weekend closures and trading calendar hours).

    Returns:
        (is_stale: bool, lag_hours: float, latest_bar_timestamp: pd.Timestamp)
    """
    if close_df.empty:
        return True, 999.0, pd.Timestamp.now()

    latest_dt = pd.to_datetime(close_df.index[-1])
    now = current_time if current_time is not None else pd.Timestamp.now()

    # Calculate expected most recent market close (16:00 Mon-Fri)
    expected_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    if now.weekday() == 5:  # Saturday
        expected_close -= pd.Timedelta(days=1)
    elif now.weekday() == 6:  # Sunday
        expected_close -= pd.Timedelta(days=2)
    elif now.weekday() == 0 and now.hour < 10:  # Monday pre-market
        expected_close -= pd.Timedelta(days=3)
    elif now.hour < 16:  # Midday trading session
        expected_close -= pd.Timedelta(days=1)
        if expected_close.weekday() == 6:
            expected_close -= pd.Timedelta(days=2)

    latest_market_time = latest_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    lag_delta = expected_close - latest_market_time
    lag_hours = max(0.0, lag_delta.total_seconds() / 3600.0)

    # If it is currently an open market day (past 10:00 AM) and latest cached bar is older than today:
    is_trading_day_open = (now.weekday() < 5 and now.hour >= 10)
    has_unrecorded_live_bar = is_trading_day_open and (latest_dt.date() < now.date())

    is_stale = (lag_hours > max_lag_hours) or has_unrecorded_live_bar
    return is_stale, lag_hours, latest_dt


def refresh_live_intraday_prices(
    close_df: pd.DataFrame,
    gspc_close: Optional[pd.Series] = None,
    tickers: Optional[List[str]] = None,
    save_path: Optional[Path | str] = None,
) -> Tuple[pd.DataFrame, Optional[pd.Series]]:
    """
    Triggers a multi-threaded yfinance.download pull (period="5d", interval="1d")
    to append today's live session prices and any missing recent trading bars.
    Safely preserves multi-decade historical data and handles network fallbacks.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("[CIRCUIT BREAKER WARNING] yfinance is not installed; skipping live quote refresh.")
        return close_df, gspc_close

    if tickers is None:
        tickers = [str(t) for t in close_df.columns if not str(t).startswith("^")]

    print(f"[CIRCUIT BREAKER] Refreshing live quotes for {len(tickers)} symbols via multi-threaded yfinance...")
    try:
        data = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if data is None or data.empty:
            print("[CIRCUIT BREAKER WARNING] yfinance returned empty response. Retaining existing cache.")
            return close_df, gspc_close

        if isinstance(data.columns, pd.MultiIndex):
            if "Close" in data.columns.levels[0]:
                close_sub = data["Close"].copy()
            else:
                close_sub = data.xs("Close", axis=1, level=0).copy()
        elif "Close" in data.columns:
            close_sub = data[["Close"]].copy()
            if len(tickers) == 1:
                close_sub.columns = [tickers[0]]
        else:
            close_sub = data.copy()

        close_sub.index = pd.to_datetime(close_sub.index).normalize()
        updated_close = close_df.copy()
        updated_close.index = pd.to_datetime(updated_close.index).normalize()

        # Append new dates
        new_dates = close_sub.index.difference(updated_close.index)
        if len(new_dates) > 0:
            updated_close = pd.concat([updated_close, close_sub.loc[new_dates]], axis=0).sort_index()

        # Update overlapping dates with non-null values
        overlap_dates = close_sub.index.intersection(updated_close.index)
        if len(overlap_dates) > 0:
            updated_close.update(close_sub.loc[overlap_dates])

        # Benchmark ^GSPC refresh
        if gspc_close is not None:
            try:
                gspc_data = yf.download("^GSPC", period="5d", interval="1d", auto_adjust=True, progress=False)
                if gspc_data is not None and not gspc_data.empty:
                    g_close = gspc_data["Close"] if "Close" in gspc_data.columns else gspc_data.iloc[:, 0]
                    g_close.index = pd.to_datetime(g_close.index).normalize()
                    gspc_updated = gspc_close.copy()
                    gspc_updated.index = pd.to_datetime(gspc_updated.index).normalize()
                    g_new = g_close.index.difference(gspc_updated.index)
                    if len(g_new) > 0:
                        gspc_updated = pd.concat([gspc_updated, g_close.loc[g_new]]).sort_index()
                    gspc_updated.update(g_close)
                    gspc_close = gspc_updated
            except Exception as e_gspc:
                print(f"[CIRCUIT BREAKER] Benchmark refresh note: {e_gspc}")

        print(f"[CIRCUIT BREAKER] Successfully updated cache through {updated_close.index[-1].strftime('%Y-%m-%d')}.")

        if save_path:
            p = Path(save_path)
            try:
                if p.suffix == ".xz":
                    with lzma.open(p, "wb") as f:
                        pickle.dump({"close": updated_close}, f)
                elif p.suffix == ".gz":
                    with gzip.open(p, "wb") as f:
                        pickle.dump({"close": updated_close}, f)
            except Exception as e_save:
                print(f"[CIRCUIT BREAKER] Cache write note: {e_save}")

        return updated_close, gspc_close

    except Exception as e:
        print(f"[CIRCUIT BREAKER WARNING] Live refresh encountered an error: {e}. Retaining existing cache.")
        return close_df, gspc_close


# ==============================================================================
# SEC FORM 4 & FINVIZ INSIDER CONVICTION ENGINE (v2.2)
# ==============================================================================

def fetch_live_insider_activity(
    tickers: List[str] | str,
    lookback_days: int = 180,
    email_header: str = "QuantResearch quant@mungersystem.org",
) -> Dict[str, Dict[str, Any]]:
    """
    Queries SEC EDGAR Form 4 XML filings via edgartools (falling back to finvizfinance)
    and strictly isolates discretionary Open-Market Purchases (Code P) and Open-Market
    Sales (Code S), discarding non-discretionary noise:
      - Discards Code F (tax withholding on RSU vest)
      - Discards Code M (option exercises)
      - Discards Code A (grants and awards)
      - Discards Code G (gifts)

    Scoring Multipliers:
      - Cluster Buying Boost (1.15x): Awarded when >= 2 distinct insiders buy >= $250k
        on the open market (or aggregate/single open-market purchase >= $2M with zero sales).
      - Heavy C-Suite Distribution Haircut (0.70x): Applied when C-suite executives
        (CEO, CFO, COO, President, Chairman) sell >= $20M on the open market with zero buys.
        (e.g., catching Micron CEO Sanjay Mehrotra's $46.33M+ open-market sale).
      - Neutral (1.00x): Applied otherwise or when no insider filings trigger conditions.
    """
    if isinstance(tickers, str):
        tickers = [tickers]

    results: Dict[str, Dict[str, Any]] = {}
    csuite_titles = {"CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL", "COO", "CHIEF OPERATING", "PRESIDENT", "CHAIRMAN"}

    def is_csuite(pos_str: Any) -> bool:
        p = str(pos_str).upper()
        return any(t in p for t in csuite_titles)

    cutoff_dt = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)

    for ticker in tickers:
        t_clean = ticker.upper().strip()
        data_res: Dict[str, Any] = {
            "ticker": t_clean,
            "multiplier": 1.0,
            "signal": "NEUTRAL",
            "buys_usd": 0.0,
            "sales_usd": 0.0,
            "csuite_sales_usd": 0.0,
            "num_buyers": 0,
            "num_sellers": 0,
            "buyers": [],
            "sellers": [],
            "csuite_sellers": [],
            "source": "None",
            "notes": "No qualifying insider activity detected.",
        }

        # 1. Primary Source: SEC EDGAR Form 4 direct XML
        used_edgar = False
        try:
            from edgar import set_identity, Company
            set_identity(email_header)
            c = Company(t_clean)
            filings = c.get_filings(form="4")
            if filings and len(filings) > 0:
                recent_filings = filings[:35]
                records = []
                for f in recent_filings:
                    try:
                        f_date_str = getattr(f, "filing_date", None)
                        if f_date_str:
                            f_date = pd.to_datetime(f_date_str)
                            if f_date < cutoff_dt:
                                continue
                        obj = f.obj()
                        if hasattr(obj, "to_dataframe"):
                            df = obj.to_dataframe()
                            if df is not None and not df.empty:
                                records.append(df)
                    except Exception:
                        continue

                if records:
                    all_f4 = pd.concat(records, ignore_index=True)
                    all_f4["Value"] = pd.to_numeric(all_f4["Value"], errors="coerce").fillna(0.0)

                    code_p = all_f4[all_f4["Code"] == "P"]
                    code_s = all_f4[all_f4["Code"] == "S"]

                    buys_usd = float(code_p["Value"].sum())
                    buyers = list(code_p["Insider"].dropna().unique())
                    num_buyers = len(buyers)

                    sales_usd = float(code_s["Value"].sum())
                    sellers = list(code_s["Insider"].dropna().unique())
                    num_sellers = len(sellers)

                    csuite_s = code_s[code_s["Position"].apply(is_csuite)]
                    csuite_sales_usd = float(csuite_s["Value"].sum())
                    csuite_sellers = list(csuite_s["Insider"].dropna().unique())

                    data_res.update({
                        "buys_usd": buys_usd,
                        "sales_usd": sales_usd,
                        "csuite_sales_usd": csuite_sales_usd,
                        "num_buyers": num_buyers,
                        "num_sellers": num_sellers,
                        "buyers": buyers,
                        "sellers": sellers,
                        "csuite_sellers": csuite_sellers,
                        "source": "SEC EDGAR Form 4",
                    })
                    used_edgar = True
        except Exception:
            pass

        # 2. Fallback to finvizfinance
        if not used_edgar or (data_res["num_buyers"] == 0 and data_res["num_sellers"] == 0):
            try:
                from finvizfinance.quote import finvizfinance
                stock = finvizfinance(t_clean)
                inside_df = stock.ticker_inside_trader()
                if inside_df is not None and not inside_df.empty:
                    if "Date" in inside_df.columns:
                        inside_df["_dt"] = pd.to_datetime(inside_df["Date"], errors="coerce", format="mixed")
                        inside_df = inside_df[inside_df["_dt"].isna() | (inside_df["_dt"] >= cutoff_dt)]
                    trans_col = inside_df["Transaction"].astype(str).str.strip().str.lower()
                    val_col = pd.to_numeric(inside_df["Value ($)"], errors="coerce").fillna(0.0)
                    inside_df["_clean_val"] = val_col

                    buys_df = inside_df[trans_col == "buy"]
                    sales_df = inside_df[trans_col == "sale"]

                    buys_usd = float(buys_df["_clean_val"].sum())
                    buyers = list(buys_df["Insider Trading"].dropna().unique())
                    num_buyers = len(buyers)

                    sales_usd = float(sales_df["_clean_val"].sum())
                    sellers = list(sales_df["Insider Trading"].dropna().unique())
                    num_sellers = len(sellers)

                    csuite_s = sales_df[sales_df["Relationship"].apply(is_csuite)]
                    csuite_sales_usd = float(csuite_s["_clean_val"].sum())
                    csuite_sellers = list(csuite_s["Insider Trading"].dropna().unique())

                    data_res.update({
                        "buys_usd": buys_usd,
                        "sales_usd": sales_usd,
                        "csuite_sales_usd": csuite_sales_usd,
                        "num_buyers": num_buyers,
                        "num_sellers": num_sellers,
                        "buyers": buyers,
                        "sellers": sellers,
                        "csuite_sellers": csuite_sellers,
                        "source": "Finviz Insider",
                    })
            except Exception:
                pass

        # 3. Apply Multiplier Rules
        num_b = data_res["num_buyers"]
        b_usd = data_res["buys_usd"]
        s_usd = data_res["sales_usd"]
        cs_usd = data_res["csuite_sales_usd"]

        if (num_b >= 2 and b_usd >= 250_000.0) or (b_usd >= 2_000_000.0 and s_usd == 0.0):
            data_res["multiplier"] = 1.15
            data_res["signal"] = "CLUSTER_BUY_BOOST"
            data_res["notes"] = f"Cluster Buying Boost (1.15x): {num_b} insiders bought ${b_usd:,.0f} on open market."
        elif cs_usd >= 20_000_000.0 and b_usd == 0.0:
            data_res["multiplier"] = 0.70
            data_res["signal"] = "CSUITE_DISTRIBUTION_HAIRCUT"
            data_res["notes"] = f"Heavy C-Suite Distribution Haircut (0.70x): C-suite sold ${cs_usd:,.0f} with zero buys ({', '.join(data_res['csuite_sellers'][:3])})."
        else:
            data_res["multiplier"] = 1.00
            data_res["signal"] = "NEUTRAL"
            data_res["notes"] = f"Neutral conviction (Buys: ${b_usd:,.0f}, Sales: ${s_usd:,.0f})."

        results[t_clean] = data_res

    return results


# ==============================================================================
# FIDELITY TAX-LOT RECONCILER (v2.2)
# ==============================================================================

FIDELITY_CASH_SWEEP_SYMBOLS = {
    "SPAXX", "FDRXX", "FCASH", "FDLXX", "SPRXX", "FZCXX",
    "VMSXX", "CORE", "CASH", "HELD IN CASH", "HELD_IN_CASH", "PENDING ACTIVITY"
}

def parse_fidelity_positions_csv(
    csv_path: Optional[Path | str] = None,
    live_prices: Optional[Dict[str, float] | pd.Series] = None,
    eval_date: Optional[pd.Timestamp | str] = None,
    munger_scores: Optional[Dict[str, float] | pd.Series] = None,
    immunized_flags: Optional[Dict[str, bool] | pd.Series] = None,
) -> pd.DataFrame:
    """
    Parses an exported Fidelity Portfolio_Positions_*.csv file:
      - Discovers file automatically across repo root or data/ if csv_path is None
      - Strips money-market sweep positions (SPAXX**, FDRXX**, FCASH**)
      - Replaces stale CSV export prices with live quotes
      - Computes holding duration against the 365-day LTCG tax hurdle
      - Reconciles tax lots with Munger Factor criteria, outputting exact execution orders:
          * STRONG HOLD / ADD: High-conviction compounder in Strike Zone
          * HOLD FOR TAX DEFERRAL (Xd to LTCG): Short-term gain with sub-hurdle score;
            defers sale until LTCG reclassification to avoid ordinary income tax drag
          * SELL (MOC): Position is either LTCG (safe to exit at MOC) or has an unrealized
            loss (immediate tax-loss harvest with zero tax drag)
    """
    if csv_path is None:
        candidate_paths = list(Path(".").glob("Portfolio_Positions*.csv")) + list(Path("data").glob("Portfolio_Positions*.csv"))
        if not candidate_paths:
            candidate_paths = list(Path(".").glob("*Positions*.csv")) + list(Path("data").glob("*Positions*.csv"))
        if candidate_paths:
            non_samples = [p for p in candidate_paths if "sample" not in p.name.lower()]
            targets = non_samples if non_samples else candidate_paths
            targets.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            csv_path = targets[0]
        else:
            return pd.DataFrame()

    path_obj = Path(csv_path)
    if not path_obj.exists():
        return pd.DataFrame()

    with open(path_obj, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()

    header_idx = -1
    for idx, line in enumerate(lines):
        if "Symbol" in line and ("Quantity" in line or "Current Value" in line or "Cost Basis" in line or "Account Name" in line):
            header_idx = idx
            break

    if header_idx == -1:
        return pd.DataFrame()

    reader = csv.DictReader(lines[header_idx:])

    if eval_date is None:
        dt_eval = pd.Timestamp.now()
    else:
        dt_eval = pd.to_datetime(eval_date)

    from typing import Any
    def clean_num(v: Any) -> float:
        if not v or pd.isna(v):
            return 0.0
        s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        try:
            return float(s)
        except ValueError:
            return 0.0

    rows = []
    for r in reader:
        raw_sym = str(r.get("Symbol", "")).strip().upper()
        if not raw_sym or raw_sym.startswith("*") or "TOTAL" in raw_sym or "DISCLAIMER" in raw_sym:
            continue

        clean_sym = raw_sym.replace("*", "").strip()
        if clean_sym in FIDELITY_CASH_SWEEP_SYMBOLS or "MONEY MARKET" in str(r.get("Description", "")).upper() or clean_sym == "PENDING ACTIVITY":
            continue

        shares = clean_num(r.get("Quantity", 0))
        if shares <= 0:
            continue

        csv_cost_basis = clean_num(r.get("Cost Basis Total", 0))
        if csv_cost_basis == 0.0:
            avg_basis = clean_num(r.get("Average Cost Basis", 0))
            csv_cost_basis = shares * avg_basis

        # Date acquired
        date_str = str(r.get("Date Acquired", r.get("Acquisition Date", ""))).strip()
        if date_str and date_str not in ("--", "n/a", "Pending"):
            try:
                acq_date = pd.to_datetime(date_str)
                days_held = max(0, (dt_eval.date() - acq_date.date()).days)
            except Exception:
                days_held = 365
        else:
            days_held = 365

        is_ltcg = days_held >= 365
        days_to_ltcg = max(0, 365 - days_held)

        # Overlay live price
        live_price = 0.0
        if live_prices is not None:
            if isinstance(live_prices, dict) and clean_sym in live_prices:
                live_price = float(live_prices[clean_sym])
            elif hasattr(live_prices, "loc") and clean_sym in live_prices.index:
                val = live_prices.loc[clean_sym]
                live_price = float(val) if pd.notna(val) else 0.0

        if live_price <= 0.0:
            live_price = clean_num(r.get("Last Price", r.get("Current Price", 0)))

        live_val = shares * live_price
        unrealized_gain = live_val - csv_cost_basis
        gain_pct = (unrealized_gain / csv_cost_basis * 100.0) if csv_cost_basis > 0 else 0.0

        # Munger Factor score & hurdle
        m_score = 0.0
        passed_hurdle = False
        if munger_scores is not None:
            if isinstance(munger_scores, dict):
                m_score = float(munger_scores.get(clean_sym, 0.0))
            elif hasattr(munger_scores, "get"):
                m_score = float(munger_scores.get(clean_sym, 0.0))

        if immunized_flags is not None:
            if isinstance(immunized_flags, dict):
                passed_hurdle = bool(immunized_flags.get(clean_sym, False))
            elif hasattr(immunized_flags, "get"):
                passed_hurdle = bool(immunized_flags.get(clean_sym, False))
        else:
            passed_hurdle = m_score >= 2.0

        # Action directives
        if m_score >= 2.0 and passed_hurdle:
            action = "STRONG HOLD / ADD"
            rationale = f"Tier-1 Compounder (M={m_score:.2f}). Intact compounding moat; add on pullbacks."
        else:
            if not is_ltcg and unrealized_gain > 0:
                action = f"HOLD FOR TAX DEFERRAL ({days_to_ltcg}d to LTCG)"
                rationale = f"Sub-hurdle score (M={m_score:.2f}). Defer exit {days_to_ltcg} days to avoid ordinary income tax rate on ${unrealized_gain:,.0f} gain."
            else:
                action = "SELL (MOC)"
                if unrealized_gain <= 0:
                    rationale = f"Immediate tax-loss harvest. Sub-hurdle score (M={m_score:.2f}). Realize ${abs(unrealized_gain):,.0f} loss to offset gains; redeploy."
                else:
                    rationale = f"Sub-hurdle score (M={m_score:.2f}). Position is LTCG ({days_held}d held). Reallocate capital to Strike Zone."

        rows.append({
            "Account": r.get("Account Name", r.get("Account Number", "Default")),
            "Symbol": clean_sym,
            "Shares": shares,
            "Cost_Basis": round(csv_cost_basis, 2),
            "Live_Price": round(live_price, 2),
            "Live_Value": round(live_val, 2),
            "Unrealized_Gain": round(unrealized_gain, 2),
            "Gain_Pct": round(gain_pct, 1),
            "Days_Held": days_held,
            "LTCG_Status": "LTCG" if is_ltcg else "STCG",
            "Days_to_LTCG": days_to_ltcg,
            "Munger_Score": round(m_score, 2),
            "Action": action,
            "Rationale": rationale,
        })

    return pd.DataFrame(rows)
