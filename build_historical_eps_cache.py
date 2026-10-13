"""Build Historical Reported EPS Cache for 2002-2026.

Extracts point-in-time reported quarterly EPS from yfinance for all universe tickers
and saves to data/historical_quarterly_eps.json.
"""

import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf

def fetch_ticker_eps(ticker: str):
    try:
        tk = yf.Ticker(ticker)
        ed = tk.get_earnings_dates(limit=100)
        if ed is not None and not ed.empty and 'Reported EPS' in ed.columns:
            s = ed['Reported EPS'].dropna()
            # Convert timestamp to YYYY-MM-DD
            d = {}
            for dt, val in s.items():
                date_str = dt.strftime('%Y-%m-%d')
                try:
                    v = float(val)
                    if -100.0 < v < 1000.0: # filter aberrant data artifacts
                        d[date_str] = v
                except Exception:
                    pass
            return ticker, d
    except Exception as e:
        pass
    return ticker, {}

def main():
    data_file = Path("data/sp100_nasdaq100_universe.json")
    with open(data_file, "r", encoding="utf-8") as f:
        fund_dict = json.load(f)
        
    tickers = [t for t in fund_dict.keys() if t not in ['ROOTS', 'CRYPTO', 'T-Bills', 'VTI / VT']]
    print(f"Fetching point-in-time quarterly EPS for {len(tickers)} tickers...")
    
    eps_db = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_ticker_eps, t): t for t in tickers}
        completed = 0
        for fut in as_completed(futures):
            t, d = fut.result()
            if d:
                eps_db[t] = d
            completed += 1
            if completed % 25 == 0 or completed == len(tickers):
                print(f"Progress: {completed}/{len(tickers)} tickers completed. (With EPS data: {len(eps_db)})")
                
    out_file = Path("data/historical_quarterly_eps.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(eps_db, f, indent=2)
        
    print(f"Successfully saved {len(eps_db)} tickers with quarterly EPS to {out_file}")

if __name__ == "__main__":
    main()
