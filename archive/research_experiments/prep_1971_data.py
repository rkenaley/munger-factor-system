import json
import pickle
import os
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures

def run_1971_prep():
    with open('data/sp100_nasdaq100_universe.json', 'r') as f:
        universe = json.load(f)
    tickers = list(universe.keys())
    if '^GSPC' not in tickers:
        tickers.append('^GSPC')
    if 'SPY' not in tickers:
        tickers.append('SPY')
        
    print(f"Total tickers to inspect: {len(tickers)}")
    cache_path = 'data/historical_prices_1970_2026.pkl'
    
    if os.path.exists(cache_path):
        print("Loading cached 1970-2026 prices...")
        with open(cache_path, 'rb') as f:
            all_prices = pickle.load(f)
    else:
        print("Downloading all available data from 1970-01-01 to 2026-10-12...")
        all_prices = {}
        
        # Download in chunks of 25 tickers
        chunk_size = 25
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i+chunk_size]
            print(f"Downloading chunk {i//chunk_size + 1}/{(len(tickers)+chunk_size-1)//chunk_size} ({len(chunk)} tickers)...")
            try:
                data = yf.download(chunk, start="1970-01-01", end="2026-10-12", group_by='ticker', auto_adjust=False, progress=False)
                for t in chunk:
                    try:
                        if len(chunk) == 1:
                            sub = data
                        else:
                            sub = data[t]
                        sub = sub.dropna(subset=['Close'])
                        if not sub.empty:
                            all_prices[t] = sub
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error on chunk: {e}")
                
        with open(cache_path, 'wb') as f:
            pickle.dump(all_prices, f)
            print(f"Saved {len(all_prices)} price series to {cache_path}")
            
    stats = {}
    for t, df in all_prices.items():
        if not df.empty:
            stats[t] = df.index[0].year
            
    print(f"\nTotal tickers in database: {len(all_prices)}")
    by_decade = {
        "Pre-1980": sum(1 for y in stats.values() if y < 1980),
        "1980-1989": sum(1 for y in stats.values() if 1980 <= y < 1990),
        "1990-1999": sum(1 for y in stats.values() if 1990 <= y < 2000),
        "2000-2009": sum(1 for y in stats.values() if 2000 <= y < 2010),
        "2010-present": sum(1 for y in stats.values() if y >= 2010)
    }
    print("Ticker Inception Distribution:")
    for k, v in by_decade.items():
        print(f"  {k:15s}: {v} tickers")
        
    print("\nPre-1980 tickers available:", [t for t, y in stats.items() if y < 1980])

if __name__ == '__main__':
    run_1971_prep()
