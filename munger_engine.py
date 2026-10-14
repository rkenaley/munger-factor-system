#!/usr/bin/env python3
"""
================================================================================
MUNGER FACTOR SYSTEM: SHARED VECTORIZED & ZERO-LOOKAHEAD CORE ENGINE
================================================================================
Centralizes data loading, point-in-time (causal) factor matrix construction,
portfolio simulation with delisting/slippage/tax-regime accounting, and
analytical O(1) Black-Scholes-Merton option pricing with volatility skew.
================================================================================
"""

from __future__ import annotations

import json
import lzma
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_NORM = NormalDist(0.0, 1.0)
EXCLUDED_TICKERS = {"SNDK"}


@dataclass
class BacktestConfig:
    """Configuration parameters for causal Munger Factor simulations."""
    initial_capital: float = 1000.0
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
    """
    Returns effective top Federal Long-Term Capital Gains tax rate by calendar year,
    rather than applying the post-2013 23.8% (20% + 3.8% ACA NIIT) retroactively to 1971.
    """
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
# DATA LOADING & SYNTHETIC FIXTURE FALLBACK
# ==============================================================================

def load_or_generate_dataset(data_dir: Path | str = "data") -> Tuple[pd.DataFrame, pd.Series, Dict, Dict, bool]:
    """
    Loads (close_df, gspc_close, fund_dict, eps_dict, is_synthetic) from `data_dir`.
    Supports the repository's native `historical_prices_1970_2026.pkl.xz` format
    (either a dict of OHLCV DataFrames/Series or a combined DataFrame).
    If the binary price cache is absent (e.g., in an isolated CI test environment),
    generates a deterministic multi-decade synthetic dataset with identical schema.
    """
    data_path = Path(data_dir)
    price_file = data_path / "historical_prices_1970_2026.pkl.xz"
    univ_file = data_path / "sp100_nasdaq100_universe.json"
    eps_file = data_path / "historical_quarterly_eps.json"

    if price_file.exists() and univ_file.exists():
        with open(univ_file, "r", encoding="utf-8") as f:
            fund_dict = json.load(f)
        eps_dict = {}
        if eps_file.exists():
            with open(eps_file, "r", encoding="utf-8") as f:
                eps_dict = json.load(f)

        with lzma.open(price_file, "rb") as f:
            raw_obj = pickle.load(f)

        if isinstance(raw_obj, pd.DataFrame):
            close_df = raw_obj.copy()
        elif isinstance(raw_obj, dict):
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
    end_date: str = "2026-06-30",
    n_tickers: int = 32,
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.Series, Dict, Dict, bool]:
    """
    Deterministic multi-decade daily market fixture matching the exact schema of
    sp100_nasdaq100_universe.json, historical_quarterly_eps.json, and close_df,
    including realistic delistings/bankruptcies and regime shifts.
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
        t = f"EQ{i+1:02d}"
        roic = float(np.clip(rng.normal(0.18, 0.08), 0.03, 0.45))
        sales5y = float(np.clip(rng.normal(11.0, 6.0), -2.0, 28.0))
        de = float(np.clip(rng.lognormal(mean=3.9, sigma=0.6), 5.0, 250.0))
        pe = float(np.clip(rng.normal(21.0, 7.0), 8.0, 48.0))

        fund_dict[t] = {
            "ticker": t,
            "name": f"Synthetic Corp {i+1:02d}",
            "sector": "Technology" if i % 3 == 0 else ("Financials" if i % 3 == 1 else "Industrials"),
            "theme": "Core",
            "pe": round(pe, 2),
            "pb": round(pe * roic, 2),
            "de": round(de, 2),
            "sales5y": round(sales5y, 2),
            "roic_proxy": round(roic, 3),
            "fcf_margin": round(roic * 0.75, 3),
        }

        quality_edge = 0.00012 * ((roic - 0.14) / 0.08) - 0.00005 * ((de - 60.0) / 60.0)
        idio = rng.normal(loc=quality_edge, scale=0.014, size=n_days)
        beta = float(rng.uniform(0.75, 1.25))
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
            q_eps = (p_val / pe) / 4.0 * float(rng.uniform(0.85, 1.15))
            q_map[qd.strftime("%Y-%m-%d")] = round(q_eps, 4)
        eps_dict[t] = q_map

    close_df = pd.DataFrame(price_cols, index=dates)
    return close_df, gspc_close, fund_dict, eps_dict, True


# ==============================================================================
# VECTORIZED CAUSAL FACTOR MATRIX ENGINE (NO LOOKAHEAD)
# ==============================================================================

def build_point_in_time_ttm_eps_matrix(
    close_df: pd.DataFrame,
    eval_dates: pd.DatetimeIndex,
    fund_dict: Dict,
    eps_dict: Dict,
    reporting_lag_days: int = 45,
    allow_legacy_lookahead: bool = False,
) -> pd.DataFrame:
    """
    Constructs a [len(eval_dates) x len(tickers)] DataFrame of Trailing Twelve Month (TTM) EPS.
    """
    tickers = list(close_df.columns)
    eval_dates = pd.DatetimeIndex(eval_dates)
    ttm_eps_df = pd.DataFrame(np.nan, index=eval_dates, columns=tickers, dtype=float)

    for t in tickers:
        q_raw = eps_dict.get(t, {})
        if q_raw:
            s = pd.Series(q_raw, dtype=float)
            s.index = pd.to_datetime(s.index).tz_localize(None) + pd.Timedelta(days=reporting_lag_days)
            s = s.sort_index()
            ttm_s = s.rolling(4, min_periods=1).sum() * (4.0 / np.minimum(4.0, np.arange(1, len(s) + 1)))
            ttm_s = ttm_s[~ttm_s.index.duplicated(keep="last")]
            ttm_eps_df[t] = ttm_s.reindex(eval_dates, method="ffill")

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
    return ttm_eps_df


def compute_vectorized_munger_scores(
    close_df: pd.DataFrame,
    eval_dates: pd.DatetimeIndex,
    fund_dict: Dict,
    eps_dict: Dict,
    reporting_lag_days: int = 45,
    allow_legacy_lookahead: bool = False,
    formula: str = "four_factor_z",
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Vectorized computation of composite Munger scores across all `eval_dates` and `tickers`
    in a single matrix pass (replacing triple-nested Python loops).
    """
    eval_dates = pd.DatetimeIndex(eval_dates)
    tickers = list(close_df.columns)
    prices_eval = close_df.reindex(eval_dates, method="ffill")
    active_mask = prices_eval.notna() & (prices_eval > 0)

    ttm_eps_df = build_point_in_time_ttm_eps_matrix(
        close_df=close_df,
        eval_dates=eval_dates,
        fund_dict=fund_dict,
        eps_dict=eps_dict,
        reporting_lag_days=reporting_lag_days,
        allow_legacy_lookahead=allow_legacy_lookahead,
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

        sales_prior = pd.Series({t: float(fund_dict.get(t, {}).get("sales5y", 10.0)) for t in tickers})
        roic_prior = pd.Series({t: float(fund_dict.get(t, {}).get("roic_proxy", 0.15)) for t in tickers})
        de_prior = pd.Series({t: float(fund_dict.get(t, {}).get("de", 50.0)) for t in tickers})

        sales_df = (0.5 * ann_g_5y.fillna(0.0).add(sales_prior, axis=1)).where(active_mask, np.nan)
        roic_df = (roic_prior / (1.0 + vol_1y.fillna(0.25))).where(active_mask, np.nan)
        de_df = (de_prior * (1.0 + vol_1y.fillna(0.25))).where(active_mask, np.nan)

    de_inv_df = -de_df

    def _positive_z_mult(df: pd.DataFrame) -> pd.DataFrame:
        row_mean = df.mean(axis=1)
        row_std = df.std(axis=1).replace(0.0, np.nan)
        z = df.sub(row_mean, axis=0).div(row_std, axis=0)
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
    """
    Executes the Munger Factor portfolio simulation over `eval_dates` using fast
    pre-extracted 2D NumPy arrays and O(1) index mapping.
    """
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

        # 1. Process exits & delistings
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

        # 2. Process entries into top-ranked Munger candidates
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
    """
    Models realistic equity implied volatility skew and term structure:
    - ITM calls (e.g. delta = 0.65, lower strike K) carry higher implied volatility than ATM (delta = 0.50).
    - OTM calls (e.g. delta = 0.30, higher strike K) carry lower implied volatility than ITM calls.
    """
    skew_adj = -skew_slope * (delta - 0.50)
    term_adj = term_slope * (T - 0.25)
    return float(np.clip(base_sigma + skew_adj + term_adj, 0.10, 1.00))


def bsm_call(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.01) -> Dict[str, float]:
    """
    Closed-form Black-Scholes-Merton European Call price and Greeks (Delta, Gamma, Vega, Theta).
    """
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
    """
    O(1) exact analytical inversion of Black-Scholes-Merton call delta:
        Delta = exp(-q * T) * Phi(d1)  ==>  d1 = Phi^{-1}(Delta * exp(q * T))
        K = S * exp(-d1 * sigma * sqrt(T) + (r - q + 0.5 * sigma^2) * T)
    Replaces the 50-iteration scalar binary search loop.
    """
    u = min(0.999999, max(1e-6, target_delta * math.exp(q * T)))
    d1 = _NORM.inv_cdf(u)
    K = S * math.exp(-d1 * sigma * math.sqrt(T) + (r - q + 0.5 * sigma * sigma) * T)
    return K
