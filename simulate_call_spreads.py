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

def analyze_call_spreads():
    S0 = 100.0
    T = 90.0 / 365.0  # 90 days to expiration
    r = 0.045         # 4.5% risk-free rate
    sigma = 0.32      # 32% annualized IV (typical for high-quality tech / compounders post-dip)
    
    # 5 Strategies
    strategies = {
        "Outright Stock": {"type": "stock", "capital": 100.0},
        "Naked ATM Call (100C)": {"type": "call", "K": 100.0},
        "Deep ITM Spread (90C / 105C)": {"type": "spread", "K1": 90.0, "K2": 105.0},
        "Optimal Delta Spread (95C / 110C)": {"type": "spread", "K1": 95.0, "K2": 110.0},
        "Aggressive OTM Spread (100C / 115C)": {"type": "spread", "K1": 100.0, "K2": 115.0},
    }
    
    # Calculate initial pricing & Greeks
    details = {}
    for name, s in strategies.items():
        if s["type"] == "stock":
            details[name] = {
                "Capital ($)": 100.0,
                "Delta": 1.0,
                "Theta ($/day)": 0.0,
                "Max Loss ($)": 100.0,
                "Max Profit ($)": "Uncapped",
                "Breakeven ($)": 100.0,
                "Cost / 100 Shares ($)": 10000.0
            }
        elif s["type"] == "call":
            c = bsm_call(S0, s["K"], T, r, sigma)
            details[name] = {
                "Capital ($)": round(c["price"], 2),
                "Delta": round(c["delta"], 3),
                "Theta ($/day)": round(c["theta"], 3),
                "Max Loss ($)": round(c["price"], 2),
                "Max Profit ($)": "Uncapped",
                "Breakeven ($)": round(s["K"] + c["price"], 2),
                "Cost / 100 Shares ($)": round(c["price"] * 100, 2)
            }
        elif s["type"] == "spread":
            c1 = bsm_call(S0, s["K1"], T, r, sigma)
            c2 = bsm_call(S0, s["K2"], T, r, sigma)
            net_cost = c1["price"] - c2["price"]
            net_delta = c1["delta"] - c2["delta"]
            net_theta = c1["theta"] - c2["theta"]
            max_profit = (s["K2"] - s["K1"]) - net_cost
            max_roc = (max_profit / net_cost) * 100.0
            details[name] = {
                "Capital ($)": round(net_cost, 2),
                "Delta": round(net_delta, 3),
                "Theta ($/day)": round(net_theta, 3),
                "Max Loss ($)": round(net_cost, 2),
                "Max Profit ($)": round(max_profit, 2),
                "Max ROC (%)": round(max_roc, 1),
                "Breakeven ($)": round(s["K1"] + net_cost, 2),
                "Cost / 100 Shares ($)": round(net_cost * 100, 2)
            }
            
    print("=== INITIAL OPTION PROFILE (S0 = $100, 90 DTE, IV = 32%, r = 4.5%) ===")
    for k, v in details.items():
        print(f"\n{k}:")
        for metric, val in v.items():
            print(f"  {metric:22s}: {val}")
            
    # Empirical Scenario Payoff Matrix
    # Scenarios based on historical MUNGER distribution:
    # 1. Severe Drawdown: -18.7% (Max 3M Drawdown)
    # 2. Moderate Dip: -10.0%
    # 3. Flat: 0.0%
    # 4. SPY Benchmark Return: +3.32%
    # 5. MUNGER Expected Alpha: +10.14% (Average 3M return)
    # 6. Upper Quartile Outperformance: +20.0%
    # 7. Extreme Outlier: +35.0%
    
    scenarios = [-18.7, -10.0, 0.0, 3.32, 10.14, 20.0, 35.0]
    
    payoffs = []
    for sc in scenarios:
        ST = S0 * (1.0 + sc / 100.0)
        row = {"Stock Return (%)": f"{sc:+.1f}%", "Spot Price": f"${ST:.2f}"}
        for name, s in strategies.items():
            if s["type"] == "stock":
                pnl = ST - S0
                roc = (pnl / S0) * 100.0
            elif s["type"] == "call":
                pnl = max(0.0, ST - s["K"]) - details[name]["Capital ($)"]
                roc = (pnl / details[name]["Capital ($)"]) * 100.0
            elif s["type"] == "spread":
                payoff = max(0.0, min(s["K2"] - s["K1"], ST - s["K1"]))
                pnl = payoff - details[name]["Capital ($)"]
                roc = (pnl / details[name]["Capital ($)"]) * 100.0
            row[name] = f"{roc:+.1f}% (${pnl:+.2f})"
        payoffs.append(row)
        
    df_payoffs = pd.DataFrame(payoffs)
    print("\n=== EXPIRY RETURN ON CAPITAL (ROC) BY UNDERLYING SCENARIO ===")
    print(df_payoffs.to_string(index=False))
    
    # Save results to json for whitepaper ingestion
    with open('data/call_spread_optimization_results.json', 'w') as f:
        json.dump({"details": details, "scenarios": payoffs}, f, indent=2)

if __name__ == '__main__':
    analyze_call_spreads()
