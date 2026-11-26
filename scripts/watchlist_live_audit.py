import pandas as pd
import yfinance as yf
from munger_engine import (
    load_or_generate_dataset,
    compute_vectorized_munger_scores,
    sanitize_universe_fundamentals,
)

close_df, gspc_close, fund_dict, eps_dict, _ = load_or_generate_dataset("data")

watchlist = [
    "MLI", "EME", "ADBE", "GOOGL", "TSM", "NVDA",
    "AVGO", "FN", "ASML", "TER", "EOG", "AMP", "ETN", "CEG", "GRID", "MSFT",
    "FSLR", "NXT", "MWH"
]

# Fetch live prices for watchlist + benchmark
live = yf.download(watchlist, period="5d", progress=False)["Close"].iloc[-1]

# Update a synthetic/live row for today's date (or overwrite last bar with live)
today_ts = pd.Timestamp.today().normalize()
if today_ts in close_df.index:
    eval_df = close_df.copy()
else:
    last_row = close_df.iloc[-1:].copy()
    last_row.index = [today_ts]
    eval_df = pd.concat([close_df, last_row])

for t in watchlist:
    if t in live.index and pd.notna(live[t]) and t in eval_df.columns:
        eval_df.loc[today_ts, t] = float(live[t])

eval_dates = eval_df.index[-2:]
dt_last = eval_dates[-1]

scores_clean, _, _ = compute_vectorized_munger_scores(
    close_df=eval_df,
    eval_dates=eval_dates,
    fund_dict=fund_dict,
    eps_dict=eps_dict,
    reporting_lag_days=45,
    allow_legacy_lookahead=False,
    winsorize_cross_section=True,
    enforce_full_cycle_profitability=True,
    use_cyclically_adjusted_ey=True,
)
clean_fund = sanitize_universe_fundamentals(fund_dict, eps_dict)

rows = []
for t in watchlist:
    spot = float(live[t]) if t in live.index and pd.notna(live[t]) else (float(eval_df.loc[dt_last, t]) if t in eval_df.columns else None)
    clean_m = float(scores_clean.loc[dt_last, t]) if t in scores_clean.columns and pd.notna(scores_clean.loc[dt_last, t]) else None
    raw_f = fund_dict.get(t, {})
    c_f = clean_fund.get(t, {})
    rows.append({
        "Ticker": t,
        "Live_$": round(spot, 2) if spot else None,
        "Clean_M": round(clean_m, 2) if clean_m is not None else None,
        "ROIC_%": round(float(raw_f.get("roic_proxy", 0.0)) * 100.0, 1) if raw_f else None,
        "Sales5Y_%": round(float(c_f.get("sales5y", 0.0)), 1) if c_f else None,
        "P/E": round(float(raw_f.get("pe", 0.0)), 1) if raw_f else None,
        "ND/EBITDA": round(float(raw_f.get("net_debt_to_ebitda", 0.0)), 2) if raw_f else None,
    })

df = pd.DataFrame(rows).sort_values(by="Clean_M", ascending=False, na_position="last")
print(f"=== LIVE WATCHLIST MUNGER AUDIT ({today_ts.strftime('%Y-%m-%d')}) ===")
print(df.to_string(index=False))
