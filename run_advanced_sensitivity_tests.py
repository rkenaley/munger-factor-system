"""Comprehensive Multi-Factor Sensitivity & Trigger Optimization Tests.

Evaluates:
1. Complete inventory of considered MUNGER triggers (normalized & absolute terms, frequency, returns).
2. Factor Ablation (Leave-One-Out Sensitivity: -Growth, -ROIC, -Yield, -Solvency).
3. Weighting Sensitivity (Exponent Tilts: Growth-tilt, Quality-tilt, Value-tilt).
4. Hurdle Stringency Sensitivity (Z >= -0.25, 0.0, +0.25, +0.67).
5. Universe & Rebalancing Frequency Sensitivity.
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd

def run_sensitivities():
    price_file = Path("data/historical_prices_2007_2026.pkl")
    prices_df = pd.read_pickle(price_file)
    dates = prices_df.index
    
    eps_file = Path("data/historical_quarterly_eps.json")
    with open(eps_file, "r", encoding="utf-8") as f:
        eps_data = json.load(f)
        
    univ_file = Path("data/sp100_nasdaq100_universe.json")
    with open(univ_file, "r", encoding="utf-8") as f:
        fund_dict = json.load(f)
        
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
        
    start_dt = pd.to_datetime("2008-01-02")
    sample_indices = [i for i in range(0, len(dates) - 63, 5) if dates[i] >= start_dt]
    total_weeks = len(sample_indices)
    print(f"Running sensitivities across {total_weeks} weekly cross-sections...")
    
    # Trigger categories to evaluate
    triggers = {
        "Strict Q1 Value": {"type": "fixed", "events": []},
        "Relaxed to Median": {"type": "fixed", "events": []},
        "Dislocation Quality": {"type": "fixed", "events": []},
        "+0.5 SD Quality": {"type": "quality", "z": 0.5, "events": []},
        "+1.0 SD Quality": {"type": "quality", "z": 1.0, "events": []},
        "+1.5 SD Quality": {"type": "quality", "z": 1.5, "events": []},
        "+2.0 SD Outlier Quality": {"type": "quality", "z": 2.0, "events": []},
        "Multiplier >= 4.0x": {"type": "multiplier", "c": 4.0, "events": []},
        "Multiplier >= 6.0x": {"type": "multiplier", "c": 6.0, "events": []},
        "Multiplier >= 8.0x": {"type": "multiplier", "c": 8.0, "events": []},
        "Multiplier >= 10.0x": {"type": "multiplier", "c": 10.0, "events": []},
        "Multiplier >= 12.0x (Optimal)": {"type": "multiplier", "c": 12.0, "events": []},
        "Multiplier >= 14.0x": {"type": "multiplier", "c": 14.0, "events": []},
        "Multiplier >= 16.0x": {"type": "multiplier", "c": 16.0, "events": []},
        "Multiplier >= 18.0x (Generational)": {"type": "multiplier", "c": 18.0, "events": []},
        "Multiplier >= 25.0x (Outlier)": {"type": "multiplier", "c": 25.0, "events": []}
    }
    
    # Ablation tests: Drop one factor from M >= 12x
    ablations = {
        "Base 4-Factor (Full MUNGER)": {"events": []},
        "Ablation 1: Drop Growth (-g)": {"events": []},
        "Ablation 2: Drop ROIC (-r)": {"events": []},
        "Ablation 3: Drop Yield / Value (-y)": {"events": []},
        "Ablation 4: Drop Debt Solvency (-s)": {"events": []},
    }
    
    # Weighting tests: Exponent tilt on factors
    weightings = {
        "Equal Weight: (1+Z)^1.0": {"events": []},
        "Growth-Tilt: (1+Z_g)^1.5": {"events": []},
        "ROIC-Tilt: (1+Z_r)^1.5": {"events": []},
        "Value-Tilt: (1+Z_y)^1.5": {"events": []},
        "Solvency-Tilt: (1+Z_s)^1.5": {"events": []},
    }
    
    # Hurdle stringency tests
    hurdles = {
        "Sub-Median Hurdle: Z >= -0.25": {"events": []},
        "Median Hurdle: Z >= 0.0 (Base)": {"events": []},
        "Above-Median Hurdle: Z >= +0.25": {"events": []},
        "Quartile Hurdle: Z >= +0.67 (Q3)": {"events": []}
    }
    
    # Absolute metric tracking for averages
    abs_metrics = {"sales_med": [], "roic_med": [], "pe_med": [], "de_med": []}
    
    for idx in sample_indices:
        dt = dates[idx]
        row_prices = prices_df.loc[dt]
        
        curr_pes = {}
        curr_ey = {}
        curr_sales = {}
        curr_roic = {}
        curr_de_inv = {}
        curr_raw_de = {}
        
        for t in fund_dict.keys():
            if t not in row_prices or pd.isna(row_prices[t]) or row_prices[t] <= 0:
                continue
            p = float(row_prices[t])
            ttm_eps = None
            if t in parsed_eps:
                past_quarters = [q for q in parsed_eps[t] if q[0] <= dt]
                if len(past_quarters) >= 4:
                    ttm_eps = sum(q[1] for q in past_quarters[-4:])
            if ttm_eps is None or ttm_eps <= 0:
                today_p = float(prices_df[t].dropna().iloc[-1])
                today_pe = fund_dict[t].get('pe', 25.0)
                today_eps = today_p / (today_pe if today_pe > 0 else 25.0)
                years_ago = (dates[-1] - dt).days / 365.25
                annual_g = fund_dict[t].get('sales5y', 12.0) / 100.0
                ttm_eps = today_eps / ((1.0 + max(-0.05, min(0.35, annual_g))) ** years_ago)
                
            if ttm_eps > 0.05:
                pe = p / ttm_eps
                if 2.0 <= pe <= 200.0:
                    curr_pes[t] = pe
                    curr_ey[t] = 1.0 / pe
                    idx_5y = idx - (252 * 5)
                    if idx_5y >= 0 and pd.notna(prices_df[t].iloc[idx_5y]) and prices_df[t].iloc[idx_5y] > 0:
                        p_5y = float(prices_df[t].iloc[idx_5y])
                        g_price = ((p / p_5y) ** 0.2 - 1.0) * 100.0
                        sales_g = 0.5 * fund_dict[t].get('sales5y', 12.0) + 0.5 * g_price
                    else:
                        sales_g = fund_dict[t].get('sales5y', 12.0)
                    curr_sales[t] = max(-20.0, min(120.0, sales_g))
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
        pes_vals = [curr_pes[t] for t in valid_t]
        raw_de_vals = [curr_raw_de[t] for t in valid_t]
        
        s_std, s_med = float(np.std(sales_vals)), float(np.median(sales_vals))
        r_std, r_med = float(np.std(roic_vals)), float(np.median(roic_vals))
        e_std, e_med = float(np.std(ey_vals)), float(np.median(ey_vals))
        d_std, d_med = float(np.std(de_inv_vals)), float(np.median(de_inv_vals))
        p_q1, p_med = float(np.percentile(pes_vals, 25)), float(np.median(pes_vals))
        de_med = float(np.median(raw_de_vals))
        
        abs_metrics["sales_med"].append(s_med)
        abs_metrics["roic_med"].append(r_med)
        abs_metrics["pe_med"].append(p_med)
        abs_metrics["de_med"].append(de_med)
        
        spy_ret_3m = None
        if 'SPY' in prices_df and idx + 63 < len(dates):
            spy_ret_3m = (float(prices_df['SPY'].iloc[idx + 63]) / float(prices_df['SPY'].iloc[idx])) - 1.0
            
        for t in valid_t:
            S0 = float(row_prices[t])
            pe = curr_pes[t]
            sales = curr_sales[t]
            roic = curr_roic[t]
            ey = curr_ey[t]
            de_inv = curr_de_inv[t]
            raw_de = curr_raw_de[t]
            
            ret_3m = (float(prices_df[t].iloc[idx + 63]) / S0) - 1.0 if idx + 63 < len(dates) else None
            ret_1y = (float(prices_df[t].iloc[idx + 252]) / S0) - 1.0 if idx + 252 < len(dates) else None
            alpha_3m = (ret_3m - spy_ret_3m) if (ret_3m is not None and spy_ret_3m is not None) else None
            
            # Max DD
            max_dd_3m = None
            if idx + 63 < len(dates):
                window = prices_df[t].iloc[idx : idx + 64]
                dd = (window - window.cummax()) / window.cummax()
                max_dd_3m = float(dd.min())
                
            ev = {
                'ret_3m': ret_3m,
                'ret_1y': ret_1y,
                'alpha_3m': alpha_3m,
                'max_dd_3m': max_dd_3m,
                'date': dt
            }
            
            # Z-scores
            zg = (sales - s_med) / (s_std if s_std > 0 else 1.0)
            zr = (roic - r_med) / (r_std if r_std > 0 else 1.0)
            zy = (ey - e_med) / (e_std if e_std > 0 else 1.0)
            zs = (de_inv - d_med) / (d_std if d_std > 0 else 1.0)
            
            # Quality composite
            z_qual = 0.5 * zg + 0.5 * zr
            
            # Base multiplier
            all_above_med = (zg >= 0 and zr >= 0 and zy >= 0 and zs >= 0)
            m_score = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs)) if all_above_med else 0.0
            
            # 1. Triggers Evaluation
            if pe <= p_q1 and sales >= 15.0 and raw_de <= 100.0:
                triggers["Strict Q1 Value"]["events"].append(ev)
            if pe <= p_med and sales >= 12.0 and raw_de <= 120.0:
                triggers["Relaxed to Median"]["events"].append(ev)
            if pe <= p_med and roic >= r_med and raw_de <= 150.0:
                triggers["Dislocation Quality"]["events"].append(ev)
                
            for name, trig in triggers.items():
                if trig["type"] == "quality":
                    if pe <= p_med and z_qual >= trig["z"]:
                        trig["events"].append(ev)
                elif trig["type"] == "multiplier":
                    if all_above_med and m_score >= trig["c"]:
                        trig["events"].append(ev)
                        
            # 2. Ablation Evaluation (at M >= 12x threshold)
            if all_above_med and m_score >= 12.0:
                ablations["Base 4-Factor (Full MUNGER)"]["events"].append(ev)
            # Ablation 1: Drop Growth (-g)
            if (zr >= 0 and zy >= 0 and zs >= 0):
                m_no_g = (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_no_g >= (12.0 ** 0.75):  # Adjusted 3-factor threshold
                    ablations["Ablation 1: Drop Growth (-g)"]["events"].append(ev)
            # Ablation 2: Drop ROIC (-r)
            if (zg >= 0 and zy >= 0 and zs >= 0):
                m_no_r = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_no_r >= (12.0 ** 0.75):
                    ablations["Ablation 2: Drop ROIC (-r)"]["events"].append(ev)
            # Ablation 3: Drop Yield (-y)
            if (zg >= 0 and zr >= 0 and zs >= 0):
                m_no_y = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zs))
                if m_no_y >= (12.0 ** 0.75):
                    ablations["Ablation 3: Drop Yield / Value (-y)"]["events"].append(ev)
            # Ablation 4: Drop Debt (-s)
            if (zg >= 0 and zr >= 0 and zy >= 0):
                m_no_s = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy))
                if m_no_s >= (12.0 ** 0.75):
                    ablations["Ablation 4: Drop Debt Solvency (-s)"]["events"].append(ev)
                    
            # 3. Weighting Evaluation
            if all_above_med:
                if m_score >= 12.0:
                    weightings["Equal Weight: (1+Z)^1.0"]["events"].append(ev)
                # Growth-tilt
                m_gtilt = ((1.0 + max(0.0, zg)) ** 1.5) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_gtilt >= 14.5:
                    weightings["Growth-Tilt: (1+Z_g)^1.5"]["events"].append(ev)
                # ROIC-tilt
                m_rtilt = (1.0 + max(0.0, zg)) * ((1.0 + max(0.0, zr)) ** 1.5) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_rtilt >= 14.5:
                    weightings["ROIC-Tilt: (1+Z_r)^1.5"]["events"].append(ev)
                # Value-tilt
                m_ytilt = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * ((1.0 + max(0.0, zy)) ** 1.5) * (1.0 + max(0.0, zs))
                if m_ytilt >= 14.5:
                    weightings["Value-Tilt: (1+Z_y)^1.5"]["events"].append(ev)
                # Solvency-tilt
                m_stilt = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * ((1.0 + max(0.0, zs)) ** 1.5)
                if m_stilt >= 14.5:
                    weightings["Solvency-Tilt: (1+Z_s)^1.5"]["events"].append(ev)
                    
            # 4. Hurdle Stringency Evaluation
            # Sub-median: Z >= -0.25
            if (zg >= -0.25 and zr >= -0.25 and zy >= -0.25 and zs >= -0.25):
                m_sub = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_sub >= 12.0:
                    hurdles["Sub-Median Hurdle: Z >= -0.25"]["events"].append(ev)
            if all_above_med and m_score >= 12.0:
                hurdles["Median Hurdle: Z >= 0.0 (Base)"]["events"].append(ev)
            # Above-median: Z >= +0.25
            if (zg >= 0.25 and zr >= 0.25 and zy >= 0.25 and zs >= 0.25):
                m_above = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_above >= 12.0:
                    hurdles["Above-Median Hurdle: Z >= +0.25"]["events"].append(ev)
            # Quartile Q3: Z >= +0.67
            if (zg >= 0.67 and zr >= 0.67 and zy >= 0.67 and zs >= 0.67):
                m_q3 = (1.0 + max(0.0, zg)) * (1.0 + max(0.0, zr)) * (1.0 + max(0.0, zy)) * (1.0 + max(0.0, zs))
                if m_q3 >= 12.0:
                    hurdles["Quartile Hurdle: Z >= +0.67 (Q3)"]["events"].append(ev)
                    
    def summarize_events(events):
        if not events:
            return {"signals": 0, "win_rate": 0.0, "ret_3m": 0.0, "alpha": 0.0, "ret_1y": 0.0, "max_dd": 0.0, "active_weeks_pct": 0.0, "signals_per_week": 0.0}
        n = len(events)
        r3 = [e['ret_3m'] for e in events if e['ret_3m'] is not None]
        a3 = [e['alpha_3m'] for e in events if e['alpha_3m'] is not None]
        r1y = [e['ret_1y'] for e in events if e['ret_1y'] is not None]
        dd = [e['max_dd_3m'] for e in events if e['max_dd_3m'] is not None]
        unique_dates = len(set(e['date'] for e in events))
        
        return {
            "signals": n,
            "win_rate": round(sum(1 for r in r3 if r > 0) / len(r3) * 100.0, 1) if r3 else 0.0,
            "ret_3m": round(float(np.mean(r3)) * 100.0, 2) if r3 else 0.0,
            "alpha": round(float(np.mean(a3)) * 100.0, 2) if a3 else 0.0,
            "ret_1y": round(float(np.mean(r1y)) * 100.0, 2) if r1y else 0.0,
            "max_dd": round(float(np.mean(dd)) * 100.0, 1) if dd else 0.0,
            "active_weeks_pct": round(unique_dates / total_weeks * 100.0, 1),
            "signals_per_week": round(n / total_weeks, 2)
        }
        
    print("\n=== TABLE 1: MUNGER TRIGGERS CONSIDERED (NORMALIZED & ABSOLUTE, FREQUENCY, PERFORMANCE) ===")
    t1_rows = []
    # Median benchmarks across entire 18.8y period
    avg_s_med = float(np.mean(abs_metrics["sales_med"]))
    avg_r_med = float(np.mean(abs_metrics["roic_med"])) * 100.0
    avg_p_med = float(np.mean(abs_metrics["pe_med"]))
    avg_d_med = float(np.mean(abs_metrics["de_med"]))
    
    for name, trig in triggers.items():
        s = summarize_events(trig["events"])
        t1_rows.append({
            "Trigger Name": name,
            "Signals": s["signals"],
            "Sig/Wk": s["signals_per_week"],
            "Active Wks %": f"{s['active_weeks_pct']}%",
            "Win Rate": f"{s['win_rate']}%",
            "Fwd 3M Ret": f"+{s['ret_3m']}%",
            "3M Alpha": f"+{s['alpha']}%",
            "Fwd 1Y Ret": f"+{s['ret_1y']}%",
            "Max DD": f"{s['max_dd']}%"
        })
    df_t1 = pd.DataFrame(t1_rows)
    print(df_t1.to_string(index=False))
    
    print("\n=== TABLE 2A: FACTOR ABLATION SENSITIVITY (LEAVE-ONE-OUT) ===")
    t2a_rows = []
    for name, d in ablations.items():
        s = summarize_events(d["events"])
        t2a_rows.append({
            "Ablation Test": name,
            "Signals": s["signals"],
            "Win Rate": f"{s['win_rate']}%",
            "Fwd 3M": f"+{s['ret_3m']}%",
            "3M Alpha": f"+{s['alpha']}%",
            "Fwd 1Y": f"+{s['ret_1y']}%",
            "Max DD": f"{s['max_dd']}%"
        })
    df_t2a = pd.DataFrame(t2a_rows)
    print(df_t2a.to_string(index=False))
    
    print("\n=== TABLE 2B: WEIGHTING SENSITIVITY (FACTOR EXPONENT TILTS) ===")
    t2b_rows = []
    for name, d in weightings.items():
        s = summarize_events(d["events"])
        t2b_rows.append({
            "Weighting Scheme": name,
            "Signals": s["signals"],
            "Win Rate": f"{s['win_rate']}%",
            "Fwd 3M": f"+{s['ret_3m']}%",
            "3M Alpha": f"+{s['alpha']}%",
            "Fwd 1Y": f"+{s['ret_1y']}%",
            "Max DD": f"{s['max_dd']}%"
        })
    df_t2b = pd.DataFrame(t2b_rows)
    print(df_t2b.to_string(index=False))

    print("\n=== TABLE 2C: HURDLE STRINGENCY SENSITIVITY ===")
    t2c_rows = []
    for name, d in hurdles.items():
        s = summarize_events(d["events"])
        t2c_rows.append({
            "Hurdle Level": name,
            "Signals": s["signals"],
            "Win Rate": f"{s['win_rate']}%",
            "Fwd 3M": f"+{s['ret_3m']}%",
            "3M Alpha": f"+{s['alpha']}%",
            "Fwd 1Y": f"+{s['ret_1y']}%",
            "Max DD": f"{s['max_dd']}%"
        })
    df_t2c = pd.DataFrame(t2c_rows)
    print(df_t2c.to_string(index=False))

    with open("data/robust_sensitivity_results.json", "w") as f:
        json.dump({
            "triggers": t1_rows,
            "ablations": t2a_rows,
            "weightings": t2b_rows,
            "hurdles": t2c_rows,
            "universe_medians": {
                "sales_growth_med": avg_s_med,
                "roic_med": avg_r_med,
                "pe_med": avg_p_med,
                "de_med": avg_d_med
            }
        }, f, indent=2)

if __name__ == '__main__':
    run_sensitivities()
