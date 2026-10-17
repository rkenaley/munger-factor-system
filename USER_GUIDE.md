# MUNGER Factor System: Operational Tool Guide & User Manual

> **A Comprehensive Step-by-Step Guide to Operating the Live Strike-Zone Screener, SEC Form 4 Conviction Engine, Fidelity Tax-Lot Reconciler, and Research Backtests**  
> **Release:** v2.3.0  
> **Repository:** [`rkenaley/munger-factor-system`](https://github.com/rkenaley/munger-factor-system)

---

## Table of Contents
1. [Quickstart & Environment Setup](#1-quickstart--environment-setup)
2. [Interactive Web Terminal & Guided Screener Wizard](#2-interactive-web-terminal--guided-screener-wizard)
3. [Tool 1: Live Strike-Zone Scanner (`scan_strike_zone.py`)](#3-tool-1-live-strike-zone-scanner)
4. [Tool 2: Fidelity Tax-Lot Reconciler](#4-tool-2-fidelity-tax-lot-reconciler)
5. [Tool 3: Staleness Circuit Breaker](#5-tool-3-staleness-circuit-breaker)
6. [Tool 4: SEC Form 4 Insider Conviction Engine](#6-tool-4-sec-form-4-insider-conviction-engine)
7. [Tool 5: Research Backtesting Engines](#7-tool-5-research-backtesting-engines)
8. [Tool 6: Options Analysis & The 80/20 Engine](#8-tool-6-options-analysis--the-8020-engine)
9. [Tool 7: Master Reproduction & Unit Tests](#9-tool-7-master-reproduction--unit-tests)
10. [Troubleshooting & Frequently Asked Questions](#10-troubleshooting--faqs)

---

## 1. Quickstart & Environment Setup

The system uses `uv`, an ultra-fast Python package and project manager.

### Prerequisites
- Python 3.10 or higher
- `uv` installed (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh`)

### Installation & Dependency Sync
```bash
# Clone the repository
git clone https://github.com/rkenaley/munger-factor-system.git
cd munger-factor-system

# Install and synchronize all production dependencies
uv sync

# Verify live SEC and market data connectivity
uv run python -c "import edgar, finvizfinance, yfinance; print('Connectivity Verified!')"
```

---

## 2. Interactive Web Terminal & Guided Screener Wizard

The **Munger Factor Web Terminal** provides a local, one-stop-shop graphical interface that automates the end-to-end process of finding compounders, tuning factor hurdles with sliders, inspecting SEC Form 4 insider conviction, reconciling your Fidelity portfolio, and generating trade tickets.

### Launching the Web Terminal:
```bash
# Option A: Via PowerShell helper (opens browser automatically)
.\start_server.ps1

# Option B: Via Command Prompt / Batch file
start_server.bat

# Option C: Via uv package entrypoint
uv run python -m fidelity_control.server
```

### Automatic Local File-Watcher Daemon (`watch_fidelity.py`)
To avoid manually uploading CSVs, run the background file-watcher daemon:
```bash
uv run python watch_fidelity.py
```
* Monitors your default `Downloads` folder for new `Portfolio_Positions_*.csv` exports from Fidelity.com.
* Upon detecting a new download, it instantly copies the file to `data/`, executes the tax-lot reconciler, updates the cache, and pushes fresh directives into the Web Control Center.
The terminal launches at **`http://localhost:8505`**.

### The 7-Stage Guided Discovery Workflow:
1. **Stage 1: Universe & Clock Diagnostics**: Choose between the 176 Composite Universe, S&P 100, NASDAQ 100, or Custom Tickers. Inspect the market clock and trigger live intraday price updates via the Staleness Circuit Breaker.
2. **Stage 2: 4-Pillar Hurdle Controls (Sliders & Checkboxes)**:
   - **Sliders:** Minimum Munger Multiplier ($\mathcal{M}_{\text{clean}} \ge 2.0\times$), Min 5Y Revenue CAGR ($g$), Min ROIC ($r$), Max P/E ($y$), and Max Net Debt / EBITDA ($s \le 2.5\text{x}$).
   - **Checkboxes:** Enforce Strict Conjunctive Hurdle (must beat all 4 medians simultaneously), Value Trap Filter (full-cycle 5Y profitability, winsorization), and 200-Week SMA pullback gating.
   - **Preset Buttons:** 1-click strategy switching ("Classic Munger Strike Zone", "Deep Value Dislocation", "Secular Moat Compounder", "Permissive Watchlist").
3. **Stage 3: SEC Form 4 Insider Conviction Engine**: Toggle the $1.15\times$ Cluster Buying Boost, $0.70\times$ Heavy C-Suite Distribution Haircut, and automatic Form 4 noise filtering.
4. **Stage 4: Strike Zone Matrix & Heatmap**: View live cross-sectional candidate rankings, filter by Strike Zone or Blocked Traps, search by symbol, and click **"Inspect Moat"** to view a 4-pillar radar chart vs. the S&P 100 benchmark and insider transaction logs.
5. **Stage 5: Fidelity Portfolio Reconciliation & Dynamic Tax Deferral**: Drag & drop your `Portfolio_Positions_*.csv` or click "Load Sample Portfolio". The engine computes exact days to the 365-day LTCG threshold and displays tax drag savings.
6. **Stage 6: Order Ticket Generator**: Set available cash, max position caps (default 15%), and cash reserve buffers. The system calculates exact share quantities and emits copyable tickets (`BUY LLY 45 SHARES MOC DAY`), direct Fidelity trade links, and downloadable CSVs.
7. **Stage 7: Options 80/20 Collar Overlay**: Recommends 180–365 DTE protective put / covered call collars to protect large unrealized gains without triggering taxable capital gains realization.

---

## 3. Tool 1: Live Strike-Zone Scanner

The Strike-Zone Scanner (`scan_strike_zone.py`) is the primary production tool for daily market analysis.

### Running the Scanner
```bash
uv run python scan_strike_zone.py
# or via package entrypoint:
uv run munger-scan
```

### What Happens When You Run It:
1. **Staleness Circuit Breaker**: Checks if the local price cache lags the market clock by >18 hours. If stale, it downloads the latest 5-day bars via multi-threaded `yfinance` and updates the cache.
2. **Cross-Sectional Scoring**: Calculates Raw Munger Multipliers ($\mathcal{M}_{\text{raw}}$) and Value Trap Filter Immunized Multipliers ($\mathcal{M}_{\text{clean}}$) across the entire universe.
3. **Cyclical-Trap Gating**: Flags and blocks boom-bust cyclicals (companies that fail 5-year full-cycle profitability or have distorted ratios).
4. **SEC Form 4 Conviction Queries**: Downloads direct Form 4 XML filings from SEC EDGAR for top candidates, filtering for discretionary buys (Code P) and sales (Code S).
5. **Conviction Multiplier Application**: Adjusts final scores:
   - **1.15x Boost**: Cluster Buying ($\ge 2$ insiders buying $\ge \$250\text{k}$).
   - **0.70x Haircut**: Heavy C-Suite Distribution ($\ge \$20\text{M}$ open-market sales with zero buys).
6. **Fidelity Reconciliation**: If an exported Fidelity CSV is found, generates tax-lot orders.

### Understanding the Output Table:
```
Ticker  Spot ($)  Sales5Y_%  ROIC_%   P/B  Raw_M  Trap_Filtered_M  Insider_Mult  Conviction_M  All_Med  Audit_Status
   LLY   1208.25       47.7   102.3 31.11   2.98            10.70          1.00         10.70    False     Watchlist
  FTNT    186.46       25.6   117.4 91.27   4.33            10.24          0.70          7.17    False     Watchlist
  AAPL    345.85       16.4   148.8 46.33   4.26             7.16          0.70          5.01    False     Watchlist
```
- **`Raw_M`**: Legacy unadjusted score (vulnerable to cyclical traps and single-quarter spikes).
- **`Trap_Filtered_M`**: Winsorized, full-cycle profitability-gated score.
- **`Insider_Mult`**: SEC Form 4 conviction multiplier (1.15x, 0.70x, or 1.00x).
- **`Conviction_M`**: Final decision score ($\text{Trap\_Filtered\_M} \times \text{Insider\_Mult}$).
- **`All_Med`**: Boolean indicating whether the company clears the median hurdle across **all four pillars** simultaneously (Growth, ROIC, Earnings Yield, Solvency).
- **`Audit_Status`**:
  - `STRIKE ZONE`: Meets all 4 factor hurdles with $\mathcal{M} \ge 2.0$. Prime buy candidate.
  - `CYCLICAL TRAP BLOCKED`: Blocked by the Value Trap filter (e.g. Micron `MU`). Do not buy.
  - `Watchlist`: High quality, but temporarily missing one hurdle (e.g., valuation too rich or ROIC slightly below median).

---

## 4. Tool 2: Fidelity Tax-Lot Reconciler

The reconciler bridges quantitative factor scoring directly into your personal Fidelity brokerage account.

### How to Export Your Positions from Fidelity:
1. Log in to your account at [Fidelity.com](https://www.fidelity.com).
2. Navigate to **Accounts & Trade** $\to$ **Portfolio**.
3. Click the **Positions** tab.
4. In the upper-right corner of the positions table, click the **Download icon** (or "Download CSV").
5. Save the file. Fidelity names this export `Portfolio_Positions_YYYYMMDD.csv` or `Portfolio_Positions.csv`.
6. Drop this CSV directly into your repository root or `data/` folder:
   ```
   c:\AGProj\Tabs\munger-factor-system\data\Portfolio_Positions.csv
   ```
7. Run the scanner:
   ```bash
   uv run munger-scan
   ```

### Reading the Reconciliation Table:
```
Symbol  Shares  Cost_Basis  Live_Price  Live_Value  Unrealized_Gain  Days_Held LTCG_Status  Action                               Rationale
  AAPL   100.0   $18,000.0     $345.85   $34,585.0       +$16,585.0        486        LTCG  SELL (MOC)                           Sub-hurdle score (M=5.01). Position is LTCG (486d held). Reallocate capital to Strike Zone.
  MSFT    50.0   $20,000.0     $539.11   $26,955.5        +$6,955.5        277        STCG  HOLD FOR TAX DEFERRAL (88d to LTCG)  Sub-hurdle score (M=2.03). Defer exit 88 days to avoid ordinary income tax rate on $6,955 gain.
    MU   150.0   $10,500.0     $939.53  $140,929.5      +$130,429.5         74        STCG  HOLD FOR TAX DEFERRAL (291d to LTCG) Sub-hurdle score (M=0.00). Defer exit 291 days to avoid ordinary income tax rate on $130,429 gain.
  INTC   200.0    $6,000.0      $21.50    $4,300.0        -$1,700.0        213        STCG  SELL (MOC)                           Immediate tax-loss harvest. Sub-hurdle score (M=0.85). Realize $1,700 loss to offset gains; redeploy.
```

### The Three Action Directives & How to Trade Them:
1. **`STRONG HOLD / ADD`**:
   - **Meaning:** This company is in the Munger Strike Zone ($\mathcal{M} \ge 2.0$, all hurdles cleared).
   - **Execution:** Do not sell. If you have unallocated cash, add to this position on market pullbacks.
2. **`HOLD FOR TAX DEFERRAL (Xd to LTCG)`**:
   - **Meaning:** The stock currently fails Munger factor criteria (or is a blocked cyclical trap), **BUT** you have held it for less than 365 days and have a substantial unrealized gain.
   - **Execution:** **DO NOT SELL.** Selling today triggers federal and state short-term ordinary income tax rates (up to $40.8\%$). Defer the sale for $X$ calendar days until the lot matures into Long-Term Capital Gains ($15\% - 23.8\%$).
3. **`SELL (MOC)`**:
   - **Meaning:** The stock fails Munger criteria **AND** either:
     - You have held it for $\ge 365$ days (qualifying for LTCG rates), OR
     - You have an **unrealized loss** (tax-loss harvest immediately with zero tax drag).
   - **Execution:** Place a **Market-On-Close (MOC)** order around 3:55 PM Eastern to capture clean closing volume and redeploy into top Strike-Zone compounders.

---

## 5. Tool 3: Staleness Circuit Breaker

The circuit breaker prevents you from trading on day-old or week-old snapshot data.

### How It Works:
- Inspects the timestamp of the final bar in `data/historical_prices_1970_2026.pkl.xz`.
- Checks the current market clock:
  - If today is Saturday or Sunday, expected close is Friday 4:00 PM Eastern.
  - If today is Monday before 10:00 AM, expected close is Friday 4:00 PM Eastern.
  - If today is an active trading day (Mon–Fri) past 10:00 AM, expected bar is today's live intraday price.
- If lag exceeds 18 hours, it automatically triggers `yfinance` in parallel and updates the cache.

### Manual Refresh:
You can manually force a price refresh anytime in Python:
```python
from munger_engine import load_or_generate_dataset, refresh_live_intraday_prices

close_df, gspc_close, _, _, _ = load_or_generate_dataset("data")
updated_close, updated_gspc = refresh_live_intraday_prices(
    close_df=close_df,
    gspc_close=gspc_close,
    save_path="data/historical_prices_1970_2026.pkl.xz"
)
print("Updated through:", updated_close.index[-1])
```

---

## 6. Tool 4: SEC Form 4 Insider Conviction Engine

### Running Standalone Insider Queries:
You can query insider transactions for any ticker or list of tickers:

```python
from munger_engine import fetch_live_insider_activity

# Query a single company or multiple tickers
results = fetch_live_insider_activity(["AAPL", "MU", "BA", "MSTR"])

for ticker, data in results.items():
    print(f"{ticker}: Multiplier={data['multiplier']}x | Signal={data['signal']}")
    print(f"  Notes: {data['notes']}")
    print(f"  Buys: ${data['buys_usd']:,.0f} | C-Suite Sales: ${data['csuite_sales_usd']:,.0f}")
```

### Signal Rules:
- **`CLUSTER_BUY_BOOST` (1.15x Multiplier)**:
  - $\ge 2$ distinct executives/directors purchase shares on the open market (Transaction Code P) totaling $\ge \$250,000$.
  - Signifies high collective confidence from multiple decision-makers.
- **`CSUITE_DISTRIBUTION_HAIRCUT` (0.70x Multiplier)**:
  - C-suite executives (CEO, CFO, COO, President, Chairman) sell $\ge \$20,000,000$ open-market (Code S) with **zero** insider purchases.
  - Signifies aggressive insider liquidity extraction (e.g. Micron CEO dumping \$182M+).
- **`NEUTRAL` (1.00x Multiplier)**:
  - Routine small trades or no significant open-market activity.

---

## 7. Tool 5: Research Backtesting Engines

To audit historical performance, run factor sensitivity sweeps, or inspect the 1971–2026 compounding curves:

### Master Survivorship-Free Backtest
```bash
uv run python run_survivorship_free_backtest.py
# or: uv run munger-backtest
```
- Simulates the full 55.5-year dataset (1971–2026).
- Compares Causal Capped (15%), Uncapped, and S&P 500 benchmarks with point-in-time causality.
- Saves results to `data/survivorship_free_alpha_uplift_results.json` and generates `docs/figures/munger_survivorship_free_alpha_uplift.png`.

### Audited Warren Buffett Showdown & Survivorship Audit
```bash
uv run python audit_buffett_and_survivorship.py
```
- Compares MUNGER post-tax performance head-to-head against Warren Buffett / Berkshire Hathaway (`BRK-A` / `BRK-B`) over 2008–2026 (GFC to present) and 1996–2026 (30-year long horizon).
- Verifies that MUNGER's **23.69% post-tax CAGR** (2008–2026) outpaces Berkshire's **9.79% gross CAGR** by **5.5x in terminal wealth** (\$1,000 $\to$ \$50,477 vs \$5,372), even after applying a **-350 bps annual delisting haircut** stress test.
- Verifies academic parity against Berkshire's 1965–2024 gross return (~19.8%), demonstrating that MUNGER mirrors Berkshire's historic gross compounding **completely unlevered and post-tax**.

### Academic Research & Parameter Sweep Archive
The parameter sweeps and options experiments are preserved for replication in `archive/research_experiments/`:
- **Walk-Forward 1Y Hurdle Optimization:** `archive/research_experiments/optimize_1y_threshold_1971_2008.py` (in-sample grid search across $\mathcal{M} \in [1.0, 3.5]$).
- **Dynamic Sell Criteria Parameter Sweep:** `archive/research_experiments/test_munger_sell_criteria.py` (evaluates 7 exit strategies).
- **Institutional Alpha Bridge:** `archive/research_experiments/run_audited_institutional_analysis.py` (evaluates 15% vs. 20% position caps).
- **Debt Metric Tournament:** `archive/research_experiments/run_debt_metrics_backtest.py` (proves Net Debt / EBITDA $\le 2.5\text{x}$ superior to D/E and Debt/Market Cap).
- **Options Tenor & Spread Simulators:** `archive/research_experiments/optimize_call_tenors.py` and `simulate_call_spreads.py` (evaluates 30–365 DTE vertical call spreads).

---

## 8. Tool 6: Options Analysis & The 80/20 Engine

### Expiration Tenor & Skew Optimization
```bash
uv run python archive/research_experiments/optimize_call_tenors.py
```
- Uses analytical $O(1)$ Black-Scholes-Merton inverse normal CDF inversion to evaluate vertical bull call spreads ($65\Delta / 30\Delta$) across 30, 60, 90, 120, 180, and 365 DTE with realistic equity volatility skew.

### Vertical Call Spread Simulator
```bash
uv run python archive/research_experiments/simulate_call_spreads.py
```
- Simulates lognormal payoff distributions, delta-neutral cash yields, and models the **80/20 Treasury Allocation Model** (80% 3M T-Bills + 20% Bull Call Spreads).

---

## 9. Tool 7: Master Reproduction & Unit Tests

### Automated Unit Test Suite
```bash
uv run python test_causality_and_performance.py
# or: uv run munger-test
```
Verifies all 7 core mathematical invariants:
1. `test_strict_point_in_time_causality_no_lookahead`
2. `test_value_trap_cyclical_filter_blocks_micron_outlier`
3. `test_analytical_delta_strike_inversion_accuracy_and_speed`
4. `test_slippage_penalty_vs_synthetic_moc_bonus`
5. `test_staleness_circuit_breaker_detection`
6. `test_form4_insider_conviction_scoring_rules`
7. `test_fidelity_tax_lot_reconciler_and_cash_sweep_stripping`

### Turnkey One-Command Master Pipeline
```bash
uv run python reproduce_all.py
# or: uv run munger-reproduce
```
Executes the entire research pipeline from scratch: validates data files, runs all unit tests, executes all 7 backtests, updates figures, and recompiles the LaTeX academic whitepaper via `tectonic`.

---

## 10. Troubleshooting & FAQs

### Q: Why did the scanner flag my holding as `HOLD FOR TAX DEFERRAL`?
**A:** The stock no longer qualifies for the Munger Strike Zone (either its score dropped below $\mathcal{M} = 2.0$ or it was flagged as a cyclical trap). However, you have held this lot for less than 365 days and have an unrealized gain. Selling now forces an ordinary income tax hit ($\le 40.8\%$). The reconciler tells you the exact number of days remaining until LTCG status so you can defer the sale and preserve capital.

### Q: Does the scanner trade automatically or place orders for me?
**A:** No. The scanner is an audited decision-support engine. It generates exact, deterministic execution directives (`STRONG HOLD / ADD`, `HOLD FOR TAX DEFERRAL`, `SELL (MOC)`). You execute the orders directly in your brokerage account at Market-On-Close.

### Q: What if `yfinance` encounters rate limits or network issues?
**A:** The Staleness Circuit Breaker has built-in graceful exception handling. If `yfinance` is unreachable or rate-limited, the system prints a warning and falls back to your existing local price cache, ensuring backtests and offline screens never crash.

### Q: How do I recompile the academic whitepaper?
**A:** If you modify `docs/whitepaper.tex`, simply run:
```bash
tectonic docs/whitepaper.tex
```
`tectonic` will automatically download required fonts, resolve cross-references, and output `docs/whitepaper.pdf`.
