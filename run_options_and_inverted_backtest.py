"""Options Leverage & Inverted Overbought Put Backtester.

Evaluates:
1. Long Call Strategies on the Extremized Multiplier (>= 12x) & +1s/2s signals:
   - Deep ITM 80-Delta LEAPS (Capital Efficient Stock Replacement)
   - Bull Call Vertical Spreads (60/30 Delta, 90 DTE)
   - Benchmark vs Outright Common Stock
2. Inverted Screener (Overvalued + Overbought + Decelerating) & Put Strategies:
   - Fragility Factors: PE >= 80th percentile, RSI >= 70, Price > 20% over 200d SMA, Sales Growth <= Median
   - Bear Put Vertical Spreads (50/25 Delta, 60 DTE)
   - Outright Long Puts (40 Delta, 60 DTE)
"""

import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

# Standard Normal CDF approximation for Black-Scholes
def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def bs_call_price(S, K, T, r, sigma):
    if T <= 0.001:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)

def bs_put_price(S, K, T, r, sigma):
    if T <= 0.001:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def run_options_and_inverted_analysis():
    data_file = Path("data/sp100_nasdaq100_universe.json")
    with open(data_file, "r", encoding="utf-8") as f:
        fund_dict = json.load(f)
        
    tickers = [t for t in fund_dict.keys() if t not in ['ROOTS', 'CRYPTO', 'T-Bills', 'VTI / VT']]
    print(f"Loaded {len(tickers)} universe tickers.")

    # Download price history
    prices_df = yf.download(tickers + ['SPY'], period="2y", interval="1d", progress=False)['Close']
    if prices_df.empty:
        print("Failed to download price history.")
        return

    dates = prices_df.index
    base_prices = prices_df.iloc[-1]
    base_eps = {}
    base_bvps = {}
    for t in tickers:
        if t in base_prices and pd.notna(base_prices[t]) and t in fund_dict:
            p = float(base_prices[t])
            pe = fund_dict[t].get('pe', 25.0)
            pb = fund_dict[t].get('pb', 4.0)
            base_eps[t] = p / (pe if pe > 0 else 25.0)
            base_bvps[t] = p / (pb if pb > 0 else 4.0)

    # Precalculate 200-day SMA and 14-day RSI for all tickers
    sma200_df = prices_df.rolling(window=200).mean()
    
    # Calculate RSI 14
    delta = prices_df.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi14_df = 100 - (100 / (1 + rs))

    # Realized historical volatility (30-day rolling)
    log_ret = np.log(prices_df / prices_df.shift(1))
    vol30_df = log_ret.rolling(window=30).std() * math.sqrt(252)

    risk_free_rate = 0.045 # 4.5% Fed Funds

    long_calls_results = []
    inverted_puts_results = []

    # Weekly evaluation loop starting after 200 trading days
    sample_indices = range(200, len(dates) - 63, 5)

    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]

        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {}

        for t in tickers:
            if t in row_prices and pd.notna(row_prices[t]) and t in base_eps and base_eps[t] > 0:
                p = float(row_prices[t])
                pe = p / base_eps[t]
                if 2.0 <= pe <= 250.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    curr_sales[t] = fund_dict[t].get('sales5y', 12.0)
                    curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                    raw_de = fund_dict[t].get('de', 50.0)
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))

        if len(curr_pes) < 15:
            continue

        valid_t = list(curr_pes.keys())
        sales_vals = [curr_sales[t] for t in valid_t]
        roic_vals = [curr_roic[t] for t in valid_t]
        ey_vals = [curr_ey[t] for t in valid_t]
        de_inv_vals = [curr_de_inv[t] for t in valid_t]
        pes_vals = [curr_pes[t] for t in valid_t]

        s_mean, s_std, s_med = float(np.mean(sales_vals)), float(np.std(sales_vals)), float(np.median(sales_vals))
        r_mean, r_std, r_med = float(np.mean(roic_vals)), float(np.std(roic_vals)), float(np.median(roic_vals))
        e_mean, e_std, e_med = float(np.mean(ey_vals)), float(np.std(ey_vals)), float(np.median(ey_vals))
        d_mean, d_std, d_med = float(np.mean(de_inv_vals)), float(np.std(de_inv_vals)), float(np.median(de_inv_vals))
        p_q75 = float(np.percentile(pes_vals, 75))
        p_q85 = float(np.percentile(pes_vals, 85))

        for t in valid_t:
            S0 = float(row_prices[t])
            pe = curr_pes[t]
            sales = curr_sales[t]
            roic = curr_roic[t]
            ey = curr_ey[t]
            de_inv = curr_de_inv[t]
            
            # Underlying forward returns
            S_3m = float(prices_df[t].iloc[idx + 63])
            stock_ret_3m = (S_3m / S0) - 1.0

            # Volatility estimate
            vol = float(vol30_df[t].loc[dt]) if t in vol30_df and pd.notna(vol30_df[t].loc[dt]) else 0.35
            vol = max(0.18, min(0.75, vol)) # Bound between 18% and 75%

            # -------------------------------------------------------------
            # 1. LONG SIGNAL: Multiplier >= 12.0x
            # -------------------------------------------------------------
            all_above_med = (sales > s_med and roic > r_med and ey > e_med and de_inv > d_med)
            zs_med = max(0.0, (sales - s_med) / (s_std if s_std > 0 else 1.0))
            zr_med = max(0.0, (roic - r_med) / (r_std if r_std > 0 else 1.0))
            ze_med = max(0.0, (ey - e_med) / (e_std if e_std > 0 else 1.0))
            zd_med = max(0.0, (de_inv - d_med) / (d_std if d_std > 0 else 1.0))
            composite_multiplier = (1.0 + zs_med) * (1.0 + zr_med) * (1.0 + ze_med) * (1.0 + zd_med)

            if all_above_med and composite_multiplier >= 12.0:
                # Option Strategy A: Deep ITM 80-Delta LEAPS (250 DTE, Strike = 0.85 * S0)
                # At entry: T0 = 250 / 365, Strike = 0.85 * S0
                K_leaps = 0.85 * S0
                c0_leaps = bs_call_price(S0, K_leaps, 250 / 365.0, risk_free_rate, vol)
                # At 3M exit (after 63 trading days ~ 90 calendar days): T1 = 160 / 365
                c1_leaps = bs_call_price(S_3m, K_leaps, 160 / 365.0, risk_free_rate, vol)
                ret_leaps = (c1_leaps / c0_leaps) - 1.0 if c0_leaps > 0 else 0.0

                # Option Strategy B: Bull Call Vertical Spread (90 DTE, Buy 1.00 ATM Call, Sell 1.15 OTM Call)
                K_long = 1.00 * S0
                K_short = 1.15 * S0
                c0_long = bs_call_price(S0, K_long, 90 / 365.0, risk_free_rate, vol)
                c0_short = bs_call_price(S0, K_short, 90 / 365.0, risk_free_rate, vol)
                debit_spread_0 = c0_long - c0_short
                
                # At 3M expiry (T=0)
                c1_long = max(0.0, S_3m - K_long)
                c1_short = max(0.0, S_3m - K_short)
                spread_val_1 = c1_long - c1_short
                ret_spread = (spread_val_1 / debit_spread_0) - 1.0 if debit_spread_0 > 0 else 0.0

                long_calls_results.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "ticker": t,
                    "price_0": S0,
                    "price_3m": S_3m,
                    "stock_ret_3m": stock_ret_3m,
                    "leaps_ret_3m": ret_leaps,
                    "spread_ret_3m": ret_spread,
                    "leaps_cost_pct": c0_leaps / S0,
                    "spread_cost_pct": debit_spread_0 / S0,
                    "score": composite_multiplier
                })

            # -------------------------------------------------------------
            # 2. INVERTED SIGNAL (Overvalued + Overextended + Fragile)
            # -------------------------------------------------------------
            # Conditions for Short / Put candidate:
            # a) Valuation stretched: PE >= 80th percentile (or PE >= 45)
            # b) Technical Extension: Price >= 15% above 200d SMA, AND RSI >= 65
            # c) Fundamental Mismatch: Sales growth <= median OR ROIC <= 10%
            sma200 = float(sma200_df[t].loc[dt]) if t in sma200_df and pd.notna(sma200_df[t].loc[dt]) else 0.0
            rsi = float(rsi14_df[t].loc[dt]) if t in rsi14_df and pd.notna(rsi14_df[t].loc[dt]) else 50.0

            is_overvalued = (pe >= p_q75 and pe >= 35.0)
            is_overextended = (sma200 > 0 and (S0 / sma200) >= 1.15 and rsi >= 65.0)
            is_low_quality = (sales <= s_med or roic <= 0.10)

            if is_overvalued and is_overextended and is_low_quality:
                # Put Strategy A: Outright Put (60 DTE, 40-Delta / Strike = 0.95 * S0)
                K_put = 0.95 * S0
                p0_outright = bs_put_price(S0, K_put, 60 / 365.0, risk_free_rate, vol)
                # At 2-month expiry
                p1_outright = max(0.0, K_put - S_3m)
                ret_outright_put = (p1_outright / p0_outright) - 1.0 if p0_outright > 0 else -1.0

                # Put Strategy B: Bear Put Vertical Debit Spread (60 DTE, Buy 1.00 ATM Put, Sell 0.88 OTM Put)
                K_put_long = 1.00 * S0
                K_put_short = 0.88 * S0
                p0_long = bs_put_price(S0, K_put_long, 60 / 365.0, risk_free_rate, vol)
                p0_short = bs_put_price(S0, K_put_short, 60 / 365.0, risk_free_rate, vol)
                debit_put_spread_0 = p0_long - p0_short

                p1_long = max(0.0, K_put_long - S_3m)
                p1_short = max(0.0, K_put_short - S_3m)
                put_spread_val_1 = p1_long - p1_short
                ret_put_spread = (put_spread_val_1 / debit_put_spread_0) - 1.0 if debit_put_spread_0 > 0 else -1.0

                inverted_puts_results.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "ticker": t,
                    "price_0": S0,
                    "pe": pe,
                    "rsi": rsi,
                    "sma_ext_pct": ((S0 / sma200) - 1.0) * 100.0,
                    "sales5y": sales,
                    "price_3m": S_3m,
                    "stock_ret_3m": stock_ret_3m,
                    "outright_put_ret": ret_outright_put,
                    "put_spread_ret": ret_put_spread,
                    "spread_cost_pct": debit_put_spread_0 / S0
                })

    # Summary Statistics for Calls
    print("="*80)
    print("1. LONG LEVERAGE BACKTEST (EXTREMIZED MULTIPLIER >= 12.0x)")
    print("="*80)
    if long_calls_results:
        df_calls = pd.DataFrame(long_calls_results)
        print(f"Total Signals Evaluated: {len(df_calls)}")
        print(f"Unique Tickers: {df_calls['ticker'].unique().tolist()}")
        print(f"Underlying Stock 3M Mean Return: {df_calls['stock_ret_3m'].mean()*100:.2f}% (Win Rate: {(df_calls['stock_ret_3m'] > 0).mean()*100:.1f}%)")
        print(f"Deep ITM 80-Delta LEAPS 3M Mean Return: {df_calls['leaps_ret_3m'].mean()*100:.2f}% (Win Rate: {(df_calls['leaps_ret_3m'] > 0).mean()*100:.1f}%)")
        print(f"Bull Call Spread (ATM/15% OTM) Mean Return: {df_calls['spread_ret_3m'].mean()*100:.2f}% (Win Rate: {(df_calls['spread_ret_3m'] > 0).mean()*100:.1f}%)")
        print(f"Average LEAPS Capital Required: {df_calls['leaps_cost_pct'].mean()*100:.1f}% of Stock Price (~{1/df_calls['leaps_cost_pct'].mean():.1f}x Capital Leverage)")
        print(f"Average Spread Capital Required: {df_calls['spread_cost_pct'].mean()*100:.1f}% of Stock Price (~{1/df_calls['spread_cost_pct'].mean():.1f}x Capital Leverage)")
        print("\nTop 5 Call Sample Events:")
        print(df_calls[['date', 'ticker', 'score', 'stock_ret_3m', 'leaps_ret_3m', 'spread_ret_3m']].head(5).to_string())

    # Summary Statistics for Puts
    print("\n" + "="*80)
    print("2. INVERTED (OVERBOUGHT / OVERVALUED / FRAGILE) PUT BACKTEST")
    print("="*80)
    if inverted_puts_results:
        df_puts = pd.DataFrame(inverted_puts_results)
        print(f"Total Fragility Signals Evaluated: {len(df_puts)}")
        print(f"Unique Short/Put Candidates: {df_puts['ticker'].unique().tolist()}")
        print(f"Underlying Stock 3M Mean Return: {df_puts['stock_ret_3m'].mean()*100:.2f}% (Drop Rate [Stock < 0]: {(df_puts['stock_ret_3m'] < 0).mean()*100:.1f}%)")
        print(f"Outright Put (OTM 40-Delta) Mean Return: {df_puts['outright_put_ret'].mean()*100:.2f}% (Win Rate: {(df_puts['outright_put_ret'] > 0).mean()*100:.1f}%)")
        print(f"Bear Put Spread (ATM/12% OTM) Mean Return: {df_puts['put_spread_ret'].mean()*100:.2f}% (Win Rate: {(df_puts['put_spread_ret'] > 0).mean()*100:.1f}%)")
        print(f"Average Put Spread Cost: {df_puts['spread_cost_pct'].mean()*100:.1f}% of Stock Price")
        print("\nTop 5 Put Sample Events:")
        print(df_puts[['date', 'ticker', 'pe', 'rsi', 'sma_ext_pct', 'stock_ret_3m', 'put_spread_ret', 'outright_put_ret']].head(5).to_string())

if __name__ == "__main__":
    run_options_and_inverted_analysis()
