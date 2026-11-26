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
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

_NORM = NormalDist(0.0, 1.0)
EXCLUDED_TICKERS = {"SNDK"}


def load_coi_excluded_tickers(data_dir: Path | str = "data") -> set[str]:
    """Load physician conflict-of-interest (Biotech/Pharma) ticker exclusions."""
    data_path = Path(data_dir)
    if not data_path.exists():
        alt_path = Path(__file__).resolve().parent / data_dir
        if alt_path.exists():
            data_path = alt_path

    coi_file = data_path / "coi_exclusions.json"
    if not coi_file.exists():
        return set()

    try:
        with open(coi_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return set()

    if not cfg.get("enabled", True):
        return set()

    excluded = {str(t).upper().strip() for t in cfg.get("excluded_tickers", []) if t}
    excluded_themes = {str(th).strip().lower() for th in cfg.get("excluded_themes", []) if th}

    sp900_file = data_path / "sp900_universe.json"
    if excluded_themes and sp900_file.exists():
        try:
            with open(sp900_file, "r", encoding="utf-8") as f:
                univ = json.load(f)
            if isinstance(univ, dict):
                for ticker, meta in univ.items():
                    if isinstance(meta, dict):
                        theme = str(meta.get("theme", "")).strip().lower()
                        if theme in excluded_themes:
                            excluded.add(str(ticker).upper().strip())
        except Exception:
            pass

    return excluded


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
    apply_anti_crest_guard: bool = True  # Damps peak-windfall/blow-off scores and blocks broken-EPS downtrend traps


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

CAPITAL_LIGHT_FINANCIAL_TICKERS = {
    "V", "MA", "AXP", "SPGI", "MCO", "MSCI", "FDS", "CME", "ICE", "NDAQ",
    "CBOE", "MKTX", "MMC", "AJG", "AON", "BRO", "WTW", "RYAN", "FI", "FIS",
    "GPN", "JKHY", "CPAY", "WEX", "PYPL", "BR", "ERIE",
}

CAPITAL_LIGHT_FINANCIAL_THEMES = {
    "Transaction & Payment Processing Services",
    "Financial Exchanges & Data",
    "Insurance Brokers",
}

INSURANCE_AND_BANK_KEYWORDS = (
    "insurance", "reinsurance", "bank", "thrift", "mortgage",
    "consumer finance", "multi-sector holdings", "diversified financial",
)

COMMODITY_CYCLICAL_KEYWORDS = (
    "oil & gas", "refining", "fertilizer", "aluminum", "steel", "copper",
    "gold", "silver", "metals & mining", "commodity chemical", "coal",
    "forest product", "paper",
)


def classify_balance_sheet_profile(ticker: str, meta: Dict[str, Any]) -> str:
    """
    Classifies a constituent's balance-sheet structure to guard against:
      1. Insurance float & bank deposit leverage distorting ROE/ROIC and P/E
      2. Commodity boom-bust peak earnings distorting trailing P/E and 1Y revenue spikes
      3. Preserves capital-light financial tollbooths (Visa, Mastercard, S&P Global, Moody's)
    """
    tk = str(ticker).upper().strip()
    sector = str(meta.get("sector", "")).strip()
    theme = str(meta.get("theme", "")).strip()
    theme_low = theme.lower()

    if theme == "Core" or tk.startswith("EQ"):
        return "STANDARD"

    if tk in CAPITAL_LIGHT_FINANCIAL_TICKERS or theme in CAPITAL_LIGHT_FINANCIAL_THEMES:
        return "CAPITAL_LIGHT_TOLLBOOTH"

    if any(k in theme_low for k in INSURANCE_AND_BANK_KEYWORDS):
        return "INSURANCE_OR_BANK"

    if sector == "Financials":
        if tk in {"BAC", "JPM", "WFC", "C", "GS", "MS", "USB", "BNY", "COF", "SCHW", "BRK-B", "AMP"}:
            return "INSURANCE_OR_BANK"
        return "OTHER_FINANCIAL"

    if sector in {"Energy", "Materials", "Basic Materials"} or any(k in theme_low for k in COMMODITY_CYCLICAL_KEYWORDS):
        return "COMMODITY_CYCLICAL"

    return "STANDARD"


def sanitize_universe_fundamentals(fund_dict: Dict, eps_dict: Dict) -> Dict:
    """
    Sanitizes scraper artifacts in `sp100_nasdaq100_universe.json` without mutating
    raw files unless requested:
      1. Caps single-quarter/1-year YoY revenue spikes misrecorded as 5-year CAGRs:
         - Tech/Healthcare outliers > 65% (e.g., MU's `sales5y: 379.3` -> `36.8%`)
         - Financials & Commodity Cyclicals > 18% where 1Y GAAP mark-to-market
           investment gains (ASU 2016-01), M&A, or commodity rebounds were scraped
           from `revenueGrowth` (e.g., CINF `31.6%` -> `5.65%`, EOG `58.7%` -> `9.68%`).
      2. De-leverages ROE-based `roic_proxy` for Balance-Sheet Financials (Insurers/Banks)
         where policyholder float and deposit liabilities create 4x-10x equity leverage
         and peak underwriting cycles inflate ROE (e.g., ALL `46.1%` -> `13.5%`).
      3. Book-Anchored Normalized P/E Floor for Insurers, Banks & Commodity Cyclicals:
         Prevents peak-cycle earnings windfalls (`PE = 5.0x` at `P/B = 2.0x`) from
         masquerading as secular value bargains by anchoring normalized P/E to
         `P/B / Normalized_Through_Cycle_ROE`.
      4. De-leverages non-financial buyback/debt cannibals where tiny GAAP book equity
         inflates ROE (`P/B > 12x` and `D/E > 60%`).
    """
    cleaned = {}
    for t, meta in fund_dict.items():
        m = dict(meta)
        profile = classify_balance_sheet_profile(t, m)
        m["balance_sheet_profile"] = profile

        raw_s5y = float(m.get("sales5y", 10.0))
        raw_roic = float(m.get("roic_proxy", 0.15))
        raw_pe = float(m.get("pe", 20.0))
        pb = float(m.get("pb", 3.0))
        raw_de = float(m.get("de", 50.0))
        raw_nd = float(m.get("net_debt_to_ebitda", 1.2))
        sector = str(m.get("sector", ""))

        # 1. Revenue Growth Spike Sanitization (1Y YoY spike -> 5Y annualized equivalent)
        if profile in {"INSURANCE_OR_BANK", "OTHER_FINANCIAL", "COMMODITY_CYCLICAL"}:
            s5y_cap_trigger = 18.0
        elif sector not in {"Technology", "Information Technology", "Communication Services", "Health Care"}:
            s5y_cap_trigger = 32.0
        else:
            s5y_cap_trigger = 65.0

        if raw_s5y > s5y_cap_trigger:
            m["sales5y_raw_unadjusted"] = raw_s5y
            m["sales5y"] = round(((1.0 + raw_s5y / 100.0) ** (1.0 / 5.0) - 1.0) * 100.0, 2)

        # 2. Balance-Sheet Float / Leverage ROIC & P/E Normalization
        if profile == "INSURANCE_OR_BANK":
            # Policyholder float / deposit leverage adjustment (cap at 13.5% through-cycle franchise return)
            m["roic_raw_unadjusted"] = raw_roic
            m["roic_proxy"] = round(min(raw_roic / 2.2, 0.135), 3)

            # Book-anchored normalized P/E floor: Normalized P/E >= P/B / 0.135
            book_norm_pe = min(18.5, pb / 0.135) if pb > 0 else 14.5
            if 0 < raw_pe < book_norm_pe:
                m["pe_raw_unadjusted"] = raw_pe
                m["pe"] = round(max(raw_pe, book_norm_pe), 2)

            # Statutory reserves are pledged against policyholder claims, not excess corporate cash
            if raw_de < 55.0:
                m["de_raw_unadjusted"] = raw_de
                m["de"] = 55.0
            if raw_nd < 1.35:
                m["nd_raw_unadjusted"] = raw_nd
                m["net_debt_to_ebitda"] = 1.35

        elif profile == "OTHER_FINANCIAL":
            # Custody/Asset managers & brokerages with balance-sheet or buyback leverage
            lev_divisor = max(1.25, 1.0 + max(0.0, raw_de / 100.0) + max(0.0, (pb - 2.0) * 0.15))
            if raw_roic > 0.165:
                m["roic_raw_unadjusted"] = raw_roic
                m["roic_proxy"] = round(min(raw_roic / lev_divisor, 0.165), 3)
            if pb > 0 and 0 < raw_pe < min(16.0, pb / 0.15):
                m["pe_raw_unadjusted"] = raw_pe
                m["pe"] = round(min(16.0, pb / 0.15), 2)

        elif profile == "COMMODITY_CYCLICAL":
            if raw_roic > 0.145:
                m["roic_raw_unadjusted"] = raw_roic
                m["roic_proxy"] = round(min(raw_roic / 1.5, 0.145), 3)
            book_norm_pe = min(17.5, pb / 0.12) if pb > 0 else 14.0
            if 0 < raw_pe < book_norm_pe:
                m["pe_raw_unadjusted"] = raw_pe
                m["pe"] = round(max(raw_pe, book_norm_pe), 2)

        elif (
            profile == "STANDARD"
            and sector not in {"Technology", "Information Technology", "Communication Services"}
            and pb > 12.0
            and raw_de > 50.0
            and raw_roic > 0.40
        ):
            # Non-tech buyback/debt cannibal where tiny GAAP book equity inflates ROE (e.g. EXPE)
            m["roic_raw_unadjusted"] = raw_roic
            m["roic_proxy"] = round(min(raw_roic / (1.0 + raw_de / 100.0), 0.25), 3)

        cleaned[t] = m
    return cleaned


def load_or_generate_dataset(data_dir: Path | str = "data") -> Tuple[pd.DataFrame, pd.Series, Dict, Dict, bool]:
    """Loads (close_df, gspc_close, fund_dict, eps_dict, is_synthetic) from `data_dir`."""
    data_path = Path(data_dir)
    if not data_path.exists():
        alt_path = Path(__file__).resolve().parent / data_dir
        if alt_path.exists():
            data_path = alt_path
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
        # Drop incomplete/after-hours sparse phantom bars (e.g. <50% ticker coverage)
        while not close_df.empty and float(close_df.iloc[-1].notna().mean()) < 0.50:
            close_df = close_df.iloc[:-1]
        if not close_df.empty:
            gspc_close = gspc_close.reindex(close_df.index).ffill().bfill()
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
        meta = fund_dict.get(t, {})
        profile = meta.get("balance_sheet_profile") or classify_balance_sheet_profile(t, meta)
        q_raw = eps_dict.get(t, {})
        if q_raw:
            s = pd.Series(q_raw, dtype=float)
            s.index = pd.to_datetime(s.index).tz_localize(None) + pd.Timedelta(days=reporting_lag_days)
            s = s.sort_index()
            ttm_s = s.rolling(4, min_periods=1).sum() * (4.0 / np.minimum(4.0, np.arange(1, len(s) + 1)))
            ttm_s = ttm_s[~ttm_s.index.duplicated(keep="last")]

            min_5y_ttm = ttm_s.rolling(20, min_periods=4).min()
            if profile == "INSURANCE_OR_BANK" and not allow_legacy_lookahead:
                # For balance-sheet insurers/banks, any negative reported quarter in trailing 5Y
                # or negative TTM window flags underwriting/catastrophe/mark-to-market loss cycle
                min_5y_q = s[~s.index.duplicated(keep="last")].rolling(20, min_periods=4).min()
                prof_s = ((min_5y_ttm > 0.0) & (min_5y_q > 0.0)).astype(float)
            else:
                prof_s = (min_5y_ttm > 0.0).astype(float)
            full_cycle_profitable_df[t] = prof_s.reindex(eval_dates, method="ffill").fillna(1.0).astype(bool)

            if use_cyclically_adjusted_ey and not allow_legacy_lookahead:
                med_5y_ttm = ttm_s.rolling(20, min_periods=4).median()
                eff_eps_s = np.minimum(ttm_s, 0.50 * ttm_s + 0.50 * med_5y_ttm)
            else:
                eff_eps_s = ttm_s

            ttm_eps_df[t] = eff_eps_s.reindex(eval_dates, method="ffill")

        if not allow_legacy_lookahead and profile in {"INSURANCE_OR_BANK", "COMMODITY_CYCLICAL"}:
            raw_pe = float(meta.get("pe_raw_unadjusted", meta.get("pe", 20.0)))
            raw_roic = float(meta.get("roic_raw_unadjusted", meta.get("roic_proxy", 0.15)))
            if 0.0 < raw_pe < 10.5 and raw_roic >= 0.18:
                # Peak-cycle underwriting / GAAP mark-to-market / commodity windfall trap
                full_cycle_profitable_df[t] = False

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
            meta = fund_dict.get(t, {})
            profile = meta.get("balance_sheet_profile") or classify_balance_sheet_profile(t, meta)
            base_pe = float(meta.get("pe", 20.0))
            safe_pe = base_pe if base_pe > 0 else 20.0
            causal_eps = trailing_3y_mean_price[t] / safe_pe
            mask = ttm_eps_df[t].isna() | (ttm_eps_df[t] <= 0)
            ttm_eps_df.loc[mask, t] = causal_eps.loc[mask]
            if use_cyclically_adjusted_ey and profile in {"INSURANCE_OR_BANK", "OTHER_FINANCIAL", "COMMODITY_CYCLICAL"}:
                # Cap point-in-time EPS by Book-Anchored Normalized P/E ceiling
                norm_eps_cap = prices_at_eval[t] / safe_pe
                ttm_eps_df[t] = np.minimum(ttm_eps_df[t], norm_eps_cap)

    inactive_mask = prices_at_eval.isna() | (prices_at_eval <= 0)
    ttm_eps_df = ttm_eps_df.mask(inactive_mask, np.nan)
    return ttm_eps_df, full_cycle_profitable_df


def compute_cycle_and_trend_diagnostics(
    close_df: pd.DataFrame,
    eval_dates: pd.DatetimeIndex,
    eps_dict: Dict,
    reporting_lag_days: int = 45,
) -> Dict[str, pd.DataFrame]:
    """
    Computes strictly causal (zero-lookahead) price-cycle and EPS-slope diagnostics
    aligned on `eval_dates` to guard against late-cycle blow-offs, peak windfall
    margins, and post-crest broken downtrends.

    Anti-crest multiplier components:
      - Peak Windfall damping: scales score by 1 / (1 + 0.35 * max(0, TTM/Med5Y - 1.50))
        when trailing TTM EPS exceeds 1.50x its trailing 5-year median across all sectors.
      - Parabolic Blow-Off damping: applies a 0.70x haircut when trailing 12-1M return >= +60%.
      - Broken EPS+Trend gate: zeroes score (0.0x) and disqualifies Strike Zone eligibility
        when fundamentals are rolling over (latest Q EPS YoY < -10% or TTM EPS < 0.85x 3Y peak)
        AND price trend is confirmed broken (Close < 200D SMA and 3M SMA200 slope < 0).
      - Cresting warning flag (`is_cresting`): 2Y run-up >= +80%, 52W drawdown <= -10%,
        and trailing 3M return < 0.
    """
    eval_dates = pd.DatetimeIndex(eval_dates)
    tickers = list(close_df.columns)
    c = close_df.sort_index().ffill()

    mom_12_1 = ((c.shift(21) / c.shift(252)) - 1.0).reindex(eval_dates, method="ffill")
    ret_3m = c.pct_change(63, fill_method=None).reindex(eval_dates, method="ffill")
    ret_6m = c.pct_change(126, fill_method=None).reindex(eval_dates, method="ffill")

    high_52w = c.rolling(252, min_periods=63).max()
    low_2y = c.rolling(504, min_periods=126).min()
    dd_52w = ((c / high_52w) - 1.0).reindex(eval_dates, method="ffill")
    runup_2y = ((high_52w / low_2y) - 1.0).reindex(eval_dates, method="ffill")

    sma200 = c.rolling(200, min_periods=100).mean()
    pct_vs_sma200 = ((c / sma200) - 1.0).reindex(eval_dates, method="ffill")
    sma200_slope_3m = ((sma200 / sma200.shift(63)) - 1.0).reindex(eval_dates, method="ffill")

    lag = pd.Timedelta(days=reporting_lag_days)
    windfall_cols: Dict[str, pd.Series] = {}
    vs_peak_cols: Dict[str, pd.Series] = {}
    ttm_yoy_cols: Dict[str, pd.Series] = {}
    q_yoy_cols: Dict[str, pd.Series] = {}

    for t in tickers:
        q_raw = eps_dict.get(t, {})
        if not q_raw or len(q_raw) < 4:
            continue
        s = pd.Series(q_raw, dtype=float)
        s.index = pd.to_datetime(s.index).tz_localize(None) + lag
        s = s.sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if len(s) < 4:
            continue

        ttm_s = s.rolling(4, min_periods=4).sum()
        med_5y = ttm_s.rolling(20, min_periods=4).median()
        peak_3y = ttm_s.rolling(12, min_periods=4).max()
        ttm_prev4 = ttm_s.shift(4)
        q_prev4 = s.shift(4)

        wf_s = pd.Series(np.nan, index=s.index, dtype=float)
        wf_ok = med_5y > 0.05
        wf_s[wf_ok] = ttm_s[wf_ok] / med_5y[wf_ok]

        pk_s = pd.Series(np.nan, index=s.index, dtype=float)
        pk_ok = peak_3y > 0.05
        pk_s[pk_ok] = ttm_s[pk_ok] / peak_3y[pk_ok]

        tyoy_s = pd.Series(np.nan, index=s.index, dtype=float)
        tyoy_ok = ttm_prev4 > 0.05
        tyoy_s[tyoy_ok] = (ttm_s[tyoy_ok] / ttm_prev4[tyoy_ok]) - 1.0

        qyoy_s = pd.Series(np.nan, index=s.index, dtype=float)
        qyoy_ok = q_prev4 > 0.05
        qyoy_s[qyoy_ok] = (s[qyoy_ok] / q_prev4[qyoy_ok]) - 1.0

        windfall_cols[t] = wf_s.reindex(eval_dates, method="ffill")
        vs_peak_cols[t] = pk_s.reindex(eval_dates, method="ffill")
        ttm_yoy_cols[t] = tyoy_s.reindex(eval_dates, method="ffill")
        q_yoy_cols[t] = qyoy_s.reindex(eval_dates, method="ffill")

    windfall_ratio = pd.DataFrame(windfall_cols, index=eval_dates).reindex(columns=tickers)
    ttm_vs_3y_peak = pd.DataFrame(vs_peak_cols, index=eval_dates).reindex(columns=tickers)
    ttm_eps_yoy = pd.DataFrame(ttm_yoy_cols, index=eval_dates).reindex(columns=tickers)
    q_eps_yoy = pd.DataFrame(q_yoy_cols, index=eval_dates).reindex(columns=tickers)

    is_blowoff = (mom_12_1 >= 0.60).fillna(False)
    is_peak_windfall = (windfall_ratio > 1.50).fillna(False)
    is_cresting = ((runup_2y >= 0.80) & (dd_52w <= -0.10) & (ret_3m < 0.0)).fillna(False)
    is_eps_rollover = ((q_eps_yoy < -0.10) | (ttm_vs_3y_peak < 0.85)).fillna(False)
    is_broken_trend = ((pct_vs_sma200 < 0.0) & (sma200_slope_3m < 0.0)).fillna(False)
    is_broken_eps_trend = is_eps_rollover & is_broken_trend

    windfall_excess = (windfall_ratio.clip(lower=1.0).fillna(1.0) - 1.50).clip(lower=0.0)
    windfall_mult = 1.0 / (1.0 + 0.35 * windfall_excess)
    blowoff_mult = pd.DataFrame(np.where(is_blowoff, 0.70, 1.0), index=eval_dates, columns=tickers)
    anti_crest_mult = (windfall_mult * blowoff_mult).where(~is_broken_eps_trend, 0.0)

    return {
        "mom_12_1": mom_12_1,
        "ret_3m": ret_3m,
        "ret_6m": ret_6m,
        "dd_52w": dd_52w,
        "runup_2y": runup_2y,
        "pct_vs_sma200": pct_vs_sma200,
        "sma200_slope_3m": sma200_slope_3m,
        "windfall_ratio": windfall_ratio,
        "ttm_vs_3y_peak": ttm_vs_3y_peak,
        "ttm_eps_yoy": ttm_eps_yoy,
        "q_eps_yoy": q_eps_yoy,
        "is_blowoff": is_blowoff,
        "is_peak_windfall": is_peak_windfall,
        "is_cresting": is_cresting,
        "is_eps_rollover": is_eps_rollover,
        "is_broken_trend": is_broken_trend,
        "is_broken_eps_trend": is_broken_eps_trend,
        "anti_crest_mult": anti_crest_mult,
    }


def extract_ticker_cycle_snapshot(
    diag: Dict[str, pd.DataFrame],
    dt: pd.Timestamp,
    ticker: str,
) -> Dict[str, Any]:
    """Extracts scalar cycle, momentum, and anti-crest metrics for a single ticker/date."""
    def _val(key: str) -> Optional[float]:
        df = diag.get(key)
        if df is None or ticker not in df.columns or dt not in df.index:
            return None
        v = df.loc[dt, ticker]
        return float(v) if pd.notna(v) else None

    def _flag(key: str) -> bool:
        df = diag.get(key)
        if df is None or ticker not in df.columns or dt not in df.index:
            return False
        return bool(df.loc[dt, ticker])

    flags: List[str] = []
    if _flag("is_broken_eps_trend"):
        flags.append("BROKEN_EPS_TREND")
    else:
        if _flag("is_eps_rollover"):
            flags.append("EPS_ROLLOVER")
        if _flag("is_broken_trend"):
            flags.append("BELOW_FALLING_200D")
    if _flag("is_cresting"):
        flags.append("CRESTING")
    if _flag("is_blowoff"):
        flags.append("BLOWOFF>=60%")
    if _flag("is_peak_windfall"):
        flags.append("PEAK_WINDFALL")

    return {
        "mom_12_1_pct": round((_val("mom_12_1") or 0.0) * 100.0, 1) if _val("mom_12_1") is not None else None,
        "ret_3m_pct": round((_val("ret_3m") or 0.0) * 100.0, 1) if _val("ret_3m") is not None else None,
        "dd_52w_pct": round((_val("dd_52w") or 0.0) * 100.0, 1) if _val("dd_52w") is not None else None,
        "vs_sma200_pct": round((_val("pct_vs_sma200") or 0.0) * 100.0, 1) if _val("pct_vs_sma200") is not None else None,
        "sma200_slope_pct": round((_val("sma200_slope_3m") or 0.0) * 100.0, 1) if _val("sma200_slope_3m") is not None else None,
        "windfall_ratio": round(_val("windfall_ratio"), 2) if _val("windfall_ratio") is not None else None,
        "ttm_vs_3y_peak": round(_val("ttm_vs_3y_peak"), 2) if _val("ttm_vs_3y_peak") is not None else None,
        "q_eps_yoy_pct": round((_val("q_eps_yoy") or 0.0) * 100.0, 1) if _val("q_eps_yoy") is not None else None,
        "anti_crest_mult": round(_val("anti_crest_mult") if _val("anti_crest_mult") is not None else 1.0, 2),
        "is_broken_eps_trend": _flag("is_broken_eps_trend"),
        "is_cresting": _flag("is_cresting"),
        "is_blowoff": _flag("is_blowoff"),
        "is_peak_windfall": _flag("is_peak_windfall"),
        "cycle_flags": flags,
        "cycle_label": ",".join(flags) if flags else "OK",
    }


def classify_candidate_audit_status(
    raw_m: float,
    clean_m: float,
    is_strike_zone: bool,
    insider_mult: float,
    cyc: Dict[str, Any],
    fallback_watchlist_label: str = "Watchlist",
) -> Tuple[str, bool]:
    """
    Returns `(audit_status, is_trap)` shared by CLI scanner and REST API so trap,
    insider, and anti-crest warning annotations never drift apart.
    """
    warn_tags = []
    if cyc.get("is_cresting"):
        warn_tags.append("CREST_WARN")
    if cyc.get("is_blowoff"):
        warn_tags.append("BLOWOFF")
    if cyc.get("is_peak_windfall"):
        warn_tags.append("WINDFALL")
    tag_suffix = f" [{'/'.join(warn_tags)}]" if warn_tags else ""

    if cyc.get("is_broken_eps_trend"):
        return "CREST/EPS TRAP BLOCKED", True
    if is_strike_zone:
        if insider_mult > 1.0:
            return f"STRIKE ZONE (CLUSTER BUY {insider_mult:.2f}x){tag_suffix}", False
        if insider_mult < 1.0:
            return f"STRIKE ZONE (C-SUITE HAIRCUT {insider_mult:.2f}x){tag_suffix}", False
        return f"STRIKE ZONE{tag_suffix}", False
    if raw_m >= 2.0 and clean_m == 0.0:
        return "CYCLICAL TRAP BLOCKED", True
    return f"{fallback_watchlist_label}{tag_suffix}", False


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
    apply_anti_crest_guard: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Vectorized computation of composite Munger scores across all `eval_dates` and `tickers`.

    NOTE (Adversarial Audit Disclosure):
      Even when `allow_legacy_lookahead=False`, `sales_prior`, `roic_prior`, and `de_prior`
      draw from `fund_dict` (a live cross-sectional fundamental snapshot). This is valid for
      live screening and advisory ranking, but historical backtests using a static end-of-period
      `fund_dict` carry static-prior lookahead and universe survivorship bias.
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

    if apply_anti_crest_guard and not allow_legacy_lookahead:
        diag = compute_cycle_and_trend_diagnostics(
            close_df=close_df,
            eval_dates=eval_dates,
            eps_dict=eps_dict,
            reporting_lag_days=reporting_lag_days,
        )
        above_med_df = above_med_df & (~diag["is_broken_eps_trend"])
        scores_df = (scores_df * diag["anti_crest_mult"]).where(active_mask, np.nan)

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
            if config.require_above_median:
                valid_mask = valid_mask & med_mat[step_idx]
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


def bsm_put(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.01) -> Dict[str, float]:
    """Closed-form Black-Scholes-Merton European Put price and delta via Put-Call Parity."""
    if T <= 0.0 or sigma <= 0.0:
        intrinsic = max(0.0, K - S)
        return {
            "price": intrinsic,
            "delta": -1.0 if S < K else 0.0,
        }
    call_res = bsm_call(S, K, T, r, sigma, q)
    exp_qT = math.exp(-q * T)
    exp_rT = math.exp(-r * T)
    put_price = max(0.0, call_res["price"] - S * exp_qT + K * exp_rT)
    return {
        "price": put_price,
        "delta": call_res["delta"] - exp_qT,
    }


def find_put_strike_by_delta_analytical(
    S: float,
    T: float,
    r: float,
    sigma: float,
    target_abs_delta: float,
    q: float = 0.01,
) -> float:
    """O(1) exact analytical inversion of Black-Scholes-Merton put |delta|."""
    if T <= 0.0 or sigma <= 0.0:
        return S
    u = min(0.999999, max(1e-6, target_abs_delta * math.exp(q * T)))
    d1 = -_NORM.inv_cdf(u)
    K = S * math.exp(-d1 * sigma * math.sqrt(T) + (r - q + 0.5 * sigma * sigma) * T)
    return K


def compute_ivr_from_prices(daily_close: pd.Series, window: int = 252) -> Dict[str, Optional[float]]:
    """Computes 21D realized volatility, VRP-adjusted IV proxy, and 1-year IV Rank (0-100)."""
    s = pd.to_numeric(daily_close, errors="coerce").dropna()
    s = s[s > 0]
    if len(s) < 40:
        return {"rv21_pct": None, "iv_proxy_pct": None, "ivr_1y": None}
    log_r = np.log(s / s.shift(1)).dropna()
    rv21 = log_r.rolling(21).std() * math.sqrt(252.0)
    iv_proxy = (1.15 * rv21 + 0.025).clip(lower=0.12, upper=0.95)
    iv_recent = iv_proxy.dropna()
    rv_recent = rv21.dropna()
    if len(iv_recent) < 15 or rv_recent.empty:
        return {"rv21_pct": None, "iv_proxy_pct": None, "ivr_1y": None}
    cur_rv = float(rv_recent.iloc[-1])
    cur_iv = float(iv_recent.iloc[-1])
    win = iv_recent.iloc[-window:]
    iv_min, iv_max = float(win.min()), float(win.max())
    ivr = ((cur_iv - iv_min) / (iv_max - iv_min)) * 100.0 if (iv_max - iv_min) > 1e-6 else 50.0
    return {
        "rv21_pct": round(cur_rv * 100.0, 1),
        "iv_proxy_pct": round(cur_iv * 100.0, 1),
        "ivr_1y": round(float(np.clip(ivr, 0.0, 100.0)), 1),
    }


def compute_two_tier_sma_status(price_series: pd.Series, buffer_ratio: float = 1.05) -> Dict[str, Any]:
    """
    Computes Two-Tier Munger Technical Gating (SMA-50w / SMA-100w / SMA-200w):
      - TIER2_BUY_100%: Price <= 1.05 * SMA_200w (deep crash full deployment)
      - TIER1_BUY_50%:  Price <= 1.05 * SMA_100w (backtest-optimal 8.12x sweet spot)
      - CSP_LIZARD_ZONE: Price <= 1.00 * SMA_50w (sell 25d CSP / Jade Lizard)
      - HOLD_CASH: Extended above SMA_50w
    """
    s = pd.to_numeric(price_series, errors="coerce").dropna()
    s = s[s > 0]
    if s.empty:
        return {
            "spot": 0.0,
            "sma50w": 0.0,
            "sma100w": 0.0,
            "sma200w": 0.0,
            "dist_50w_pct": 0.0,
            "dist_100w_pct": 0.0,
            "dist_200w_pct": 0.0,
            "tier1_open": False,
            "tier2_open": False,
            "csp_lizard_zone": False,
            "gating_tier": "HOLD_CASH",
            "deploy_tranche_pct": 0,
        }

    spot = float(s.iloc[-1])
    if isinstance(s.index, pd.DatetimeIndex) and len(s) > 400:
        s_w = s.resample("W-FRI").last().dropna()
    else:
        s_w = s

    sma50w = float(s_w.rolling(50).mean().iloc[-1]) if len(s_w) >= 50 else float(s_w.mean())
    sma100w = float(s_w.rolling(100).mean().iloc[-1]) if len(s_w) >= 100 else (float(s_w.mean()) if len(s_w) > 50 else spot * 0.85)
    sma200w = float(s_w.rolling(200).mean().iloc[-1]) if len(s_w) >= 200 else (float(s_w.mean()) if len(s_w) > 50 else spot * 0.85)

    trig_100w = sma100w * buffer_ratio
    trig_200w = sma200w * buffer_ratio
    tier2_open = spot <= trig_200w
    tier1_open = spot <= trig_100w
    csp_zone = spot <= sma50w

    if tier2_open:
        gating_tier = "TIER2_BUY_100%"
        deploy_pct = 100
    elif tier1_open:
        gating_tier = "TIER1_BUY_50%"
        deploy_pct = 50
    elif csp_zone:
        gating_tier = "CSP_LIZARD_ZONE"
        deploy_pct = 0
    else:
        gating_tier = "HOLD_CASH"
        deploy_pct = 0

    return {
        "spot": round(spot, 2),
        "sma50w": round(sma50w, 2),
        "sma100w": round(sma100w, 2),
        "sma200w": round(sma200w, 2),
        "dist_50w_pct": round(((spot / (sma50w + 1e-9)) - 1.0) * 100.0, 1),
        "dist_100w_pct": round(((spot / (sma100w + 1e-9)) - 1.0) * 100.0, 1),
        "dist_200w_pct": round(((spot / (sma200w + 1e-9)) - 1.0) * 100.0, 1),
        "tier1_open": bool(tier1_open),
        "tier2_open": bool(tier2_open),
        "csp_lizard_zone": bool(csp_zone),
        "gating_tier": gating_tier,
        "deploy_tranche_pct": deploy_pct,
    }


def build_csp_jade_lizard_blueprint(
    ticker: str,
    spot: float,
    iv_pct: float,
    ivr_1y: Optional[float],
    tbill_rate: float = 0.042,
    dte: int = 30,
) -> Dict[str, Any]:
    """
    Computes 30-DTE options VRP blueprints via analytical Black-Scholes with skew:
      1. 25-Delta Cash-Secured Put (collateralized in T-bills)
      2. Zero-Upside-Risk Jade Lizard (Net Credit >= Call Spread Width)
      3. IVR >= 50 Gated Iron Condor (15-delta short strangle + 5-delta wings)
    """
    T = max(dte, 1) / 365.0
    sigma = max(0.14, (iv_pct or 28.0) / 100.0)
    sig_put = sigma + 0.025
    sig_call = max(0.12, sigma - 0.015)

    inc = 5.0 if spot >= 400 else (2.5 if spot >= 50 else 1.0)

    def _round_k(k: float) -> float:
        return round(k / inc) * inc

    # 1. 25-Delta Cash-Secured Put
    kp_25 = max(inc, _round_k(find_put_strike_by_delta_analytical(spot, T, tbill_rate, sig_put, 0.25, q=0.0)))
    csp_credit = round(max(0.05, bsm_put(spot, kp_25, T, tbill_rate, sig_put, q=0.0)["price"] * 0.94), 2)
    csp_ann_yield = round(((csp_credit / kp_25) * (365.0 / max(dte, 1)) + tbill_rate) * 100.0, 1)
    csp_ticket = f"SELL TO OPEN 1 {ticker} {dte} DTE ${kp_25:g} PUT @ LIMIT ${csp_credit:.2f} (T-Bill Collateral)"

    # 2. Zero-Upside-Risk Jade Lizard (25d Put + OTM Call Spread where Call Width <= Net Credit)
    kc_short = max(kp_25 + inc, _round_k(find_strike_by_delta_analytical(spot, T, tbill_rate, sig_call, 0.20, q=0.0)))
    call_width = max(inc, math.floor((csp_credit * 0.95) / inc) * inc)
    kc_long = kc_short + call_width
    cs_credit = max(
        0.05,
        bsm_call(spot, kc_short, T, tbill_rate, sig_call, q=0.0)["price"]
        - bsm_call(spot, kc_long, T, tbill_rate, sig_call, q=0.0)["price"],
    )
    jl_credit = round((csp_credit + cs_credit) * 0.96, 2)
    zero_upside_ok = bool(jl_credit >= call_width)
    jl_ann_yield = round(((jl_credit / kp_25) * (365.0 / max(dte, 1)) + tbill_rate) * 100.0, 1)
    jl_ticket = (
        f"JADE LIZARD {ticker} {dte} DTE: SELL ${kp_25:g}P + SELL ${kc_short:g}C / BUY ${kc_long:g}C "
        f"@ NET CREDIT ${jl_credit:.2f}"
    )

    # 3. IVR >= 50 Gated Iron Condor (15-delta short strangle + 5-delta wings)
    ic_active = bool(ivr_1y is not None and ivr_1y >= 50.0)
    ic_kp_s = max(2.0 * inc, _round_k(find_put_strike_by_delta_analytical(spot, T, tbill_rate, sig_put, 0.15, q=0.0)))
    ic_kp_l = max(inc, min(ic_kp_s - inc, _round_k(find_put_strike_by_delta_analytical(spot, T, tbill_rate, sig_put + 0.02, 0.05, q=0.0))))
    ic_kc_s = max(ic_kp_s + inc, _round_k(find_strike_by_delta_analytical(spot, T, tbill_rate, sig_call, 0.15, q=0.0)))
    ic_kc_l = max(ic_kc_s + inc, _round_k(find_strike_by_delta_analytical(spot, T, tbill_rate, sig_call, 0.05, q=0.0)))
    ic_credit = round(
        max(
            0.05,
            (
                bsm_put(spot, ic_kp_s, T, tbill_rate, sig_put, q=0.0)["price"]
                - bsm_put(spot, ic_kp_l, T, tbill_rate, sig_put + 0.02, q=0.0)["price"]
                + bsm_call(spot, ic_kc_s, T, tbill_rate, sig_call, q=0.0)["price"]
                - bsm_call(spot, ic_kc_l, T, tbill_rate, sig_call, q=0.0)["price"]
            )
            * 0.90,
        ),
        2,
    )
    ic_ticket = (
        f"IRON CONDOR {ticker} {dte} DTE: {ic_kp_l:g}/{ic_kp_s:g}P + {ic_kc_s:g}/{ic_kc_l:g}C "
        f"@ NET CREDIT ${ic_credit:.2f}"
    )

    return {
        "ticker": ticker,
        "spot": round(spot, 2),
        "dte": dte,
        "iv_proxy_pct": round(sigma * 100.0, 1),
        "ivr_1y": round(ivr_1y, 1) if ivr_1y is not None else None,
        "csp_25d": {
            "short_put": kp_25,
            "est_credit": csp_credit,
            "breakeven": round(kp_25 - csp_credit, 2),
            "ann_yield_incl_tbill_pct": csp_ann_yield,
            "ticket": csp_ticket,
        },
        "jade_lizard": {
            "short_put": kp_25,
            "short_call": kc_short,
            "long_call": kc_long,
            "call_width": call_width,
            "est_credit": jl_credit,
            "zero_upside_risk_valid": zero_upside_ok,
            "breakeven": round(kp_25 - jl_credit, 2),
            "ann_yield_incl_tbill_pct": jl_ann_yield,
            "ticket": jl_ticket,
        },
        "iron_condor": {
            "signal": "ACTIVE_OPEN_CONDOR" if ic_active else "WAIT_LOW_IVR",
            "put_spread": f"{ic_kp_l:g}/{ic_kp_s:g}P",
            "call_spread": f"{ic_kc_s:g}/{ic_kc_l:g}C",
            "est_credit": ic_credit,
            "management": "Close at 50% max profit or 21 DTE",
            "ticket": ic_ticket,
        },
    }


def fetch_yahoo_v8_chart(
    ticker: str,
    range_str: str = "5d",
    interval: str = "1d",
    timeout: float = 10.0,
) -> Tuple[pd.Series, Dict[str, Any]]:
    """Direct Yahoo Finance v8 Chart API fetcher (zero yfinance dependency fallback)."""
    try:
        import requests
    except ImportError:
        return pd.Series(dtype=float), {}

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_str}&interval={interval}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                results = r.json().get("chart", {}).get("result") or []
                if not results:
                    return pd.Series(dtype=float), {}
                res = results[0]
                meta = res.get("meta", {})
                ts = res.get("timestamp") or []
                if not ts:
                    return pd.Series(dtype=float), meta
                indicators = res.get("indicators", {})
                adj = indicators.get("adjclose")
                if adj and adj[0].get("adjclose"):
                    vals = adj[0]["adjclose"]
                else:
                    vals = indicators.get("quote", [{}])[0].get("close", [])
                idx = pd.to_datetime(ts, unit="s").tz_localize(None).normalize()
                s = pd.Series(vals, index=idx, name=ticker, dtype=float).dropna()
                s = s[~s.index.duplicated(keep="last")].sort_index()
                return s, meta
        except Exception:
            if attempt == 0:
                time.sleep(0.25)
    return pd.Series(dtype=float), {}


def fetch_vol_regime_snapshot() -> Dict[str, Any]:
    """Fetches live ^VIX, ^VXN (1Y IVR), and BIL 3M T-bill collateral yield via Yahoo v8."""
    snap: Dict[str, Any] = {
        "vix": None,
        "vix_ivr_1y": None,
        "vxn": None,
        "vxn_ivr_1y": None,
        "tbill_yield_pct": 4.2,
        "condor_regime": "WAIT (IVR < 50)",
    }
    for v_sym, key in [("^VIX", "vix"), ("^VXN", "vxn")]:
        vs, vm = fetch_yahoo_v8_chart(v_sym, range_str="1y", interval="1d", timeout=8.0)
        if len(vs) > 20:
            cur_v = float(vm.get("regularMarketPrice") or vs.iloc[-1])
            vmin, vmax = float(vs.min()), float(vs.max())
            v_ivr = ((cur_v - vmin) / (vmax - vmin)) * 100.0 if (vmax - vmin) > 1e-6 else 50.0
            snap[key] = round(cur_v, 2)
            snap[f"{key}_ivr_1y"] = round(float(np.clip(v_ivr, 0.0, 100.0)), 1)

    bil_s, _ = fetch_yahoo_v8_chart("BIL", range_str="6mo", interval="1d", timeout=8.0)
    if len(bil_s) >= 63:
        r_3m = float(bil_s.iloc[-1] / bil_s.iloc[-63] - 1.0)
        snap["tbill_yield_pct"] = round(max(0.0, r_3m * 4.0) * 100.0, 2)

    if snap["vix_ivr_1y"] is not None and snap["vix_ivr_1y"] >= 50.0:
        snap["condor_regime"] = "OPEN (IVR >= 50)"
    return snap


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


def _yahoo_v8_batch(tickers: List[str], range_str: str = "5d", interval: str = "1d") -> pd.DataFrame:
    """Fetches multi-ticker OHLCV close series concurrently via direct Yahoo Finance v8 API."""
    if not tickers:
        return pd.DataFrame()
    series_map: Dict[str, pd.Series] = {}

    def _fetch_one(sym: str) -> Tuple[str, pd.Series]:
        s, _ = fetch_yahoo_v8_chart(sym, range_str=range_str, interval=interval, timeout=8.0)
        return sym, s

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as pool:
        for sym, s in pool.map(_fetch_one, tickers):
            if s is not None and not s.empty:
                series_map[sym] = s

    if not series_map:
        return pd.DataFrame()
    df = pd.DataFrame(series_map).sort_index()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def refresh_live_intraday_prices(
    close_df: pd.DataFrame,
    gspc_close: Optional[pd.Series] = None,
    tickers: Optional[List[str]] = None,
    save_path: Optional[Path | str] = None,
) -> Tuple[pd.DataFrame, Optional[pd.Series]]:
    """
    Triggers a multi-threaded yfinance.download pull (period="5d", interval="1d")
    with automatic Yahoo Finance v8 Chart API fallback to append today's live session
    prices and any missing recent trading bars.
    Safely preserves multi-decade historical data and handles network fallbacks.
    """
    if tickers is None:
        tickers = [str(t) for t in close_df.columns if not str(t).startswith("^")]

    close_sub = pd.DataFrame()
    used_yfinance = False
    try:
        import yfinance as yf
        print(f"[CIRCUIT BREAKER] Refreshing live quotes for {len(tickers)} symbols via multi-threaded yfinance...")
        data = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if data is not None and not data.empty:
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
            used_yfinance = not close_sub.empty
    except Exception as e:
        print(f"[CIRCUIT BREAKER] yfinance refresh note ({e}); falling back to Yahoo Finance v8 API...")

    if close_sub.empty:
        if used_yfinance:
            print("[CIRCUIT BREAKER] yfinance returned empty data; trying Yahoo Finance v8 Chart API fallback...")
        else:
            print(f"[CIRCUIT BREAKER] Refreshing live quotes for {len(tickers)} symbols via Yahoo Finance v8 fallback...")
        try:
            close_sub = _yahoo_v8_batch(tickers, range_str="5d", interval="1d")
        except Exception as e_v8:
            print(f"[CIRCUIT BREAKER WARNING] Yahoo v8 fallback failed: {e_v8}. Retaining existing cache.")
            return close_df, gspc_close

    if close_sub.empty:
        print("[CIRCUIT BREAKER WARNING] No live quotes returned from yfinance or Yahoo v8. Retaining existing cache.")
        return close_df, gspc_close

    try:
        close_sub.index = pd.to_datetime(close_sub.index).tz_localize(None).normalize()
        # Reject incomplete after-hours bulk download bars (<50% ticker coverage)
        row_coverage = close_sub.notna().mean(axis=1)
        valid_rows = row_coverage >= 0.50
        if not valid_rows.all():
            skipped_dts = [d.strftime("%Y-%m-%d") for d in close_sub.index[~valid_rows]]
            print(f"[CIRCUIT BREAKER] Skipping sparse/incomplete market bars (<50% coverage): {', '.join(skipped_dts)}")
            close_sub = close_sub.loc[valid_rows]

        updated_close = close_df.copy()
        updated_close.index = pd.to_datetime(updated_close.index).normalize()
        while not updated_close.empty and float(updated_close.iloc[-1].notna().mean()) < 0.50:
            updated_close = updated_close.iloc[:-1]

        if close_sub.empty:
            print("[CIRCUIT BREAKER] No complete daily bar available yet; keeping latest full close.")
            return updated_close, gspc_close

        # Append new dates
        new_dates = close_sub.index.difference(updated_close.index)
        if len(new_dates) > 0:
            updated_close = pd.concat([updated_close, close_sub.loc[new_dates]], axis=0).sort_index()

        # Update overlapping dates with non-null values
        overlap_dates = close_sub.index.intersection(updated_close.index)
        if len(overlap_dates) > 0:
            updated_close.update(close_sub.loc[overlap_dates])

        while not updated_close.empty and float(updated_close.iloc[-1].notna().mean()) < 0.50:
            updated_close = updated_close.iloc[:-1]

        # Benchmark ^GSPC refresh (yfinance -> Yahoo v8 fallback)
        if gspc_close is not None:
            g_close = pd.Series(dtype=float)
            try:
                import yfinance as yf
                gspc_data = yf.download("^GSPC", period="5d", interval="1d", auto_adjust=True, progress=False)
                if gspc_data is not None and not gspc_data.empty:
                    g_close = gspc_data["Close"] if "Close" in gspc_data.columns else gspc_data.iloc[:, 0]
                    if isinstance(g_close, pd.DataFrame):
                        g_close = g_close.iloc[:, 0]
            except Exception:
                g_close = pd.Series(dtype=float)

            if g_close is None or g_close.empty:
                g_close, _ = fetch_yahoo_v8_chart("^GSPC", range_str="5d", interval="1d", timeout=8.0)

            if g_close is not None and not g_close.empty:
                g_close = g_close.dropna()
                g_close.index = pd.to_datetime(g_close.index).tz_localize(None).normalize()
                gspc_updated = gspc_close.copy()
                gspc_updated.index = pd.to_datetime(gspc_updated.index).tz_localize(None).normalize()
                g_new = g_close.index.difference(gspc_updated.index)
                if len(g_new) > 0:
                    gspc_updated = pd.concat([gspc_updated, g_close.loc[g_new]]).sort_index()
                gspc_updated.update(g_close)
                gspc_close = gspc_updated

        print(f"[CIRCUIT BREAKER] Successfully updated cache through {updated_close.index[-1].strftime('%Y-%m-%d')}.")

        if save_path:
            p = Path(save_path)
            try:
                save_df = updated_close.copy()
                # Preserve existing columns on disk if caller only refreshed a universe subset
                if p.exists():
                    try:
                        if p.suffix == ".xz":
                            with lzma.open(p, "rb") as rf:
                                prev_obj = pickle.load(rf)
                        elif p.suffix == ".gz":
                            with gzip.open(p, "rb") as rf:
                                prev_obj = pickle.load(rf)
                        else:
                            prev_obj = None
                        prev_df = prev_obj.get("close") if isinstance(prev_obj, dict) else (prev_obj if isinstance(prev_obj, pd.DataFrame) else None)
                        if isinstance(prev_df, pd.DataFrame) and not prev_df.empty:
                            prev_df = prev_df.copy()
                            prev_df.index = pd.to_datetime(prev_df.index).tz_localize(None).normalize()
                            while not prev_df.empty and float(prev_df.iloc[-1].notna().mean()) < 0.50:
                                prev_df = prev_df.iloc[:-1]
                            combined_idx = prev_df.index.union(save_df.index).sort_values()
                            merged_df = prev_df.reindex(combined_idx)
                            for col in save_df.columns:
                                merged_df[col] = save_df[col].reindex(combined_idx)
                            while not merged_df.empty and float(merged_df.iloc[-1].notna().mean()) < 0.50:
                                merged_df = merged_df.iloc[:-1]
                            save_df = merged_df
                    except Exception:
                        pass
                if gspc_close is not None:
                    g_ser = gspc_close.copy()
                    g_ser.index = pd.to_datetime(g_ser.index).tz_localize(None).normalize()
                    g_ser = g_ser[~g_ser.index.duplicated(keep="last")]
                    save_df["^GSPC"] = g_ser.reindex(save_df.index).ffill()
                if p.suffix == ".xz":
                    with lzma.open(p, "wb") as f:
                        pickle.dump({"close": save_df}, f)
                elif p.suffix == ".gz":
                    with gzip.open(p, "wb") as f:
                        pickle.dump({"close": save_df}, f)
            except Exception as e_save:
                print(f"[CIRCUIT BREAKER] Cache write note: {e_save}")

        return updated_close, gspc_close

    except Exception as e:
        print(f"[CIRCUIT BREAKER WARNING] Live refresh encountered an error: {e}. Retaining existing cache.")
        return close_df, gspc_close


# ==============================================================================
# SEC FORM 4 (10b5-1 IMMUNE), SCHEDULE 13D & FINVIZ INSIDER ENGINE (v2.4)
# ==============================================================================

_CSUITE_TITLES = {"CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL", "COO", "CHIEF OPERATING", "PRESIDENT", "CHAIRMAN"}
_SEC_TICKER_CIK_CACHE: Dict[str, str] = {}


def _is_csuite_role(pos_str: Any) -> bool:
    p = str(pos_str or "").upper()
    return any(t in p for t in _CSUITE_TITLES)


def parse_form4_xml(xml_text: str, exclude_10b5_1: bool = True) -> Dict[str, Any]:
    """
    Parses SEC Form 4 XML directly, isolating Open-Market Purchases (Code P)
    and Open-Market Sales (Code S), tagging C-suite roles, and checking the
    Rule 10b5-1 affirmative defense checkbox (<aff10b5One>: "1" or "true").
    """
    empty_res: Dict[str, Any] = {"ticker": "", "issuer": "", "is_10b5_1": False, "transactions": []}
    if not xml_text or not isinstance(xml_text, str):
        return empty_res
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return empty_res

    ticker = (root.findtext(".//issuer/issuerTradingSymbol") or "").strip().upper()
    issuer = (root.findtext(".//issuer/issuerName") or "").strip()

    owner_names: List[str] = []
    roles: List[str] = []
    csuite_hit = False
    for ro in root.findall(".//reportingOwner"):
        nm = (ro.findtext(".//reportingOwnerId/rptOwnerName") or "").strip()
        if nm:
            owner_names.append(nm)
        title = (ro.findtext(".//reportingOwnerRelationship/officerTitle") or "").strip()
        is_dir = (ro.findtext(".//reportingOwnerRelationship/isDirector") or "").strip().lower() in ("1", "true")
        is_10 = (ro.findtext(".//reportingOwnerRelationship/isTenPercentOwner") or "").strip().lower() in ("1", "true")
        r_str = title if title else ("10% Owner" if is_10 else ("Director" if is_dir else "Insider"))
        roles.append(r_str)
        if _is_csuite_role(r_str):
            csuite_hit = True

    owner_name = owner_names[0] if owner_names else "Unknown"
    role = roles[0] if roles else "Insider"
    is_10b5_1 = (root.findtext(".//aff10b5One") or "0").strip().lower() in ("1", "true")

    if exclude_10b5_1 and is_10b5_1:
        return {"ticker": ticker, "issuer": issuer, "is_10b5_1": True, "transactions": []}

    txs: List[Dict[str, Any]] = []
    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        code = (tx.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
        if code not in {"P", "S"}:
            continue
        tx_date = (tx.findtext(".//transactionDate/value") or "").strip()
        try:
            shares = float(tx.findtext(".//transactionAmounts/transactionShares/value") or 0.0)
            price = float(tx.findtext(".//transactionAmounts/transactionPricePerShare/value") or 0.0)
        except (TypeError, ValueError):
            continue
        dollar_val = shares * price
        if not (math.isfinite(dollar_val) and dollar_val > 0.0):
            continue
        txs.append({
            "code": code,
            "insider": owner_name,
            "role": role,
            "is_c_suite": csuite_hit,
            "is_10b5_1": is_10b5_1,
            "tx_date": tx_date,
            "shares": round(shares, 4),
            "price": round(price, 4),
            "dollar_value": float(dollar_val),
        })

    return {"ticker": ticker, "issuer": issuer, "is_10b5_1": is_10b5_1, "transactions": txs}


def _fetch_ticker_form4_xmls_direct(
    ticker: str,
    lookback_days: int = 180,
    email_header: str = "QuantResearch quant@mungersystem.org",
    max_filings: int = 35,
) -> Tuple[List[Tuple[str, str]], bool]:
    """
    Direct SEC EDGAR Submissions API fallback fetching raw Form 4 XML filings
    without requiring edgartools. Returns ([(filing_date, xml_text), ...], success).
    """
    try:
        import requests
    except ImportError:
        return [], False

    headers = {"User-Agent": email_header, "Accept-Encoding": "gzip, deflate"}
    global _SEC_TICKER_CIK_CACHE
    if not _SEC_TICKER_CIK_CACHE:
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=10)
            if r.status_code == 200:
                payload = r.json()
                for entry in payload.values():
                    tkr = str(entry.get("ticker", "")).upper().strip()
                    cik_str = str(entry.get("cik_str", "")).strip()
                    if tkr and cik_str:
                        _SEC_TICKER_CIK_CACHE[tkr] = cik_str.zfill(10)
        except Exception:
            return [], False

    t_up = ticker.upper().strip()
    cik = (
        _SEC_TICKER_CIK_CACHE.get(t_up)
        or _SEC_TICKER_CIK_CACHE.get(t_up.replace("-", "."))
        or _SEC_TICKER_CIK_CACHE.get(t_up.replace(".", "-"))
    )
    if not cik:
        return [], False

    try:
        sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        sr = requests.get(sub_url, headers=headers, timeout=10)
        if sr.status_code != 200:
            return [], False
        recent = sr.json().get("filings", {}).get("recent", {})
    except Exception:
        return [], False

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    adshs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cutoff_str = (pd.Timestamp.now() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    xml_results: List[Tuple[str, str]] = []
    cik_int = int(cik)
    for form, fdate, adsh, pdoc in zip(forms, dates, adshs, docs):
        if form != "4":
            continue
        if fdate and fdate < cutoff_str:
            break
        raw_xml_name = str(pdoc or "").split("/")[-1]
        if not (adsh and raw_xml_name.lower().endswith(".xml")):
            continue
        adsh_nodash = adsh.replace("-", "")
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{adsh_nodash}/{raw_xml_name}"
        try:
            xr = requests.get(xml_url, headers=headers, timeout=8)
            if xr.status_code == 200 and "<ownershipDocument" in xr.text:
                xml_results.append((str(fdate or ""), xr.text))
        except Exception:
            pass
        time.sleep(0.12)
        if len(xml_results) >= max_filings:
            break

    return xml_results, True


def score_insider_transactions(
    transactions: List[Dict[str, Any]],
    exclude_10b5_1: bool = True,
    reference_date: Optional[Any] = None,
    source: str = "SEC EDGAR Form 4",
) -> Dict[str, Any]:
    """
    Aggregates Form 4 Code P / Code S transactions into conviction multipliers,
    isolating discretionary open-market activity from Rule 10b5-1 scheduled plans,
    tagging C-suite open-market buys, and tracking Day 1-5 alpha-decay recency.
    """
    disc_buys = [t for t in transactions if t.get("code") == "P" and not t.get("is_10b5_1")]
    plan_buys = [t for t in transactions if t.get("code") == "P" and t.get("is_10b5_1")]
    disc_sales = [t for t in transactions if t.get("code") == "S" and not t.get("is_10b5_1")]
    plan_sales = [t for t in transactions if t.get("code") == "S" and t.get("is_10b5_1")]

    planned_buys_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in plan_buys))
    planned_sales_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in plan_sales))
    planned_csuite_sales_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in plan_sales if t.get("is_c_suite")))
    disc_sales_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in disc_sales))
    disc_csuite_sales_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in disc_sales if t.get("is_c_suite")))

    active_buys = disc_buys if exclude_10b5_1 else (disc_buys + plan_buys)
    active_sales = disc_sales if exclude_10b5_1 else (disc_sales + plan_sales)

    buys_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in active_buys))
    buyers = list(dict.fromkeys(str(t.get("insider", "")) for t in active_buys if t.get("insider")))
    num_buyers = len(buyers)

    csuite_buys = [t for t in active_buys if t.get("is_c_suite")]
    csuite_buys_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in csuite_buys))
    csuite_buyers = list(dict.fromkeys(str(t.get("insider", "")) for t in csuite_buys if t.get("insider")))
    csuite_buy_hit = bool(csuite_buyers and csuite_buys_usd > 0.0)

    sales_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in active_sales))
    sellers = list(dict.fromkeys(str(t.get("insider", "")) for t in active_sales if t.get("insider")))
    num_sellers = len(sellers)

    csuite_sales = [t for t in active_sales if t.get("is_c_suite")]
    csuite_sales_usd = float(sum(float(t.get("dollar_value", 0.0)) for t in csuite_sales))
    csuite_sellers = list(dict.fromkeys(str(t.get("insider", "")) for t in csuite_sales if t.get("insider")))

    # Recency window tracking (Day 1-5 alpha decay window)
    ref_dt = pd.to_datetime(reference_date).normalize() if reference_date is not None else pd.Timestamp.now().normalize()
    buy_dates: List[pd.Timestamp] = []
    for t in active_buys:
        dt_str = t.get("tx_date") or t.get("file_date")
        if dt_str:
            parsed_dt = pd.to_datetime(dt_str, errors="coerce")
            if pd.notna(parsed_dt):
                buy_dates.append(parsed_dt.normalize())

    latest_buy_date: Optional[str] = None
    latest_buy_age_days: Optional[int] = None
    fresh_buy_window = "NONE"
    if buy_dates:
        max_buy_dt = max(buy_dates)
        latest_buy_date = max_buy_dt.strftime("%Y-%m-%d")
        latest_buy_age_days = max(0, (ref_dt.date() - max_buy_dt.date()).days)
        if latest_buy_age_days <= 5:
            fresh_buy_window = "FRESH_5D"
        elif latest_buy_age_days <= 30:
            fresh_buy_window = "RECENT_30D"
        else:
            fresh_buy_window = "STALE"

    fresh_tag = (
        f" [FRESH {latest_buy_age_days}d]" if fresh_buy_window == "FRESH_5D"
        else (f" [RECENT {latest_buy_age_days}d]" if fresh_buy_window == "RECENT_30D" else "")
    )

    if (
        (num_buyers >= 2 and buys_usd >= 250_000.0)
        or (csuite_buy_hit and csuite_buys_usd >= 250_000.0 and sales_usd == 0.0)
        or (buys_usd >= 2_000_000.0 and sales_usd == 0.0)
    ):
        multiplier = 1.15
        signal = "CLUSTER_BUY_BOOST"
        csuite_note = f" (C-Suite: {', '.join(csuite_buyers[:3])})" if csuite_buyers else ""
        notes = f"Cluster Buying Boost (1.15x){fresh_tag}: {num_buyers} insider(s) bought ${buys_usd:,.0f} on open market{csuite_note}."
    elif csuite_sales_usd >= 20_000_000.0 and buys_usd == 0.0:
        multiplier = 0.70
        signal = "CSUITE_DISTRIBUTION_HAIRCUT"
        notes = (
            f"Heavy C-Suite Distribution Haircut (0.70x): C-suite sold ${csuite_sales_usd:,.0f} "
            f"with zero buys ({', '.join(csuite_sellers[:3])})."
        )
    else:
        multiplier = 1.00
        signal = "NEUTRAL"
        notes = f"Neutral conviction (Buys: ${buys_usd:,.0f}, Sales: ${sales_usd:,.0f})."

    if exclude_10b5_1 and planned_csuite_sales_usd > 0.0:
        notes += f" (Excluded ${planned_csuite_sales_usd:,.0f} Rule 10b5-1 planned C-suite sales)"

    return {
        "multiplier": multiplier,
        "signal": signal,
        "buys_usd": buys_usd,
        "sales_usd": sales_usd,
        "csuite_sales_usd": csuite_sales_usd,
        "num_buyers": num_buyers,
        "num_sellers": num_sellers,
        "buyers": buyers,
        "sellers": sellers,
        "csuite_sellers": csuite_sellers,
        "csuite_buys_usd": csuite_buys_usd,
        "csuite_buyers": csuite_buyers,
        "csuite_buy_hit": csuite_buy_hit,
        "discretionary_sales_usd": disc_sales_usd,
        "discretionary_csuite_sales_usd": disc_csuite_sales_usd,
        "planned_10b5_buys_usd": planned_buys_usd,
        "planned_10b5_sales_usd": planned_sales_usd,
        "planned_10b5_csuite_sales_usd": planned_csuite_sales_usd,
        "latest_buy_date": latest_buy_date,
        "latest_buy_age_days": latest_buy_age_days,
        "fresh_buy_window": fresh_buy_window,
        "exclude_10b5_1": exclude_10b5_1,
        "source": source,
        "notes": notes,
    }


def fetch_live_insider_activity(
    tickers: List[str] | str,
    lookback_days: int = 180,
    email_header: str = "QuantResearch quant@mungersystem.org",
    exclude_10b5_1: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """
    Queries SEC EDGAR Form 4 XML filings via edgartools -> direct SEC submissions
    XML fallback -> finvizfinance tertiary fallback.
    Strictly isolates discretionary Open-Market Purchases (Code P) and Open-Market
    Sales (Code S), discarding noise (Codes F, M, A, G) and stripping predetermined
    Rule 10b5-1 trading plans when `exclude_10b5_1=True`.
    """
    if isinstance(tickers, str):
        tickers = [tickers]

    results: Dict[str, Dict[str, Any]] = {}
    cutoff_dt = pd.Timestamp.now() - pd.Timedelta(days=lookback_days)

    for ticker in tickers:
        t_clean = ticker.upper().strip()
        all_txs: List[Dict[str, Any]] = []
        used_edgar = False
        source_label = "None"

        # 1. Primary Source: SEC EDGAR Form 4 XML via edgartools (captures <aff10b5One>)
        try:
            from edgar import set_identity, Company
            set_identity(email_header)
            c = Company(t_clean)
            filings = c.get_filings(form="4")
            if filings and len(filings) > 0:
                recent_filings = filings[:35]
                parsed_any = False
                for f in recent_filings:
                    try:
                        f_date_str = getattr(f, "filing_date", None)
                        if f_date_str:
                            f_date = pd.to_datetime(f_date_str)
                            if f_date < cutoff_dt:
                                break
                        xml_txt = f.xml() if hasattr(f, "xml") else ""
                        if isinstance(xml_txt, str) and "<ownershipDocument" in xml_txt:
                            parsed = parse_form4_xml(xml_txt, exclude_10b5_1=False)
                            for tx in parsed["transactions"]:
                                tx["file_date"] = str(f_date_str or "")
                                all_txs.append(tx)
                            parsed_any = True
                            continue
                        obj = f.obj()
                        if hasattr(obj, "to_dataframe"):
                            df = obj.to_dataframe()
                            if df is not None and not df.empty:
                                df["Value"] = pd.to_numeric(df["Value"], errors="coerce").fillna(0.0)
                                for _, r in df.iterrows():
                                    code = str(r.get("Code", "")).strip().upper()
                                    val = float(r.get("Value", 0.0))
                                    if code not in {"P", "S"} or val <= 0.0:
                                        continue
                                    pos = str(r.get("Position", "") or "")
                                    all_txs.append({
                                        "code": code,
                                        "insider": str(r.get("Insider", "Unknown") or "Unknown"),
                                        "role": pos or "Insider",
                                        "is_c_suite": _is_csuite_role(pos),
                                        "is_10b5_1": False,
                                        "tx_date": str(r.get("Date", f_date_str or "") or ""),
                                        "file_date": str(f_date_str or ""),
                                        "shares": float(pd.to_numeric(r.get("Shares", 0.0), errors="coerce") or 0.0),
                                        "price": float(pd.to_numeric(r.get("Price", 0.0), errors="coerce") or 0.0),
                                        "dollar_value": val,
                                    })
                                parsed_any = True
                    except Exception:
                        continue
                if parsed_any:
                    used_edgar = True
                    source_label = "SEC EDGAR Form 4"
        except Exception:
            pass

        # 2. Secondary Fallback: Direct SEC EDGAR Submissions XML
        if not used_edgar:
            xml_pairs, direct_ok = _fetch_ticker_form4_xmls_direct(
                t_clean, lookback_days=lookback_days, email_header=email_header, max_filings=35
            )
            if direct_ok:
                used_edgar = True
                source_label = "SEC EDGAR Form 4 (Direct XML)"
                for fdate, xml_txt in xml_pairs:
                    parsed = parse_form4_xml(xml_txt, exclude_10b5_1=False)
                    for tx in parsed["transactions"]:
                        tx["file_date"] = fdate
                        all_txs.append(tx)

        # 3. Tertiary Fallback: finvizfinance (only if both SEC EDGAR paths failed)
        used_finviz = False
        if not used_edgar:
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
                    for idx_r, r in inside_df.iterrows():
                        tr_type = str(trans_col.loc[idx_r])
                        val = float(val_col.loc[idx_r])
                        if tr_type not in {"buy", "sale"} or val <= 0.0:
                            continue
                        rel = str(r.get("Relationship", "") or "")
                        dt_val = r.get("_dt")
                        dt_str = dt_val.strftime("%Y-%m-%d") if pd.notna(dt_val) else ""
                        all_txs.append({
                            "code": "P" if tr_type == "buy" else "S",
                            "insider": str(r.get("Insider Trading", "Unknown") or "Unknown"),
                            "role": rel or "Insider",
                            "is_c_suite": _is_csuite_role(rel),
                            "is_10b5_1": False,
                            "tx_date": dt_str,
                            "file_date": dt_str,
                            "shares": 0.0,
                            "price": 0.0,
                            "dollar_value": val,
                        })
                    used_finviz = True
                    source_label = "Finviz Insider"
            except Exception:
                pass

        scored = score_insider_transactions(
            all_txs,
            exclude_10b5_1=exclude_10b5_1,
            source=source_label,
        )
        if not used_edgar and not used_finviz and not all_txs:
            scored["notes"] = "No qualifying insider activity detected."
        elif used_finviz:
            scored["notes"] += " [Finviz fallback: Rule 10b5-1 flags unavailable]"

        data_res = {"ticker": t_clean, **scored}
        results[t_clean] = data_res

    return results


def fetch_schedule_13d_catalysts(
    universe_tickers: Optional[Iterable[str]] = None,
    lookback_days: int = 30,
    email_header: str = "QuantResearch quant@mungersystem.org",
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Queries SEC EDGAR EFTS for recent Schedule 13D activist filings and intersects
    against `universe_tickers` (when provided) to filter out closed-end funds & microcaps.
    """
    try:
        import requests
    except ImportError:
        return {}

    end_dt = pd.Timestamp.now().strftime("%Y-%m-%d")
    start_dt = (pd.Timestamp.now() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    url = (
        f"https://efts.sec.gov/LATEST/search-index?forms=SCHEDULE%2013D"
        f"&dateRange=custom&startdt={start_dt}&enddt={end_dt}&from=0&size=100"
    )
    headers = {"User-Agent": email_header, "Accept-Encoding": "gzip, deflate"}
    universe_set = {str(t).upper().strip() for t in universe_tickers} if universe_tickers is not None else None

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return {}
        hits = r.json().get("hits", {}).get("hits", [])
    except Exception:
        return {}

    catalysts: Dict[str, List[Dict[str, Any]]] = {}
    for h in hits:
        src = h.get("_source", {})
        names = src.get("display_names", [])
        target = str(names[0]) if len(names) >= 1 else ""
        filer = str(names[1]) if len(names) >= 2 else "Activist Filer"
        target_clean = re.sub(r"\(\s*CIK\s+\d+\s*\)", "", target, flags=re.IGNORECASE)
        matches = re.findall(r"\(([A-Z0-9.,\s-]{1,24})\)", target_clean)
        found_tickers: List[str] = []
        for grp in matches:
            for part in grp.split(","):
                cand = part.strip().upper()
                if cand and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,5}", cand):
                    found_tickers.append(cand)
        if not found_tickers:
            continue

        for tk in found_tickers:
            if universe_set is not None and tk not in universe_set:
                continue
            catalysts.setdefault(tk, []).append({
                "file_date": str(src.get("file_date", "")),
                "form": str(src.get("form", "SCHEDULE 13D")),
                "target": target.strip(),
                "filer": filer.strip(),
                "adsh": str(src.get("adsh", "")),
            })

    return catalysts


# ==============================================================================
# FIDELITY TAX-LOT RECONCILER (v2.2)
# ==============================================================================

FIDELITY_CASH_SWEEP_SYMBOLS = {
    "SPAXX", "FDRXX", "FCASH", "FDLXX", "SPRXX", "FZCXX", "FZDXX", "FZFXX",
    "FNSXX", "FTEXX", "FMPXX", "VMFXX", "VMMXX", "VMSXX", "SWVXX", "SNVXX",
    "CORE", "CASH", "USD", "HELD IN CASH", "HELD_IN_CASH", "PENDING ACTIVITY"
}

BROAD_INDEX_ETF_SYMBOLS = {
    "SPY", "VOO", "IVV", "VTI", "ITOT", "SCHB", "QQQ", "QQQM", "DIA", "IWM",
    "IJH", "IJR", "MDY", "EFA", "IEFA", "VEA", "VEU", "VXUS", "IXUS", "EEM",
    "IEMG", "VWO", "SCHD", "VIG", "VYM", "DGRO", "AGG", "BND", "TLT", "IEF",
    "SHY", "SGOV", "BIL", "LQD", "HYG", "GLD", "IAU", "SLV", "VNQ", "XLK",
    "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"
}


def _is_cash_or_sweep(clean_sym: str, desc_upper: str) -> bool:
    if clean_sym in FIDELITY_CASH_SWEEP_SYMBOLS:
        return True
    if len(clean_sym) == 5 and clean_sym.endswith("XX"):
        return True
    if any(k in desc_upper for k in ("MONEY MARKET", "MMKT", "HELD IN FCASH", "CASH RESERVES", "PENDING ACTIVITY")):
        return True
    return False


def _classify_holding_asset_type(clean_sym: str, desc_upper: str) -> str:
    """Returns 'EQUITY', 'FIXED_INCOME_CD', 'RETIREMENT_TRUST', or 'INDEX_ETF'."""
    is_cusip = len(clean_sym) == 9 and any(c.isdigit() for c in clean_sym)
    if is_cusip:
        if any(k in desc_upper for k in (" CD ", "CERT OF DEP", "TREAS", "BOND", "NOTE", "%")):
            return "FIXED_INCOME_CD"
        return "RETIREMENT_TRUST"
    if any(k in desc_upper for k in ("INDEX 20", "TD T", "TARGET DATE", "COLLEGE", "529")):
        return "RETIREMENT_TRUST"
    if clean_sym in BROAD_INDEX_ETF_SYMBOLS or any(k in desc_upper for k in (" ETF", "ISHARES", "VANGUARD", "SPDR", "INVESCO QQQ", "INDEX FD")):
        return "INDEX_ETF"
    return "EQUITY"


def _is_tax_advantaged_account(acc_name: str) -> bool:
    u = str(acc_name or "").upper()
    return any(k in u for k in (
        "IRA", "ROTH", "ROLLOVER", "401K", "401(K)", "403B", "403(B)",
        "529", "HSA", "SAVINGS PLAN", "PENSION", "KEOGH", "SEP", "SIMPLE", "COLLEGE"
    ))


def parse_fidelity_positions_csv(
    csv_path: Optional[Path | str] = None,
    live_prices: Optional[Dict[str, float] | pd.Series] = None,
    eval_date: Optional[pd.Timestamp | str] = None,
    munger_scores: Optional[Dict[str, float] | pd.Series] = None,
    immunized_flags: Optional[Dict[str, bool] | pd.Series] = None,
) -> pd.DataFrame:
    """
    Parses an exported Fidelity Portfolio_Positions_*.csv file (supporting both
    Title Case and Fidelity.com's native sentence-case headers):
      - Discovers file automatically across repo root or data/ if csv_path is None
      - Strips money-market sweep positions (SPAXX**, FDRXX**, FCASH**, FZDXX, etc.)
      - Distinguishes single-stock equities from Fixed-Income CDs, 401k/529 Trusts, and Index ETFs
      - Distinguishes Taxable accounts from Tax-Advantaged accounts (IRA, Roth IRA, 401k, 529)
      - Replaces stale CSV export prices with live quotes (falling back to CSV price/value)
      - Computes holding duration against the 365-day LTCG tax hurdle
      - Reconciles tax lots with Munger Factor criteria, outputting exact execution orders:
          * STRONG HOLD / ADD: High-conviction compounder in Strike Zone
          * HOLD FOR TAX DEFERRAL (Xd to LTCG): Taxable short-term gain with sub-hurdle score;
            defers sale until LTCG reclassification to avoid ordinary income tax drag
          * SELL (MOC): Position is either LTCG, tax-advantaged (IRA/Roth), or has an unrealized
            loss in a taxable account (immediate tax-loss harvest with zero tax drag)
    """
    if csv_path is None:
        base_dir = Path(__file__).resolve().parent
        candidate_paths = (
            list(Path(".").glob("Portfolio_Positions*.csv"))
            + list(Path("data").glob("Portfolio_Positions*.csv"))
            + list((base_dir / "data").glob("Portfolio_Positions*.csv"))
        )
        # Prefer Portfolio_Positions_latest.csv if present, otherwise newest mtime
        latest_named = [p for p in candidate_paths if p.name.lower() == "portfolio_positions_latest.csv"]
        if latest_named:
            candidate_paths = [latest_named[0]]
        if not candidate_paths:
            candidate_paths = list(Path(".").glob("*Positions*.csv")) + list(Path("data").glob("*Positions*.csv"))
        if candidate_paths:
            non_samples = [
                p for p in candidate_paths
                if "sample" not in p.name.lower() and "latest" not in p.name.lower() and not p.name.startswith("_")
            ]
            targets = non_samples if non_samples else candidate_paths
            targets.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            csv_path = targets[0]
        else:
            return pd.DataFrame()

    path_obj = Path(csv_path)
    if not path_obj.exists():
        alt_obj = Path(__file__).resolve().parent / csv_path
        if alt_obj.exists():
            path_obj = alt_obj
        else:
            return pd.DataFrame()

    with open(path_obj, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()

    header_idx = -1
    for idx, line in enumerate(lines):
        line_low = line.lower()
        if "symbol" in line_low and ("quantity" in line_low or "current value" in line_low or "cost basis" in line_low or "account name" in line_low):
            header_idx = idx
            break

    if header_idx == -1:
        return pd.DataFrame()

    reader = csv.DictReader(lines[header_idx:])

    if eval_date is None:
        dt_eval = pd.Timestamp.now()
    else:
        dt_eval = pd.to_datetime(eval_date)

    def clean_num(v: Any) -> float:
        if v is None or v == "" or pd.isna(v):
            return 0.0
        s = str(v).replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip()
        if not s or s.lower() in ("--", "n/a", "nan", "none", "pending"):
            return 0.0
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        try:
            return float(s)
        except ValueError:
            return 0.0

    rows = []
    for r in reader:
        if not r:
            continue
        r_low = {str(k).strip().lower(): v for k, v in r.items() if k is not None}

        raw_sym = str(r_low.get("symbol") or "").strip().upper()
        if not raw_sym or raw_sym in ("NONE", "NAN") or raw_sym.startswith("*") or "TOTAL" in raw_sym or "DISCLAIMER" in raw_sym or "PENDING" in raw_sym:
            continue

        clean_sym = raw_sym.replace("*", "").strip()
        desc_raw = str(r_low.get("description") or "").strip()
        desc_upper = desc_raw.upper()

        if _is_cash_or_sweep(clean_sym, desc_upper):
            continue

        shares = clean_num(r_low.get("quantity", 0))
        if shares <= 0:
            continue

        csv_cur_val = clean_num(r_low.get("current value", 0))
        csv_cost_basis = clean_num(r_low.get("cost basis total", 0))
        if csv_cost_basis <= 0.0:
            avg_basis = clean_num(r_low.get("average cost basis", 0))
            if avg_basis > 0.0:
                csv_cost_basis = shares * avg_basis
        if csv_cost_basis <= 0.0 and csv_cur_val > 0.0:
            total_gl = clean_num(r_low.get("total gain/loss dollar", 0))
            if total_gl != 0.0 and (csv_cur_val - total_gl) > 0.0:
                csv_cost_basis = csv_cur_val - total_gl
            else:
                csv_cost_basis = csv_cur_val

        # Account metadata
        acc_name = str(r_low.get("account name") or "").strip()
        acc_num = str(r_low.get("account number") or "").strip()
        account_label = acc_name if acc_name else (acc_num if acc_num else "Default")
        is_tax_advantaged = _is_tax_advantaged_account(account_label)

        # Date acquired
        date_str = str(r_low.get("date acquired") or r_low.get("acquisition date") or "").strip()
        if date_str and date_str.lower() not in ("--", "n/a", "pending", "none", ""):
            try:
                acq_date = pd.to_datetime(date_str)
                days_held = max(0, (dt_eval.date() - acq_date.date()).days)
            except Exception:
                days_held = 365
        else:
            days_held = 365

        is_ltcg = True if is_tax_advantaged else (days_held >= 365)
        days_to_ltcg = 0 if is_tax_advantaged else max(0, 365 - days_held)
        ltcg_status = "TAX-EXEMPT" if is_tax_advantaged else ("LTCG" if is_ltcg else "STCG")

        # Overlay live price
        live_price = 0.0
        if live_prices is not None:
            if isinstance(live_prices, dict) and clean_sym in live_prices:
                live_price = float(live_prices[clean_sym])
            elif hasattr(live_prices, "loc") and clean_sym in live_prices.index:
                val = live_prices.loc[clean_sym]
                live_price = float(val) if pd.notna(val) else 0.0

        if live_price <= 0.0:
            live_price = clean_num(r_low.get("last price") or r_low.get("current price") or 0)
        if live_price <= 0.0 and csv_cur_val > 0.0 and shares > 0.0:
            live_price = csv_cur_val / shares

        # Fixed-income CDs report price per $100 par (e.g. 99.669 with Quantity 40,000 -> Current value $39,867.60)
        asset_type = _classify_holding_asset_type(clean_sym, desc_upper)
        if asset_type == "RETIREMENT_TRUST":
            is_tax_advantaged = True
            is_ltcg = True
            days_to_ltcg = 0
            ltcg_status = "TAX-EXEMPT"
        if asset_type == "FIXED_INCOME_CD" and csv_cur_val > 0.0:
            live_val = csv_cur_val
            live_price = csv_cur_val / shares
        else:
            live_val = shares * live_price if live_price > 0.0 else csv_cur_val

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

        # Action directives by asset class & tax regime
        if asset_type == "FIXED_INCOME_CD":
            action = "HOLD (FIXED INCOME / CD)"
            rationale = f"Fixed-income certificate/bond ({desc_raw[:42]}). Hold to maturity; exempt from single-stock hurdle."
        elif asset_type == "RETIREMENT_TRUST":
            action = "HOLD (PLAN / TARGET DATE)"
            rationale = f"Plan trust / target-date allocation ({desc_raw[:42]}). Core plan holding; exempt from single-stock hurdle."
        elif asset_type == "INDEX_ETF":
            action = "HOLD (INDEX ETF)"
            rationale = f"Broad index ETF allocation ({desc_raw[:42] or clean_sym}). Diversified core sleeve; exempt from single-stock hurdle."
        elif m_score >= 2.0 and passed_hurdle:
            action = "STRONG HOLD / ADD"
            rationale = f"Tier-1 Compounder (M={m_score:.2f}). Intact compounding moat; add on pullbacks."
        elif is_tax_advantaged:
            action = "SELL (MOC)"
            rationale = f"Tax-advantaged account ({account_label}): zero capital-gains tax friction. Sub-hurdle score (M={m_score:.2f}); reallocate to Strike Zone."
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
            "Account": account_label,
            "Asset_Type": asset_type,
            "Symbol": clean_sym,
            "Shares": shares,
            "Cost_Basis": round(csv_cost_basis, 2),
            "Live_Price": round(live_price, 2),
            "Live_Value": round(live_val, 2),
            "Unrealized_Gain": round(unrealized_gain, 2),
            "Gain_Pct": round(gain_pct, 1),
            "Days_Held": days_held,
            "LTCG_Status": ltcg_status,
            "Days_to_LTCG": days_to_ltcg,
            "Munger_Score": round(m_score, 2),
            "Action": action,
            "Rationale": rationale,
        })

    return pd.DataFrame(rows)
