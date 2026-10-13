import pickle
import pandas as pd
import numpy as np
import json

def run_1971_study():
    with open('data/historical_prices_1970_2026.pkl', 'rb') as f:
        all_prices = pickle.load(f)
        
    gspc = all_prices.get('^GSPC')
    if gspc is None or gspc.empty:
        print("Error: ^GSPC not found.")
        return
        
    print(f"^GSPC daily rows: {len(gspc)}, Start: {gspc.index[0].date()}, End: {gspc.index[-1].date()}")
    
    # Identify tickers with long history (>= 30 years and >= 50 years)
    tickers_50y = []
    tickers_40y = []
    tickers_30y = []
    
    for t, df in all_prices.items():
        if t in ['^GSPC', 'SPY']:
            continue
        if df.empty:
            continue
        start_year = df.index[0].year
        if start_year <= 1975:
            tickers_50y.append(t)
        if start_year <= 1985:
            tickers_40y.append(t)
        if start_year <= 1995:
            tickers_30y.append(t)
            
    print(f"Tickers with >= 50 years history (pre-1975): {len(tickers_50y)}")
    print(f"Tickers with >= 40 years history (pre-1985): {len(tickers_40y)}")
    print(f"Tickers with >= 30 years history (pre-1995): {len(tickers_30y)}")
    
    # Analyze historical market drawdowns and recoveries since 1971 on S&P 500
    # Regimes:
    # 1. 1971-1981: Stagflation & Volcker Era (1973-74 Bear Market)
    # 2. 1982-1999: Great Disinflation & Bull Market (1987 Black Monday, 1990s Expansion)
    # 3. 2000-2007: Tech Bubble Burst & Pre-GFC Cycle
    # 4. 2008-2022: Great Financial Crisis to Post-COVID Tightening
    # 5. 2023-2026: Generative AI Infrastructure Supercycle
    
    regimes = [
        ("1971-1981: Stagflation & Volcker Shock", "1971-01-01", "1981-12-31"),
        ("1982-1999: Great Bull Market & Disinflation", "1982-01-01", "1999-12-31"),
        ("2000-2007: Dot-Com Crash & Housing Cycle", "2000-01-01", "2007-12-31"),
        ("2008-2022: GFC, ZIRP & Fed Tightening", "2008-01-01", "2022-12-31"),
        ("2023-2026: Generative AI Expansion", "2023-01-01", "2026-10-12"),
    ]
    
    print("\n=== MACROECONOMIC REGIMES (S&P 500 BENCHMARK: 1971-2026) ===")
    regime_stats = []
    for name, start, end in regimes:
        sub = gspc.loc[start:end]['Close']
        if sub.empty:
            continue
        start_px = sub.iloc[0]
        end_px = sub.iloc[-1]
        years = (sub.index[-1] - sub.index[0]).days / 365.25
        ann_ret = ((end_px / start_px) ** (1.0 / years) - 1.0) * 100.0
        
        # Max drawdown
        cummax = sub.cummax()
        dd = (sub - cummax) / cummax
        max_dd = dd.min() * 100.0
        
        regime_stats.append({
            "Regime": name,
            "Start": str(sub.index[0].date()),
            "End": str(sub.index[-1].date()),
            "Years": round(years, 1),
            "Start Px": round(start_px, 2),
            "End Px": round(end_px, 2),
            "Ann Return": f"{ann_ret:+.2f}%",
            "Max Drawdown": f"{max_dd:.1f}%"
        })
        
    df_reg = pd.DataFrame(regime_stats)
    print(df_reg.to_string(index=False))
    
    with open('data/regimes_1971_2026.json', 'w') as f:
        json.dump(regime_stats, f, indent=2)

if __name__ == '__main__':
    run_1971_study()
