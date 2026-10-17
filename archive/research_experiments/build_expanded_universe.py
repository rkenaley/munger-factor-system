"""Build and cache the expanded S&P 100 + Nasdaq 100 universe fundamentals."""

import io
import json
import urllib.request
import concurrent.futures
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_FILE = DATA_DIR / "sp100_nasdaq100_universe.json"

def fetch_constituents():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # 1. S&P 100
    try:
        req1 = urllib.request.Request('https://en.wikipedia.org/wiki/S%26P_100', headers=headers)
        sp100_df = [t for t in pd.read_html(io.StringIO(urllib.request.urlopen(req1).read().decode('utf-8'))) if 'Symbol' in t.columns and len(t) > 80][0]
        sp_map = {r['Symbol'].replace('.', '-'): {'name': str(r.get('Name', r['Symbol'])), 'sector': str(r.get('Sector', 'Large Cap'))} for _, r in sp100_df.iterrows()}
    except Exception as e:
        print(f"Error fetching SP100: {e}")
        sp_map = {}

    # 2. Nasdaq 100
    try:
        req2 = urllib.request.Request('https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies', headers=headers)
        ndx_df = pd.read_html(io.StringIO(urllib.request.urlopen(req2).read().decode('utf-8')))[0]
        ndx_map = {r['Ticker'].replace('.', '-'): {'name': str(r.get('Company', r['Ticker'])), 'sector': str(r.get('ICB Industry[1]', 'Technology'))} for _, r in ndx_df.iterrows()}
    except Exception as e:
        print(f"Error fetching NDX: {e}")
        ndx_map = {}

    # 3. User Thematic & Infrastructure Portfolio Holdings
    thematic_map = {
        'TSM': {'name': 'Taiwan Semiconductor Manufacturing', 'sector': 'Technology'},
        'GRID': {'name': 'First Trust Smart Grid Infrastructure ETF', 'sector': 'Infrastructure'},
        'ARE': {'name': 'Alexandria Real Estate Equities', 'sector': 'Real Estate'},
        'DTCR': {'name': 'Global X Data Center REITs ETF', 'sector': 'Real Estate'},
        'BEPC': {'name': 'Brookfield Renewable Corp', 'sector': 'Utilities'},
        'CWEN': {'name': 'Clearway Energy Inc', 'sector': 'Utilities'},
        'LYTE': {'name': 'Roundhill Photonics ETF', 'sector': 'Technology'},
        'IONQ': {'name': 'IonQ Inc', 'sector': 'Technology'},
        'NBIS': {'name': 'Nebius Group N.V.', 'sector': 'Technology'},
        'SCHD': {'name': 'Schwab US Dividend Equity ETF', 'sector': 'Large Value'},
        'VTI': {'name': 'Vanguard Total Stock Market ETF', 'sector': 'Blend'}
    }

    combined = {}
    combined.update(sp_map)
    combined.update(ndx_map)
    combined.update(thematic_map)

    # Clean ticker symbols (e.g. BRK.B -> BRK-B)
    clean_combined = {}
    for k, v in combined.items():
        clean_k = k.replace('.', '-').strip()
        clean_combined[clean_k] = v

    return clean_combined


def fetch_single_ticker_info(ticker: str, meta: dict) -> dict:
    try:
        t_obj = yf.Ticker(ticker)
        info = t_obj.info or {}
        
        # P/E
        pe = info.get('trailingPE')
        if pe is None or pe <= 0:
            pe = info.get('forwardPE')
        if pe is None or pe <= 0:
            pe = 25.0
            
        # P/B
        pb = info.get('priceToBook')
        if pb is None or pb <= 0:
            pb = 4.0
            
        # D/E
        de = info.get('debtToEquity')
        if de is None or de < 0:
            de = 50.0
            
        # Sales Growth (convert to percentage)
        rev_g = info.get('revenueGrowth')
        if rev_g is not None:
            sales5y = float(rev_g) * 100.0
        else:
            sales5y = 12.0
            
        # ROIC proxy
        roe = info.get('returnOnEquity')
        roa = info.get('returnOnAssets')
        op_margin = info.get('operatingMargins')
        
        if roe is not None and roe > 0:
            roic_proxy = float(roe)
        elif roa is not None and roa > 0:
            roic_proxy = float(roa) * 1.5
        elif op_margin is not None and op_margin > 0:
            roic_proxy = float(op_margin)
        else:
            roic_proxy = 0.15

        # FCF margin
        fcf = info.get('freeCashflow')
        rev = info.get('totalRevenue')
        if fcf is not None and rev is not None and rev > 0:
            fcf_margin = float(fcf) / float(rev)
        else:
            prof_m = info.get('profitMargins')
            fcf_margin = float(prof_m) if prof_m is not None else 0.18

        return {
            'ticker': ticker,
            'name': meta.get('name', ticker),
            'sector': meta.get('sector', 'Equities'),
            'theme': meta.get('sector', 'General'),
            'pe': round(float(pe), 2),
            'pb': round(float(pb), 2),
            'de': round(float(de), 2),
            'sales5y': round(float(sales5y), 2),
            'roic_proxy': round(float(roic_proxy), 3),
            'fcf_margin': round(float(fcf_margin), 3)
        }
    except Exception as e:
        return {
            'ticker': ticker,
            'name': meta.get('name', ticker),
            'sector': meta.get('sector', 'Equities'),
            'theme': meta.get('sector', 'General'),
            'pe': 25.0,
            'pb': 4.0,
            'de': 50.0,
            'sales5y': 12.0,
            'roic_proxy': 0.15,
            'fcf_margin': 0.18
        }


def build_and_save_universe():
    print(" [*] Fetching constituent lists for S&P 100 + Nasdaq 100 + Portfolio Holdings...")
    constituents = fetch_constituents()
    print(f" [+] Total Unique Universe Tickers: {len(constituents)}")
    
    print(" [*] Ingesting fundamental statistics via multithreaded yfinance...")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_ticker = {
            executor.submit(fetch_single_ticker_info, t, meta): t 
            for t, meta in constituents.items()
        }
        for future in concurrent.futures.as_completed(future_to_ticker):
            t = future_to_ticker[future]
            try:
                data = future.result()
                results[t] = data
            except Exception as e:
                print(f"Failed {t}: {e}")

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n [SUCCESS] Cached fundamental profiles for {len(results)} companies to {CACHE_FILE}")
    return results


if __name__ == "__main__":
    build_and_save_universe()
