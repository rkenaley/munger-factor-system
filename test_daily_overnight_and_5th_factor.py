import pickle
import json
from pathlib import Path
import numpy as np
import pandas as pd

def run_daily_and_5th_factor_study():
    print("Loading OHLCV 1970-2026 price dictionary...")
    with open('data/historical_prices_1970_2026.pkl', 'rb') as f:
        ohlcv_dict = pickle.load(f)
        
    with open('data/sp100_nasdaq100_universe.json', 'r', encoding='utf-8') as f:
        fund_dict = json.load(f)
        
    with open('data/historical_quarterly_eps.json', 'r', encoding='utf-8') as f:
        eps_data = json.load(f)
        
    parsed_eps = {}
    for t, q_dict in eps_data.items():
        pairs = []
        for d_str, val in q_dict.items():
            try:
                pairs.append((pd.to_datetime(d_str), float(val)))
            except Exception:
                pass
        pairs.sort(key=lambda x: x[0])
        parsed_eps[t] = pairs
        
    # Build aligned daily Close, Open, and Volume DataFrames from 2008 to 2026
    start_dt = pd.to_datetime('2008-01-02')
    end_dt = pd.to_datetime('2026-10-12')
    
    spy_df = ohlcv_dict.get('SPY')
    if spy_df is None:
        spy_df = ohlcv_dict.get('^GSPC')
        
    common_dates = spy_df.loc[start_dt:end_dt].index
    print(f"Total daily trading sessions to evaluate: {len(common_dates)} (2008 - 2026)")
    
    # Extract Close and Open prices for universe
    tickers = [t for t in fund_dict.keys() if t in ohlcv_dict]
    print(f"Evaluating {len(tickers)} corporate constituents...")
    
    close_dict = {}
    open_dict = {}
    vol_dict = {}
    for t in tickers:
        df = ohlcv_dict[t]
        close_dict[t] = df['Close'].reindex(common_dates).ffill()
        open_dict[t] = df['Open'].reindex(common_dates).ffill()
        vol_dict[t] = df['Volume'].reindex(common_dates).fillna(0.0)
        
    close_df = pd.DataFrame(close_dict)
    open_df = pd.DataFrame(open_dict)
    vol_df = pd.DataFrame(vol_dict)
    
    spy_close = spy_df['Close'].reindex(common_dates).ffill()
    spy_open = spy_df['Open'].reindex(common_dates).ffill()
    
    # Precompute 14-day RSI for all tickers
    print("Precomputing 14-day RSI across all tickers...")
    delta_df = close_df.diff()
    gain_df = delta_df.clip(lower=0.0)
    loss_df = -delta_df.clip(upper=0.0)
    roll_gain = gain_df.rolling(14).mean()
    roll_loss = loss_df.rolling(14).mean()
    rs_df = roll_gain / (roll_loss + 1e-6)
    rsi_df = 100.0 - (100.0 / (1.0 + rs_df))
    
    # Precompute rolling 50-day SMA and distance
    sma50_df = close_df.rolling(50).mean()
    std50_df = close_df.rolling(50).std()
    z_dist50_df = (close_df - sma50_df) / (std50_df + 1e-6)
    
    # Precompute 20-day Volume Ratio
    vol_sma20 = vol_df.rolling(20).mean()
    vol_ratio_df = vol_df / (vol_sma20 + 1e-6)
    
    # Daily Screening Results Collector
    # We test daily evaluation on every single trading day (step = 1 instead of step = 5)
    daily_signals_4factor = []
    daily_signals_sentiment = [] # MUNGER + RSI <= 40 / Oversold Capitulation
    daily_signals_vol_climax = [] # MUNGER + Volume Surge >= 1.5x on down day
    daily_signals_5factor_mult = [] # 5-Factor Geometric Multiplier: (1+Z_g)(1+Z_r)(1+Z_y)(1+Z_s)(1+Z_capitulation)
    
    # Step = 1 evaluates ALL 4,600+ daily trading sessions!
    # To run in ~10 seconds, let's step every 1 day for 2008-2026
    step = 1
    eval_indices = range(50, len(common_dates) - 63, step)
    print(f"Scanning across {len(eval_indices)} daily sessions (every trading day)...")
    
    for i in eval_indices:
        dt = common_dates[i]
        
        # Cross-sectional metrics at date dt
        row_prices = close_df.iloc[i]
        row_rsi = rsi_df.iloc[i]
        row_zdist = z_dist50_df.iloc[i]
        row_volr = vol_ratio_df.iloc[i]
        
        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {}
        curr_raw_de = {}
        
        for t in tickers:
            p = row_prices[t]
            if pd.isna(p) or p <= 0:
                continue
            ttm_eps = None
            if t in parsed_eps:
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
            if ttm_eps is None or ttm_eps <= 0:
                today_p = float(close_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 25.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 25.0)
                years_ago = (common_dates[-1] - dt).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 12.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.35, annual_g))) ** years_ago)
                
            if ttm_eps > 0.05:
                pe = p / ttm_eps
                if 2.0 <= pe <= 200.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    curr_sales[t] = fund_dict[t].get('sales5y', 12.0)
                    curr_roic[t] = fund_dict[t].get('roic_proxy', 0.15)
                    raw_de = fund_dict[t].get('de', 50.0)
                    curr_raw_de[t] = raw_de
                    curr_de_inv[t] = 100.0 / (10.0 + max(0.0, raw_de))
                    
        if len(curr_pes) < 20:
            continue
            
        valid_t = list(curr_pes.keys())
        sales_vals = [curr_sales[t] for t in valid_t]
        roic_vals = [curr_roic[t] for t in valid_t]
        ey_vals = [curr_ey[t] for t in valid_t]
        de_inv_vals = [curr_de_inv[t] for t in valid_t]
        
        s_std, s_med = float(np.std(sales_vals)), float(np.median(sales_vals))
        r_std, r_med = float(np.std(roic_vals)), float(np.median(roic_vals))
        e_std, e_med = float(np.std(ey_vals)), float(np.median(ey_vals))
        d_std, d_med = float(np.std(de_inv_vals)), float(np.median(de_inv_vals))
        
        # Benchmark returns
        spy_c0 = spy_close.iloc[i]
        spy_o1 = spy_open.iloc[i+1]
        spy_c1 = spy_close.iloc[i+1]
        spy_c63 = spy_close.iloc[i+63]
        
        spy_overnight = (spy_o1 / spy_c0) - 1.0
        spy_next_day = (spy_c1 / spy_c0) - 1.0
        spy_3m = (spy_c63 / spy_c0) - 1.0
        
        for t in valid_t:
            c0 = close_df[t].iloc[i]
            o1 = open_df[t].iloc[i+1]
            c1 = close_df[t].iloc[i+1]
            c63 = close_df[t].iloc[i+63]
            
            zg = (curr_sales[t] - s_med) / (s_std if s_std > 0 else 1.0)
            zr = (curr_roic[t] - r_med) / (r_std if r_std > 0 else 1.0)
            zy = (curr_ey[t] - e_med) / (e_std if e_std > 0 else 1.0)
            zs = (curr_de_inv[t] - d_med) / (d_std if d_std > 0 else 1.0)
            
            all_4_above = (zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0)
            if not all_4_above:
                continue
                
            m4_score = (1.0 + zg) * (1.0 + zr) * (1.0 + zy) * (1.0 + zs)
            
            # Returns decomposed
            # 1. Overnight return: Close_t to Open_{t+1}
            ret_overnight = (o1 / c0) - 1.0
            # 2. Next-day full return: Close_t to Close_{t+1}
            ret_1d = (c1 / c0) - 1.0
            # 3. 3-Month return (Close_t to Close_{t+63})
            ret_3m_moc = (c63 / c0) - 1.0
            # 4. 3-Month return entering next day at Open (Open_{t+1} to Close_{t+63})
            ret_3m_next_open = (c63 / o1) - 1.0
            
            alpha_overnight = ret_overnight - spy_overnight
            alpha_3m_moc = ret_3m_moc - spy_3m
            alpha_3m_open = ret_3m_next_open - spy_3m
            
            ev = {
                'date': dt,
                'ticker': t,
                'm4': m4_score,
                'ret_overnight': ret_overnight,
                'alpha_overnight': alpha_overnight,
                'ret_1d': ret_1d,
                'ret_3m_moc': ret_3m_moc,
                'alpha_3m_moc': alpha_3m_moc,
                'ret_3m_next_open': ret_3m_next_open,
                'alpha_3m_open': alpha_3m_open,
                'rsi': row_rsi[t],
                'z_dist': row_zdist[t],
                'vol_ratio': row_volr[t]
            }
            
            # Base 4-factor trigger
            if m4_score >= 12.0:
                daily_signals_4factor.append(ev)
                
                # Hypothesis 2A: Sentiment Capitulation (RSI <= 42 or Price <= -1.0 SD below 50-day SMA)
                if (not pd.isna(row_rsi[t]) and row_rsi[t] <= 42.0) or (not pd.isna(row_zdist[t]) and row_zdist[t] <= -1.0):
                    daily_signals_sentiment.append(ev)
                    
                # Hypothesis 2B: Volume Capitulation Climax (Volume >= 1.4x on down day)
                if (not pd.isna(row_volr[t]) and row_volr[t] >= 1.4 and row_prices[t] < close_df[t].iloc[i-1]):
                    daily_signals_vol_climax.append(ev)
                    
            # Hypothesis 2C: 5-Factor Geometric Multiplier
            # Include Sentiment Oversold Z-score: z_sent = max(0, -z_dist)
            z_oversold = max(0.0, -row_zdist[t]) if not pd.isna(row_zdist[t]) else 0.0
            m5_score = m4_score * (1.0 + z_oversold)
            if m5_score >= 18.0:
                daily_signals_5factor_mult.append(ev)

    def stats_dict(events, name):
        if not events:
            return {}
        n = len(events)
        unique_days = len(set(e['date'] for e in events))
        
        # Overnight metrics
        r_on = [e['ret_overnight'] for e in events]
        a_on = [e['alpha_overnight'] for e in events]
        
        # 3M MOC (Close) vs Next-Day Open
        r3_moc = [e['ret_3m_moc'] for e in events]
        a3_moc = [e['alpha_3m_moc'] for e in events]
        r3_op = [e['ret_3m_next_open'] for e in events]
        a3_op = [e['alpha_3m_open'] for e in events]
        
        win_3m = sum(1 for r in r3_moc if r > 0) / n * 100.0
        win_on = sum(1 for r in r_on if r > 0) / n * 100.0
        
        return {
            "Strategy": name,
            "Total Alerts": n,
            "Alerts/Day": round(n / len(eval_indices), 2),
            "Active Days %": round(unique_days / len(eval_indices) * 100.0, 1),
            "Overnight Win %": f"{win_on:.1f}%",
            "Avg Overnight Gap": f"{np.mean(r_on)*100.0:+.2f}%",
            "Overnight Alpha": f"{np.mean(a_on)*100.0:+.2f}%",
            "3M Win Rate": f"{win_3m:.1f}%",
            "3M Ret (MOC)": f"{np.mean(r3_moc)*100.0:+.2f}%",
            "3M Alpha (MOC)": f"{np.mean(a3_moc)*100.0:+.2f}%",
            "3M Ret (Next Open)": f"{np.mean(r3_op)*100.0:+.2f}%",
            "3M Alpha (Next Open)": f"{np.mean(a3_op)*100.0:+.2f}%",
            "MOC Execution Bonus": f"{(np.mean(r3_moc) - np.mean(r3_op))*100.0:+.2f}%"
        }

    s1 = stats_dict(daily_signals_4factor, "Base 4-Factor Daily (M >= 12x)")
    s2 = stats_dict(daily_signals_sentiment, "+ Factor 5A: Sentiment Capitulation (RSI <= 42)")
    s3 = stats_dict(daily_signals_vol_climax, "+ Factor 5B: Volume Capitulation Climax (Vol >= 1.4x)")
    s4 = stats_dict(daily_signals_5factor_mult, "5-Factor Multiplier: Fund + Sentiment (M5 >= 18x)")
    
    df_results = pd.DataFrame([s1, s2, s3, s4])
    print("\n=== DAILY CLOSE SCREENER & OVERNIGHT ALPHA STUDY (2008 - 2026) ===")
    print(df_results.to_string(index=False))
    
    with open('data/daily_overnight_and_5th_factor_results.json', 'w') as f:
        json.dump([s1, s2, s3, s4], f, indent=2)

if __name__ == '__main__':
    run_daily_and_5th_factor_study()
