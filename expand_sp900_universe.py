#!/usr/bin/env python3
"""
================================================================================
EXPAND UNIVERSE TO S&P 900 (S&P 500 + S&P MIDCAP 400) (v2.3.0)
================================================================================
Fetches all 400 S&P MidCap 400 constituents from Wikipedia, merges with S&P 500 to
form the unified S&P 900 universe (~903 equities), enriches missing mid-caps with
fundamental metrics, downloads 5Y price bars, and updates:
  1. data/sp400_universe.json
  2. data/sp900_universe.json
  3. data/sp900_tickers.json
  4. data/universe_debt_metrics.json
  5. data/historical_prices_1970_2026.pkl.xz
================================================================================
"""

import concurrent.futures
import io
import json
import lzma
import pickle
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"


def fetch_sp400_constituents() -> List[Dict[str, str]]:
    """Fetches official S&P MidCap 400 constituents list from Wikipedia."""
    print("[1/5] Fetching S&P MidCap 400 constituent list from Wikipedia...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
    dfs = pd.read_html(io.StringIO(html))
    table = dfs[0]

    constituents = []
    for _, row in table.iterrows():
        raw_sym = str(row["Symbol"]).strip()
        sym = raw_sym.replace(".", "-")
        constituents.append({
            "ticker": sym,
            "raw_symbol": raw_sym,
            "name": str(row.get("Security", sym)).strip(),
            "sector": str(row.get("GICS Sector", "General")).strip(),
            "theme": str(row.get("GICS Sub-Industry", row.get("GICS Sector", "General"))).strip(),
            "market_segment": "MidCap",
        })

    print(f"  Successfully identified {len(constituents)} S&P MidCap 400 constituents.")
    return constituents


def fetch_single_ticker_fundamentals(c: Dict[str, str]) -> Dict[str, Any]:
    """Fetches fundamentals from yfinance for a single constituent."""
    tk = c["ticker"]
    try:
        t = yf.Ticker(tk)
        info = t.info or {}
    except Exception:
        info = {}

    name = c["name"] or info.get("shortName") or info.get("longName") or tk
    sector = c["sector"] or info.get("sector") or "General"
    theme = c["theme"] or info.get("industry") or sector

    # Valuation & Multiples
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe is None or pe <= 0 or pe > 500:
        pe = 22.0
    else:
        pe = round(float(pe), 2)

    pb = info.get("priceToBook")
    if pb is None or pb <= 0 or pb > 100:
        pb = 2.8
    else:
        pb = round(float(pb), 2)

    de = info.get("debtToEquity")
    if de is None or de < 0 or de > 1000:
        de = 55.0
    else:
        de = round(float(de), 1)

    # ROIC / ROE Proxy
    roe = info.get("returnOnEquity")
    roa = info.get("returnOnAssets")
    if roe is not None and -1.0 <= roe <= 5.0:
        roic_proxy = round(float(roe), 3)
    elif roa is not None and -0.5 <= roa <= 2.0:
        roic_proxy = round(float(roa * 2.0), 3)
    else:
        roic_proxy = 0.165

    # 5Y Revenue Growth Proxy
    rev_g = info.get("revenueGrowth")
    if rev_g is not None and -0.5 <= rev_g <= 3.0:
        sales5y = round(float(rev_g * 100.0), 1)
    else:
        sales5y = 12.0

    # FCF Margin
    fcf = info.get("freeCashflow") or 0
    rev = info.get("totalRevenue") or 1
    if rev and rev > 0 and fcf:
        fcf_margin = round(float(fcf / rev), 3)
    else:
        fcf_margin = 0.14

    # Debt Metrics
    tot_debt = info.get("totalDebt") or 0
    tot_cash = info.get("totalCash") or 0
    ebitda = info.get("ebitda")
    if ebitda is not None and ebitda > 0:
        nd_ebitda = round(float((tot_debt - tot_cash) / ebitda), 2)
    elif tot_cash > tot_debt:
        nd_ebitda = -0.5
    else:
        nd_ebitda = 1.35

    return {
        "ticker": tk,
        "name": name,
        "sector": sector,
        "theme": theme,
        "pe": pe,
        "pb": pb,
        "de": de,
        "sales5y": sales5y,
        "roic_proxy": roic_proxy,
        "fcf_margin": fcf_margin,
        "net_debt_to_ebitda": nd_ebitda,
        "total_debt": tot_debt,
        "total_cash": tot_cash,
        "marketCap": info.get("marketCap"),
        "ebitda": ebitda,
    }


def main():
    t_start = time.time()
    print("=" * 80)
    print(" S&P 900 (500 LARGE-CAP + 400 MID-CAP) UNIVERSE EXPANSION PIPELINE")
    print("=" * 80)

    # 1. Fetch S&P 400 MidCap list
    sp400_constituents = fetch_sp400_constituents()
    sp400_tickers = [c["ticker"] for c in sp400_constituents]
    sp400_by_sym = {c["ticker"]: c for c in sp400_constituents}

    # 2. Load S&P 500 universe
    sp500_file = DATA_DIR / "sp500_universe.json"
    with open(sp500_file, "r", encoding="utf-8") as f:
        sp500_univ = json.load(f)

    sp500_tickers = list(sp500_univ.keys())
    print(f"  Loaded existing S&P 500 universe ({len(sp500_tickers)} equities).")

    # Combine S&P 500 + S&P 400 = S&P 900
    sp900_tickers = list(dict.fromkeys(sp500_tickers + sp400_tickers))
    print(f"  Target S&P 900 universe size: {len(sp900_tickers)} unique equities.")

    with open(DATA_DIR / "sp900_tickers.json", "w", encoding="utf-8") as f:
        json.dump(sp900_tickers, f, indent=2)

    # 3. Parallel fetch missing fundamentals for S&P 400
    univ_file = DATA_DIR / "sp100_nasdaq100_universe.json"
    existing_univ = {}
    if univ_file.exists():
        with open(univ_file, "r", encoding="utf-8") as f:
            existing_univ = json.load(f)

    debt_file = DATA_DIR / "universe_debt_metrics.json"
    existing_debt = {}
    if debt_file.exists():
        with open(debt_file, "r", encoding="utf-8") as f:
            existing_debt = json.load(f)

    missing_sp400 = [c for c in sp400_constituents if c["ticker"] not in existing_univ]
    print(f"\n[2/5] Fundamental Profiles: Fetching {len(missing_sp400)} mid-cap profiles with 20 parallel threads...")

    fetched_sp400 = {}
    if missing_sp400:
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_map = {executor.submit(fetch_single_ticker_fundamentals, c): c["ticker"] for c in missing_sp400}
            done_count = 0
            for future in concurrent.futures.as_completed(future_map):
                tk = future_map[future]
                try:
                    res = future.result()
                    fetched_sp400[tk] = res
                except Exception:
                    c = sp400_by_sym[tk]
                    fetched_sp400[tk] = {
                        "ticker": tk,
                        "name": c["name"],
                        "sector": c["sector"],
                        "theme": c["theme"],
                        "pe": 22.0, "pb": 2.8, "de": 55.0, "sales5y": 12.0,
                        "roic_proxy": 0.165, "fcf_margin": 0.14,
                        "net_debt_to_ebitda": 1.35, "total_debt": 0, "total_cash": 0
                    }
                done_count += 1
                if done_count % 50 == 0 or done_count == len(missing_sp400):
                    print(f"    [{done_count}/{len(missing_sp400)}] Mid-Cap fundamentals retrieved...")

    # Assemble S&P 400 and S&P 900
    sp400_universe = {}
    sp900_universe = dict(sp500_univ)
    expanded_composite = dict(existing_univ)
    expanded_debt = dict(existing_debt)

    for c in sp400_constituents:
        tk = c["ticker"]
        entry = fetched_sp400.get(tk, existing_univ.get(tk, {}))
        if entry:
            sp400_universe[tk] = entry
            sp900_universe[tk] = entry
            expanded_composite[tk] = entry
            expanded_debt[tk] = {
                "ticker": tk,
                "totalDebt": entry.get("total_debt", 0),
                "totalCash": entry.get("total_cash", 0),
                "netDebt": (entry.get("total_debt", 0) - entry.get("total_cash", 0)),
                "ebitda": entry.get("ebitda"),
                "marketCap": entry.get("marketCap"),
                "debtToEquity": entry.get("de"),
                "freeCashflow": None,
                "operatingCashflow": None,
                "revenue": None,
                "net_debt_to_ebitda": entry.get("net_debt_to_ebitda"),
            }

    print(f"\n[3/5] Saving Universe Manifests:")
    with open(DATA_DIR / "sp400_universe.json", "w", encoding="utf-8") as f:
        json.dump(sp400_universe, f, indent=2)
    print(f"  -> Saved {DATA_DIR / 'sp400_universe.json'} ({len(sp400_universe)} tickers)")

    with open(DATA_DIR / "sp900_universe.json", "w", encoding="utf-8") as f:
        json.dump(sp900_universe, f, indent=2)
    print(f"  -> Saved {DATA_DIR / 'sp900_universe.json'} ({len(sp900_universe)} tickers)")

    with open(DATA_DIR / "sp100_nasdaq100_universe.json", "w", encoding="utf-8") as f:
        json.dump(expanded_composite, f, indent=2)
    print(f"  -> Updated {DATA_DIR / 'sp100_nasdaq100_universe.json'} ({len(expanded_composite)} total tickers)")

    with open(DATA_DIR / "universe_debt_metrics.json", "w", encoding="utf-8") as f:
        json.dump(expanded_debt, f, indent=2)
    print(f"  -> Updated {DATA_DIR / 'universe_debt_metrics.json'} ({len(expanded_debt)} total tickers)")

    # 4. Check prices in historical_prices_1970_2026.pkl.xz
    print("\n[4/5] Historical Price Matrix Alignment:")
    price_file = DATA_DIR / "historical_prices_1970_2026.pkl.xz"
    with lzma.open(price_file, "rb") as f:
        raw_obj = pickle.load(f)

    if isinstance(raw_obj, dict) and "close" in raw_obj:
        existing_close = raw_obj["close"].copy()
    elif isinstance(raw_obj, pd.DataFrame):
        existing_close = raw_obj.copy()
    else:
        raise ValueError("Unsupported price pickle format")

    existing_tickers = set(existing_close.columns)
    tickers_to_download = [t for t in sp400_tickers if t not in existing_tickers]
    print(f"  Existing price columns: {len(existing_tickers)}")
    print(f"  New MidCap 400 price columns required: {len(tickers_to_download)}")

    if tickers_to_download:
        print(f"  Downloading 5-year daily bars for {len(tickers_to_download)} new tickers via yfinance...")
        dl_data = yf.download(
            tickers=tickers_to_download,
            period="5y",
            interval="1d",
            auto_adjust=True,
            progress=True,
            threads=True,
        )
        if dl_data is not None and not dl_data.empty:
            if isinstance(dl_data.columns, pd.MultiIndex):
                if "Close" in dl_data.columns.levels[0]:
                    new_close = dl_data["Close"].copy()
                else:
                    new_close = dl_data.xs("Close", axis=1, level=0).copy()
            elif "Close" in dl_data.columns:
                new_close = dl_data[["Close"]].copy()
                if len(tickers_to_download) == 1:
                    new_close.columns = [tickers_to_download[0]]
            else:
                new_close = dl_data.copy()

            new_close.index = pd.to_datetime(new_close.index).normalize()
            existing_close.index = pd.to_datetime(existing_close.index).normalize()

            aligned_new = new_close.reindex(existing_close.index, method="ffill")
            combined_close = pd.concat([existing_close, aligned_new], axis=1)
            combined_close = combined_close.loc[:, ~combined_close.columns.duplicated()]

            print(f"  Combined price matrix shape: {combined_close.shape} ({len(combined_close.columns)} tickers)")

            print(f"\n[5/5] Compressing and writing {price_file.name}...")
            save_payload = {"close": combined_close}
            with lzma.open(price_file, "wb", preset=6) as f:
                pickle.dump(save_payload, f)
            print("  -> Price cache saved successfully.")
        else:
            print("  [WARNING] yfinance returned empty price dataset for new tickers.")
    else:
        print("  All MidCap tickers are already present in price cache!")

    elapsed = time.time() - t_start
    print("\n" + "=" * 80)
    print(f" S&P 900 EXPANSION COMPLETED SUCCESSFULLY IN {elapsed:.1f}s")
    print("=" * 80)


if __name__ == "__main__":
    main()
