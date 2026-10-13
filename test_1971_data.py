import yfinance as yf
import pandas as pd
import json

test_tickers = ['^GSPC', 'IBM', 'GE', 'DIS', 'BA', 'MO', 'PG', 'JNJ', 'MMM', 'WMT', 'INTC', 'KO', 'CAT', 'XOM', 'CVX', 'AAPL', 'MSFT', 'TXN', 'LLY', 'MCD', 'PEP', 'F', 'GM', 'PFE', 'UNP']

print("Testing historical data availability back to 1970/1971:")
results = {}
for t in test_tickers:
    try:
        df = yf.download(t, start='1970-01-01', end='2026-10-12', progress=False)
        if not df.empty:
            start_date = str(df.index[0].date())
            end_date = str(df.index[-1].date())
            results[t] = {"start": start_date, "end": end_date, "rows": len(df)}
            print(f"{t:6s} | Start: {start_date} | Rows: {len(df)}")
        else:
            print(f"{t:6s} | EMPTY")
    except Exception as e:
        print(f"{t:6s} | Error: {e}")
