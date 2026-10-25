#!/usr/bin/env python3
"""
================================================================================
DOWNLOAD S&P 900 HISTORICAL DAILY PRICES (2001 - 2026)
================================================================================
Downloads daily adjusted close prices for all 903 S&P 900 constituents plus ^GSPC
benchmark from 2001-01-01 to 2026-10-21 using chunked multi-threaded yfinance.
================================================================================
"""

import json
import lzma
import pickle
import time
from pathlib import Path
from typing import List

import pandas as pd
import yfinance as yf

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"


def main():
    t_start = time.time()
    print("=" * 80)
    print(" DOWNLOADING S&P 900 HISTORICAL PRICES: 2001 TO NOW")
    print("=" * 80)

    with open(DATA_DIR / "sp900_tickers.json", "r", encoding="utf-8") as f:
        sp900_tickers = json.load(f)

    all_download_tickers = list(dict.fromkeys(sp900_tickers + ["^GSPC", "SPY"]))
    print(f"Total Symbols to pull (S&P 900 + Benchmarks): {len(all_download_tickers)}")

    chunk_size = 120
    chunks = [all_download_tickers[i:i + chunk_size] for i in range(0, len(all_download_tickers), chunk_size)]
    print(f"Executing in {len(chunks)} parallel chunks (chunk size: {chunk_size})...\n")

    chunk_dfs = []
    for idx, chunk in enumerate(chunks, start=1):
        c_start = time.time()
        print(f"[{idx}/{len(chunks)}] Pulling chunk of {len(chunk)} tickers (e.g. {chunk[0]}..{chunk[-1]})...")
        try:
            data = yf.download(
                tickers=chunk,
                start="2001-01-01",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if data is not None and not data.empty:
                if isinstance(data.columns, pd.MultiIndex):
                    if "Close" in data.columns.levels[0]:
                        c_df = data["Close"].copy()
                    else:
                        c_df = data.xs("Close", axis=1, level=0).copy()
                elif "Close" in data.columns:
                    c_df = data[["Close"]].copy()
                    if len(chunk) == 1:
                        c_df.columns = [chunk[0]]
                else:
                    c_df = data.copy()

                c_df.index = pd.to_datetime(c_df.index).normalize()
                chunk_dfs.append(c_df)
                print(f"    -> Retrieved {c_df.shape[1]} symbols over {len(c_df)} bars ({time.time() - c_start:.1f}s)")
            else:
                print(f"    [WARN] Empty response for chunk {idx}")
        except Exception as e:
            print(f"    [ERROR] Chunk {idx} failed: {e}")

    if not chunk_dfs:
        raise RuntimeError("No price data retrieved from yfinance.")

    print("\nMerging and aligning all downloaded price matrices...")
    merged_close = pd.concat(chunk_dfs, axis=1)
    merged_close = merged_close.loc[:, ~merged_close.columns.duplicated()]
    merged_close = merged_close.sort_index()

    print(f"Full S&P 900 Matrix Shape: {merged_close.shape}")
    print(f"Date Range: {merged_close.index[0].strftime('%Y-%m-%d')} to {merged_close.index[-1].strftime('%Y-%m-%d')}")

    # Ensure ^GSPC and SPY are preserved
    if "^GSPC" not in merged_close.columns:
        print("Explicitly downloading ^GSPC benchmark...")
        g_data = yf.download("^GSPC", start="2001-01-01", auto_adjust=True, progress=False)
        if g_data is not None and not g_data.empty:
            g_close = g_data["Close"] if "Close" in g_data.columns else g_data.iloc[:, 0]
            g_close.index = pd.to_datetime(g_close.index).normalize()
            merged_close["^GSPC"] = g_close

    # Save dedicated 2001-2026 dataset
    sp900_file = DATA_DIR / "sp900_prices_2001_2026.pkl.xz"
    print(f"Saving S&P 900 dataset to {sp900_file.name}...")
    with lzma.open(sp900_file, "wb", preset=6) as f:
        pickle.dump({"close": merged_close}, f)

    # Also merge into master historical_prices_1970_2026.pkl.xz
    master_file = DATA_DIR / "historical_prices_1970_2026.pkl.xz"
    if master_file.exists():
        print(f"Updating master cache {master_file.name}...")
        with lzma.open(master_file, "rb") as f:
            master_obj = pickle.load(f)
        if isinstance(master_obj, dict) and "close" in master_obj:
            master_df = master_obj["close"].copy()
        else:
            master_df = master_obj.copy()

        master_df.index = pd.to_datetime(master_df.index).normalize()
        # Combine existing and new columns
        combined = pd.concat([master_df, merged_close.reindex(master_df.index)], axis=1)
        combined = combined.loc[:, ~combined.columns.duplicated()]
        # Update overlapping dates with fresh prices
        combined.update(merged_close)

        with lzma.open(master_file, "wb", preset=6) as f:
            pickle.dump({"close": combined}, f)
        print(f"  -> Master cache updated: {combined.shape}")

    elapsed = time.time() - t_start
    print("=" * 80)
    print(f" DOWNLOAD COMPLETE IN {elapsed:.1f}s")
    print("=" * 80)


if __name__ == "__main__":
    main()
