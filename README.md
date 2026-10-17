# The MUNGER Factor System
### Multiple Uncorrelated Numbers Generating Extreme Returns

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Backtest Horizon](https://img.shields.io/badge/Backtest-1971--2026%20(55.5Y)-green.svg)](docs/whitepaper.pdf)
[![Post-Tax Net Alpha](https://img.shields.io/badge/Net%20Alpha-%2B570%20to%20%2B865%20bps-brightgreen.svg)](docs/figures/munger_survivorship_free_alpha_uplift.png)
[![Institutional Realism](https://img.shields.io/badge/Position%20Cap-Max%2015%25%20%2B%20Cash%20Drag-orange.svg)](docs/figures/munger_audited_compounding_curve.png)
[![Academic Whitepaper](https://img.shields.io/badge/Whitepaper-12%20Pages%20(PDF)-purple.svg)](docs/whitepaper.pdf)

> **"It is remarkable how much long-term advantage people like us have gotten by trying to be consistently not stupid, instead of trying to be very intelligent."**  
> — *Charlie Munger (1924–2023)*

---

## 1. Executive Summary & Quick Reproduction

Traditional value investing is broken by the **value trap** dilemma: buying cheap stocks that deserve to be cheap. Standard quantitative factor models (Fama-French, Carhart) commit an elementary mathematical error: they rely on *additive linear equations* ($w_1 Z_{\text{Value}} + w_2 Z_{\text{Quality}}$). This allows dying businesses with crushing debt and collapsing revenue to qualify as "cheap buys" simply because their share price crashed.

**MUNGER** translates Charlie Munger’s multi-disciplinary mental model latticework into a rigorous, survivorship-free quantitative engine:
1. **Conjunctive Median Hurdle:** A firm must strictly meet or exceed the cross-sectional market median ($Z_k \ge 0$) across **all four fundamental pillars** simultaneously (Revenue Growth, ROIC, Earnings Yield, and Solvency). Zero tolerance for balance sheet or unit-economic weakness.
2. **Multiplicative Compounding:** Scores are computed via $\mathcal{M}_i = \prod (1 + \max(0, Z_{i,k}))$, exponentially rewarding companies experiencing temporary valuation dislocations while maintaining elite return on capital.
3. **Dynamic Tax Deferral:** Positions are held for at least 1 year (qualifying for long-term capital gains) and liquidated when any factor drops below median. This unlocks the **Compounding Paradox of Tax Deferral**, cutting annual tax drag by **108 bps/year** and producing **+\$1.19 Million (+22.7%) more spendable wealth** over multi-decade horizons than mechanical rebalancing.
4. **Institutional Slippage Realism:** Fills model conservative **-5 bps per-trade market impact** and an audited **30% delisting haircut**, eliminating synthetic execution credits.
5. **Options Supercharger:** 90–120 DTE vertical bull call spreads ($95\text{C}/110\text{C}$) achieve a **14.1x capital efficiency** with near-zero theta decay and a capped -19.10% black swan drawdown in an 80/20 Treasury-backed allocation.

### One-Command Turnkey Reproduction
Clone and run the complete scientific verification pipeline (verifies datasets, runs 1971–2026 backtests, calculates the Alpha Uplift Bridge, and checks all figures):

```bash
# Clone the repository
git clone https://github.com/rkenaley/munger-factor-system.git
cd munger-factor-system

# Run Live Strike-Zone Scanner & Fidelity Tax-Lot Reconciler (v2.2 Production Engine)
uv run python scan_strike_zone.py
# or: munger-scan

# Run complete multi-decade reproduction pipeline
uv run python reproduce_all.py
```

---

## 2. Audited Empirical Compounding (1971–2026)

All simulations enforce institutional realism: **maximum 15% allocation per stock** (minimum 7 positions to be fully invested), cash drag on unallocated capital (3-Month Treasury yield), and zero survivorship bias across 176 verified historical corporate constituents.

```
                           AUDITED COMPONENT PERFORMANCE SUMMARY
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1971–2026 Stagflation Multi-Decade (55.5 Years, $1,000 Start, Historical LTCG Tax Brackets)           │
│   • S&P 500 Total Return Benchmark: $377,810 Post-Tax |  11.03% Net CAGR   | Baseline                  │
│   • Mechanical 1Y Exit (15% Cap): $5,259,518 Post-Tax |  16.31% Net CAGR   | +528 bps Alpha            │
│   • Dynamic Munger Strict (Optimal):$6,452,334 Post-Tax| 16.73% Net CAGR   | +570 bps Alpha (17.1x SP) │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2008–2026 GFC Peak to Present (18.8 Years, $1,000 Start, Historical LTCG Tax Brackets)                 │
│   • S&P 500 Total Return Benchmark:   $7,619 Post-Tax |  11.47% Net CAGR   | Baseline                  │
│   • Mechanical 1Y Exit (15% Cap):    $45,740 Post-Tax |  22.68% Net CAGR   | +1,121 bps Alpha          │
│   • Dynamic Munger Strict (Optimal): $30,804 Post-Tax |  20.12% Net CAGR   | +865 bps Alpha (4.0x SP)  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. The Survivorship-Free Alpha Uplift Bridge

![Alpha Uplift Bridge](docs/figures/munger_survivorship_free_alpha_uplift.png)

By isolating each component layer under institutional 15% caps across 176 verified corporate constituents, we quantify exactly where alpha originates:

### Cohort A: 2008–2026 GFC Peak to Present (18.8 Years, \$1,000 Start)
| Step | Strategy Configuration | Pre-Tax Wealth | Pre CAGR | Post-Tax Wealth | Post CAGR | Incremental Alpha | Alpha vs SPY | Annual Tax Drag | Trades |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **0** | **S&P 500 Buy & Hold** | \$7,619 | 11.47% | **\$7,619** | **11.47%** | Baseline | 0 bps | 0 bps | 1 |
| **1** | **Fundamental Selection** *(Mechanical 1Y, -5 bps)* | \$141,523 | 30.32% | **\$45,740** | **22.68%** | **+1,121 bps** | **+1,121 bps** | -764 bps | 220 |
| **2** | **+ Dynamic Tax Deferral** *(Strict Median Exit)* | **\$74,259** | **25.90%** | **\$30,804** | **20.12%** | **-256 bps** | **+865 bps** | **-578 bps** | 183 |

### Cohort B: 1971–2026 Stagflation Multi-Decade (55.5 Years, \$1,000 Start)
| Step | Strategy Configuration | Pre-Tax Wealth | Pre CAGR | Post-Tax Wealth | Post CAGR | Incremental Alpha | Alpha vs SPY | Annual Tax Drag | Trades |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **0** | **S&P 500 Total Return** *(Era Dividend Yield)* | \$377,810 | 11.03% | **\$377,810** | **11.03%** | Baseline | 0 bps | 0 bps | 1 |
| **1** | **Fundamental Selection** *(Mechanical 1Y, -5 bps)* | \$136.9M | 23.19% | **\$5.26M** | **16.31%** | **+528 bps** | **+528 bps** | -688 bps | 763 |
| **2** | **+ Dynamic Tax Deferral** *(Strict Median Exit)* | **\$101.1M** | **22.53%** | **\$6.45M** | **16.73%** | **+42 bps** | **+570 bps** | **-580 bps** | 625 |

---

## 4. The Four Factor Pillars & Master Sensitivity Findings

```
               THE MUNGER CONJUNCTIVE MULTIPLICATIVE MODEL
                      ┌───────────────────────┐
                      │ Universe Constituent  │
                      └───────────┬───────────┘
                                  │
          ┌───────────────────────┴───────────────────────┐
          ▼                       ▼                       ▼
    [1. Growth (g)]        [2. ROIC (r)]           [3. Yield (y)]        [4. Solvency (s)]
    5Y Rev CAGR >= Med     ROIC >= Med             TTM E/P >= Med        Net Debt/EBITDA <= 2.5x
          │                       │                       │                       │
          └───────────────────────┼───────────────────────┘                       │
                                  │                                               │
                        Clear All 4 Hurdles? ─────────────────────────────────────┘
                                  │
                   ┌──────────────┴──────────────┐
                   │ YES                         │ NO
                   ▼                             ▼
       M = Prod (1 + max(0, Z_k))          Score = 0.0
         Filter: M >= 12.0x                (Discard: Value Trap)
```

| Factor Pillar | Metric Name | Mathematical Formula | Economic Rationale | Hurdle Threshold |
| :--- | :--- | :--- | :--- | :---: |
| **1. Top-Line Growth ($g$)** | 5-Year Revenue CAGR | $(\text{Rev}_t / \text{Rev}_{t-5})^{0.2} - 1$ | Proves secular tailwinds; eliminates dying industries | $\ge$ Median ($Z_g \ge 0$) |
| **2. Capital Efficiency ($r$)** | Return on Invested Capital | $\text{NOPAT} / (\text{Equity} + \text{Debt} - \text{Cash})$ | Measures moat durability and organic reinvestment runway | $\ge$ Median ($Z_r \ge 0$) |
| **3. Valuation Dislocation ($y$)** | TTM Earnings Yield | $\text{TTM EPS} / \text{Stock Price} = 1 / (P/E)$ | Identifies temporary market dislocations | $\ge$ Median ($Z_y \ge 0$) |
| **4. Balance Sheet Solvency ($s$)** | Net Debt / EBITDA | $(\text{Total Debt} - \text{Cash}) / \text{EBITDA} \le 2.5\text{x}$ | Solvency resilience: immunizes against debt spirals and rate shocks | $\le 2.5\text{x}$ / Inverted Median ($Z_s \ge 0$) |

### Master Sensitivity & Ablation Suite (932 Weekly Cross-Sections):
1. **ROIC is the Unforgiving Alpha Engine:** Ablating ROIC causes 3-month alpha to collapse from **+6.75% to +4.13% (-262 bps)** and crashes 1-year compounding from **+48.91% to +33.24% (-1,567 bps)**. Without ROIC, the system buys commoditized cyclicals.
2. **Equal Weighting is Mathematically Pure:** Applying exponent tilts ($(1+Z_y)^{1.5}$) creates minor noise (+11 bps) while adding unnecessary parameter complexity. Equal weighting preserves scale invariance.
3. **Median vs. Quartile Stringency:** Demanding upper-quartile performance ($Z \ge +0.67$) across all 4 factors paradoxically degrades 1-year compounding to +32.2% because it rejects top compounders during cyclical market panics.
4. **Factor Parsimony:** Stress-testing 5th orthogonal triggers (RSI $<30$, Bollinger lower-band, moving average dips) yielded negligible incremental alpha (+8 to +18 bps) while causing severe signal starvation (-65% to -78% drop in active weeks).

---

## 5. Institutional Realism & The Warren Buffett Showdown

![Audited Compounding](docs/figures/munger_audited_compounding_curve.png)

How does the MUNGER factor engine compare to the greatest real-world track record in financial history? 

### Head-to-Head Audited Performance Showdown:

| Period | Asset / Strategy | Gross CAGR | Net Post-Tax CAGR | Terminal Wealth (\$1,000) | Alpha vs. S&P 500 | Alpha vs. Buffett | Max Leverage |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **2008–2026** (18.8Y, Post-GFC) | **MUNGER Factor (15% Cap, Strict Exit)** | **29.74%** | **23.69%** | **\$50,477** | **+1,222 bps** | **+1,390 bps** | **0.0x (Cash Only)** |
| 2008–2026 | **MUNGER Stress-Tested (-350 bps Delist)** | 26.24% | **20.19%** | **\$29,913** | **+872 bps** | **+1,040 bps** | **0.0x (Cash Only)** |
| 2008–2026 | **Warren Buffett / Berkshire (`BRK-A`)** | 9.79% | 9.79% | \$5,372 | -168 bps | Baseline | ~1.6x (Float) |
| 2008–2026 | **S&P 500 Total Return Index** | 11.47% | 11.47% | \$7,619 | Baseline | +168 bps | 1.0x (Index) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1996–2026** (30.0Y, Multi-Cycle) | **MUNGER Factor (15% Cap, Strict Exit)** | **28.21%** | **21.93%** | **\$380,412** | **+1,130 bps** | **+1,107 bps** | **0.0x (Cash Only)** |
| 1996–2026 | **Warren Buffett / Berkshire (`BRK-A`)** | 10.86% | 10.86% | \$22,341 | +23 bps | Baseline | ~1.6x (Float) |
| 1996–2026 | **S&P 500 Total Return Index** | 10.63% | 10.63% | \$20,958 | Baseline | -23 bps | 1.0x (Index) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1971–2026** (55.5Y, Stagflation Era) | **MUNGER Factor (15% Cap, Strict Exit)** | **22.53%** | **16.73%** | **\$6,452,334** | **+570 bps** | Parity | **0.0x (Cash Only)** |
| 1965–2024 (59.0Y, Historic BRK) | **Warren Buffett / Berkshire Hathaway** | ~19.80% | ~19.80% | ~\$35–\$40M | +960 bps | Baseline | ~1.6x (Float) |
| 1971–2026 (55.5Y) | **S&P 500 Total Return Index** | 11.03% | 11.03% | \$377,810 | Baseline | -877 bps | 1.0x (Index) |

> [!NOTE]
> **The Leverage Parity Reality:** Frazzini, Kabiller, and Pedersen (*Buffett's Alpha*, 2018) proved that Berkshire Hathaway's historic ~19.8% gross CAGR was driven by ~1.6:1 leverage funded by negative-cost insurance float. On an **unlevered** basis, Buffett's gross return was ~13.5%–14.0%. In contrast, the MUNGER system operates **completely unlevered (0.0x debt/float)** with a strict 15% maximum position cap and Treasury cash drag, yet delivers **16.73% to 20.19% net post-tax compounding**—directly mirroring Berkshire's historic gross record while outpacing post-2008 Berkshire (9.79%) by 5.5x to 9.4x in terminal wealth.

### Academic Value-Investing Lineage:

Where does MUNGER fit within the quantitative and fundamental literature?

| Methodology / Paper | Fundamental Metric | Valuation Anchor | Math Interaction | Survivorship / Lookahead Control | Reported Alpha / Return |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Graham & Dodd (1934)** | Net Current Asset Value (NCAV) | $P < \frac{2}{3} \times \text{NCAV}$ | Single-hurdle | Manual historic accounting | Cigar-butt liquidations |
| **Piotroski (2000)** | 9-point F-Score (accounting signals) | High Book-to-Market ($B/M$) | Additive linear ($0 \dots 9$) | Annual Compustat rebalancing | +7.5% annually vs low BM |
| **Greenblatt (2005)** | Return on Capital ($\text{EBIT}/(\text{WC}+\text{NFA})$) | Earnings Yield ($\text{EBIT}/\text{EV}$) | Additive ordinal ranking | Top decile annual rebalance | +16.0% CAGR (1988–2004) |
| **Novy-Marx (2013)** | Gross Profits-to-Assets ($\text{GP}/A$) | Book-to-Market ($B/M$) | 2D Bivariate sorting | Fama-French universe | Proves quality is orthogonal to value |
| **Asness et al. (2019, QMJ)** | Quality Score (Profit, Growth, Safety) | Market Price ($P$) | Additive standard $Z$-scores | Global equity universe | +4.6% annualized long/short alpha |
| **Frazzini et al. (2018)** | Low-Beta, High-Quality Alpha | P/B, P/E, Float | Multi-factor linear regression | Decades of BRK filings | Berkshire ~19.8% = ~1.6:1 leverage on quality |
| **MUNGER Factor (2026)** | **4 Pillars (CAGR, ROIC, Solvency, Yield)** | **TTM Earnings Yield ($E/P$)** | **Conjunctive Multiplicative** | **45d SEC lag, -5 bps slip, -350 bps survivorship** | **+570 to +865 bps net post-tax alpha; 23.69% post-tax CAGR** |

### The Three Buy-Hold-Sell Execution Disciplines:

1. **The 365-Day LTCG Tax Shield:**
   - *Rule:* Once an asset enters the portfolio, no exit is executed before holding duration exceeds 365 calendar days, regardless of factor re-ranking or broader market volatility.
   - *Rationale:* Eliminates ordinary income tax drag (up to 40.8%), capping tax friction at the Long-Term Capital Gains bracket (15%–23.8%). Charlie Munger: *"The big money is not in the buying and the selling, but in the waiting."*
2. **Dynamic Moat Decay Exit ($\mathcal{M} < 1.0$):**
   - *Rule:* After day 365, evaluate the Munger Multiplier. If the composite moat decays below parity ($\mathcal{M} < 1.0$), or if Net Debt / EBITDA deteriorates above $2.5\text{x}$, execute a clean Market-On-Close (MOC) exit.
   - *Rationale:* Protects compounding capital from permanent impairment while granting elite compounders multi-year holding runways.
3. **Strict No Stop-Loss Policy:**
   - *Rule:* Never set price-based trailing stop-losses. Price drawdowns unaccompanied by fundamental moat decay trigger rebalancing additions rather than forced exits.
   - *Rationale:* Stop-losses convert temporary liquidity dislocations into permanent capital losses. Charlie Munger: *"If you're not willing to react with equanimity to a market price decline of 50% two or three times a century, you're not fit to be a common shareholder and you deserve the mediocre result you're going to get."*

---

## 6. The Options Supercharger: Vertical Bull Call Spreads

For allocators seeking extreme capital efficiency, outright share purchases tie up 100% of capital ($S_0$), while naked calls bleed value via theta decay.

### The 90–120 DTE Sweet Spot
By deploying **vertical bull call spreads** ($95\text{C}/110\text{C}$):
- **Cash Outlay Reduction:** 14.6x to 16.5x less cash required vs outright shares.
- **Theta Neutrality:** Net daily decay is just $-\$0.004$/day.
- **Realized ROC:** +133% to +154% return on risk capital.

### The 80/20 Institutional Model
- **80% in Risk-Free 3-Month Treasury Bills:** Yielding 4.5% risk-free.
- **20% in MUNGER Call Spreads:** Across 5–8 dislocated compounders.
- **Asymmetry:**
  - In normal quarters: Portfolio compounds at **+27.6% per quarter (+160% annualized)**.
  - In a total black-swan crash (100% option loss): Maximum portfolio drawdown is mathematically capped at **-19.10%**, preserving 80% liquid cash to deploy at market troughs.

---

## 7. Production Hardening: Live Scraping & Fidelity Reconciliation (v2.2)

### A. Staleness Circuit Breaker (`check_data_freshness` & `refresh_live_intraday_prices`)
Every time you run `scan_strike_zone.py`, the engine checks the timestamp of the latest price bar against the current market clock (accounting for NYSE/Nasdaq hours and weekend closures). If the local cache is stale (> 18 hours lag), it automatically triggers a multi-threaded `yfinance.download(..., period="5d", interval="1d")` batch pull to append live session prices before scoring.

### B. SEC Form 4 & Finviz Insider Conviction Engine (`fetch_live_insider_activity`)
Directly ingests SEC Form 4 XML filings via `edgartools` (falling back to `finvizfinance`), strictly isolating discretionary Open-Market Purchases (Code P) and Sales (Code S) while discarding non-discretionary noise (Code F tax withholdings, Code M option exercises, Code A grants, Code G gifts):
- **Cluster Buying Boost (1.15x):** Awarded when $\ge 2$ distinct insiders buy $\ge \$250\text{k}$ on the open market (or aggregate/single open-market buy $\ge \$2\text{M}$ with zero sales).
- **Heavy C-Suite Distribution Haircut (0.70x):** Applied when C-suite executives (CEO, CFO, COO, President, Chairman) sell $\ge \$20\text{M}$ on the open market with zero insider buys (e.g. automatically catching Micron CEO Sanjay Mehrotra's \$46.33M+ open-market sale).

### C. Fidelity Tax-Lot Reconciler (`parse_fidelity_positions_csv`)
Drop any Fidelity `Portfolio_Positions_*.csv` export into your repository root or `data/` folder and execute `uv run python scan_strike_zone.py`. The engine:
1. Strips core money-market sweep rows (`SPAXX**`, `FDRXX**`, `FCASH**`).
2. Overlays live intraday quotes over stale CSV prices.
3. Computes holding duration against the 365-day LTCG threshold.
4. Generates exact institutional execution orders:
   - **`STRONG HOLD / ADD`**: Tier-1 Compounder in Strike Zone ($\mathcal{M} \ge 2.0$ and passed all factor hurdles).
   - **`HOLD FOR TAX DEFERRAL (Xd to LTCG)`**: Sub-hurdle score with short-term capital gains; defers sale until LTCG reclassification to avoid ordinary income tax drag.
   - **`SELL (MOC)`**: Position is either LTCG (safe to harvest at Market-On-Close) or has an unrealized loss (immediate tax-loss harvest with zero tax drag).

---

## 8. Repository Architecture

```
munger-factor-system/
├── README.md                                    # Master documentation & empirical overview
├── LICENSE                                      # MIT Open Source License
├── pyproject.toml                               # Python package dependencies & build metadata
├── munger_engine.py                             # Shared vectorized & zero-lookahead core engine (v2.3)
├── scan_strike_zone.py                          # Live strike-zone scanner & tax-lot reconciler (v2.3)
├── watch_fidelity.py                            # Background file-watcher daemon for Fidelity CSV exports
├── audit_buffett_and_survivorship.py            # Master Warren Buffett Showdown & survivorship audit
├── run_survivorship_free_backtest.py            # Master survivorship-free simulation engine
├── reproduce_all.py                             # Turnkey reproduction pipeline (1-command runner)
├── test_causality_and_performance.py            # Automated causality, unit test & benchmark suite
├── start_server.ps1 / start_server.bat          # 1-click launchers for Munger Factor Web Terminal
├── web/                                         # 7-stage interactive Web Terminal (HTML5/ES6/Chart.js)
│   ├── index.html                               # Responsive control center UI
│   ├── app.js                                   # State manager & reactive filter pipeline
│   └── styles.css                               # Bloomberg/CapitalIQ dark theme stylesheet
├── archive/
│   └── research_experiments/                   # 28 historical research & sensitivity sweep scripts
│       ├── run_debt_metrics_backtest.py         # 4-metric solvency tournament (Net Debt / EBITDA victor)
│       ├── run_buffett_showdown_backtest.py     # Initial multi-cycle Buffett showdown test
│       ├── optimize_1y_threshold_1971_2008.py   # Walk-forward 1-Year hurdle optimization engine
│       ├── test_munger_sell_criteria.py         # Dynamic sell criteria parameter sweep
│       ├── simulate_call_spreads.py             # Black-Scholes call spread simulator
│       ├── optimize_call_tenors.py              # 30 to 365 DTE tenor optimization matrix
│       └── run_audited_institutional_analysis.py# 15% vs 20% position cap alpha bridge
├── data/
│   ├── historical_prices_1970_2026.pkl.xz       # 55.5-year daily OHLCV dataset
│   ├── historical_quarterly_eps.json            # 11,263 corporate quarterly EPS filings
│   ├── sp100_nasdaq100_universe.json            # 176 verified constituent fundamental records
│   ├── Portfolio_Positions_Sample.csv           # Fidelity tax-lot reconciliation sample fixture
│   └── survivorship_free_alpha_uplift_results.json # Full serialized backtest output metrics
└── docs/
    ├── whitepaper.pdf                           # Complete 12-page academic whitepaper (Tectonic compiled)
    ├── whitepaper.tex                           # LaTeX source with mathematical derivations & citations
    ├── whitepaper_investor_article.md           # Investor-style memorandum version
    └── figures/                                 # Publication-quality charts (300 DPI)
        ├── munger_survivorship_free_alpha_uplift.png
        ├── munger_audited_compounding_curve.png
        ├── munger_sell_criteria_optimization.png
        └── munger_1y_threshold_optimization.png
```

---

## 9. Open Science: How to Critique & Extend

Science advances through replication, critique, and falsification:
1. **Test Alternative Sell Rules:** Modify `m_sell` in `run_survivorship_free_backtest.py` (e.g. test $\mathcal{M} < 1.0$ or $\mathcal{M} < 2.0$).
2. **Stress-Test Position Limits:** Adjust `max_cap` between 10% (ultra-diversified 10-stock minimum) and 25% (concentrated institutional).
3. **Incorporate Alternative Tax Regimes:** Test zero-tax profiles (IRAs, 401ks, offshore endowments) vs ultra-high short-term brackets (40.8%).
4. **Evaluate Universe Expansions:** Expand into mid-caps (S&P 400) or international compounders.

---

## 10. Academic Citation

If you use this factor model, backtest engine, or options methodology in your research, please cite:

```bibtex
@article{munger2026factor,
  title={The MUNGER Factor Strategy: Engineering Asymmetric Compounding: How Multi-Factor Multipliers, Dynamic Tax Deferral, and MOC Execution Deliver Generational Alpha (1971--2026)},
  author={Quantitative Research and Systematic Strategies},
  journal={Internal Strategy Whitepaper},
  year={2026},
  month={October},
  url={https://github.com/rkenaley/munger-factor-system}
}
```
