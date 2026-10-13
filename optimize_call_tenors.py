import math
import json
import pandas as pd

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x):
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

def bsm_call(S, K, T, r, sigma, q=0.01):
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    call = S * math.exp(-q * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    delta = math.exp(-q * T) * norm_cdf(d1)
    gamma = math.exp(-q * T) * norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * math.exp(-q * T) * norm_pdf(d1) * math.sqrt(T) / 100.0
    theta = (- (S * sigma * math.exp(-q * T) * norm_pdf(d1)) / (2.0 * math.sqrt(T)) 
             - r * K * math.exp(-r * T) * norm_cdf(d2) 
             + q * S * math.exp(-q * T) * norm_cdf(d1)) / 365.0
    return {"price": call, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}

def find_strike_by_delta(S, T, r, sigma, target_delta, is_call=True, q=0.01):
    # Binary search for strike matching target delta
    low_K = S * 0.5
    high_K = S * 2.0
    for _ in range(50):
        mid_K = (low_K + high_K) / 2.0
        c = bsm_call(S, mid_K, T, r, sigma, q)
        if c["delta"] > target_delta:
            low_K = mid_K
        else:
            high_K = mid_K
    return round(mid_K, 1)

def optimize_tenors():
    S0 = 100.0
    r = 0.045
    sigma = 0.32
    
    # Empirical underlying return trajectory from 18.8-year backtest
    # 1M (30d): +3.45%, 2M (60d): +6.80%, 3M (90d): +10.14%, 4M (120d): +14.50%, 6M (180d): +23.49%, 1Y (365d): +48.65%
    empirical_returns = {
        30: 3.45,
        60: 6.80,
        90: 10.14,
        120: 14.50,
        180: 23.49,
        365: 48.65
    }
    
    tenors = [30, 60, 90, 120, 180, 365]
    results = []
    
    for dte in tenors:
        T = dte / 365.0
        # Target: Long leg delta ~ 0.65, Short leg delta ~ 0.30
        K1 = find_strike_by_delta(S0, T, r, sigma, 0.65)
        K2 = find_strike_by_delta(S0, T, r, sigma, 0.30)
        
        c1 = bsm_call(S0, K1, T, r, sigma)
        c2 = bsm_call(S0, K2, T, r, sigma)
        
        debit = c1["price"] - c2["price"]
        net_delta = c1["delta"] - c2["delta"]
        net_theta = c1["theta"] - c2["theta"]
        spread_width = K2 - K1
        max_profit = spread_width - debit
        max_roc = (max_profit / debit) * 100.0
        
        # Realized performance based on empirical underlying return at expiration
        exp_ret = empirical_returns[dte]
        ST = S0 * (1.0 + exp_ret / 100.0)
        realized_payoff = max(0.0, min(spread_width, ST - K1))
        realized_pnl = realized_payoff - debit
        realized_roc = (realized_pnl / debit) * 100.0
        
        # Annualized compounding metrics
        periods_per_year = 365.0 / dte
        # Estimate turnover friction (bid-ask drag ~ $0.15 per contract per roll)
        friction_per_roll = 0.15
        net_pnl_after_friction = realized_pnl - friction_per_roll
        effective_roc_per_period = (net_pnl_after_friction / debit)
        
        # Annualized geometric CAGR
        annualized_cagr = ((1.0 + max(-0.99, effective_roc_per_period)) ** periods_per_year - 1.0) * 100.0
        
        # Capital efficiency ratio (S0 / debit)
        cap_eff = S0 / debit
        
        results.append({
            "DTE": dte,
            "Tenor (Months)": f"{dte/30:.1f}M",
            "K1 (Long)": K1,
            "K2 (Short)": K2,
            "Width ($)": round(spread_width, 1),
            "Debit ($)": round(debit, 2),
            "Cap Efficiency": f"{cap_eff:.1f}x",
            "Net Delta": round(net_delta, 3),
            "Net Theta ($/day)": round(net_theta, 4),
            "Empirical Ret": f"+{exp_ret:.2f}%",
            "Realized ROC": f"{realized_roc:+.1f}%",
            "Max ROC": f"{max_roc:.1f}%",
            "Annualized CAGR": f"{annualized_cagr:+.1f}%"
        })
        
    df = pd.DataFrame(results)
    print("=== CALL SPREAD TENOR OPTIMIZATION MATRIX ===")
    print(df.to_string(index=False))
    
    with open('data/call_tenor_optimization_results.json', 'w') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    optimize_tenors()
