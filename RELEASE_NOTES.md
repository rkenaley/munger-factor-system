# Release Notes: MUNGER Factor System

---

## Release v2.3.0: Audited Warren Buffett Showdown, Academic Lineage, Net Debt / EBITDA Solvency Victor & Research Archival

### Major Features & Empirical Upgrades
- **Audited Warren Buffett Showdown (`audit_buffett_and_survivorship.py`)**:
  - Implemented multi-cycle head-to-head performance audit against Warren Buffett / Berkshire Hathaway (`BRK-A` / `BRK-B`).
  - **2008–2026 (GFC to Present, 18.8Y):** MUNGER delivers **23.69% net post-tax CAGR** (\$50,477 terminal wealth per \$1,000) vs. **9.79% gross CAGR** for Berkshire Hathaway (\$5,372) and **11.47%** for the S&P 500 Total Return Index (\$7,619).
  - **1996–2026 (30-Year Cycle):** MUNGER compounds at **21.93% net post-tax CAGR** (\$380,412 per \$1,000) vs. **10.86%** for Berkshire Hathaway (\$22,341) and **10.63%** for the S&P 500 (\$20,958).
  - **Unlevered Compounding Parity:** Integrates Frazzini, Kabiller, and Pedersen (*Buffett's Alpha*, 2018) findings demonstrating that Berkshire's historic ~19.8% gross return was driven by ~1.6:1 leverage funded by negative-cost insurance float. MUNGER achieves **16.73% to 20.19% net post-tax compounding** completely unlevered (0.0x debt/float) with a 15% position cap and Treasury cash drag.
  - **Survivorship Penalty Stress Test:** Incorporates an audited **-350 bps annual delisting haircut** stress test, proving that even under aggressive survivorship penalties, MUNGER outperforms Buffett post-2008 by **+1,040 bps net post-tax alpha**.
- **Academic Value-Investing Lineage Integration**:
  - Reconciled whitepapers (`docs/whitepaper.tex`, `docs/whitepaper.pdf`, `docs/whitepaper_investor_article.md`) with the quantitative value investing canon: Graham & Dodd (1934), Piotroski (2000), Greenblatt (2005), Novy-Marx (2013), Asness et al. (2019 QMJ), and Frazzini et al. (2018).
  - Formal academic bibliography and citations added (`\begin{thebibliography}{99}`).
  - Enriched with 5 iconic Charlie Munger quotes on mental models, "bullshit earnings" (EBITDA), tax patience ("the big money is in the waiting"), 50% market decline fortitude, and concentration vs. diversification.
- **Pillar 4 Solvency Metric Tournament Victor: Net Debt / EBITDA $\le 2.5\text{x}$**:
  - Replaced legacy debt-to-equity and net solvency ratios with Net Debt / EBITDA $\le 2.5\text{x}$ across all whitepapers, web terminals, and scanner scoring engines.
  - Resolves false exclusions of asset-light compounders with high treasury share buybacks while rigorously blocking debt-heavy capital-destructive firms.
- **Three Buy-Hold-Sell Execution Disciplines**:
  - Formally codified the 365-day LTCG Tax Shield (mandatory holding window to convert short-term gains to long-term rates), Dynamic Moat Decay Exit ($\mathcal{M} < 1.0$ or Net Debt / EBITDA $> 2.5\text{x}$), and Strict No Stop-Loss Policy.
- **80/20 Options Call Overlay Architecture**:
  - Recommends 180–365 DTE vertical bull call spreads ($95\text{C}/110\text{C}$) on top strike-zone compounders combined with 80% 3M T-Bills for extreme capital efficiency with a mathematically capped -19.10% maximum black-swan drawdown.
- **Automatic Local File-Watcher Daemon (`watch_fidelity.py`)**:
  - Added background daemon monitoring local `Downloads` directories for Fidelity CSV exports (`Portfolio_Positions_*.csv`), automatically ingesting positions and updating the web control terminal.
- **Repository Hygiene & Research Script Archival**:
  - Cleaned repository root by archiving 28 historical exploratory and parameter sweep scripts into `archive/research_experiments/`.

---

## Release v2.2.0: Hardened Production System (Staleness Circuit Breaker, SEC Form 4 Insider Engine & Fidelity Tax-Lot Reconciler)

### Audit & Architecture Upgrades
- **Staleness Circuit Breaker (`check_data_freshness` & `refresh_live_intraday_prices`)**:
  - Automatically compares the latest cached price bar timestamp against the current market clock, accounting for weekend closures and NYSE/Nasdaq trading hours.
  - If local price data lags by > 18 hours, automatically triggers a multi-threaded `yfinance.download(..., period="5d", interval="1d")` batch pull across all universe constituents to append fresh daily/intraday closes.
- **SEC Form 4 & Finviz Insider Conviction Engine (`fetch_live_insider_activity`)**:
  - Queries direct SEC EDGAR Form 4 XML filings via `edgartools` with fallback to `finvizfinance`.
  - Strictly isolates discretionary Open-Market Purchases (Code P) and Open-Market Sales (Code S), discarding non-discretionary noise: Code F (tax withholding on RSU vests), Code M (option exercises), Code A (grants), and Code G (gifts).
  - **Cluster Buying Boost (1.15x)**: Awarded when $\ge 2$ distinct insiders buy $\ge \$250\text{k}$ on the open market (or $\ge \$2\text{M}$ with zero sales).
  - **Heavy C-Suite Distribution Haircut (0.70x)**: Applied when C-suite executives (CEO, CFO, COO, President, Chairman) sell $\ge \$20\text{M}$ on the open market with zero insider buys (e.g. automatically catching Micron CEO Sanjay Mehrotra's \$46.33M+ open-market sales).
- **Fidelity Tax-Lot Reconciler (`parse_fidelity_positions_csv`)**:
  - Automatically discovers and ingests Fidelity `Portfolio_Positions_*.csv` exports from repo root or `data/`.
  - Cleanly strips money-market sweep positions (`SPAXX**`, `FDRXX**`, `FCASH**`, `CORE`).
  - Replaces stale CSV export prices with live intraday quotes and computes holding duration against the 365-day LTCG hurdle.
  - Reconciles tax lots with Munger Factor criteria, generating exact execution directives:
    - **`STRONG HOLD / ADD`**: Tier-1 Compounder in Strike Zone with intact moat.
    - **`HOLD FOR TAX DEFERRAL (Xd to LTCG)`**: Sub-hurdle score with short-term capital gains; defers sale until LTCG reclassification to avoid ordinary income tax drag.
    - **`SELL (MOC)`**: Position is either LTCG (safe to exit at MOC) or has an unrealized loss (immediate tax-loss harvest with zero tax drag).
- **"Value Trap" Cyclical Filter**:
  - Cross-sectional winsorization (2nd / 98th percentile clipping) eliminates scraper artifacts (e.g. Micron's 1-quarter YoY revenue spike misrecorded as 5-year CAGR).
  - 5-year full-cycle profitability gating blocks commoditized boom-bust cyclicals with negative trailing TTM EPS.
  - Cyclically adjusted earnings yield blends TTM EPS with 5-year median EPS.
- **Turnkey Production CLI**: Added `scan_strike_zone.py` and `munger-scan` entrypoint.

---

## Release v2.1.0: Cyclical-Trap-Immunized & Audited Whitepaper Synchronization

- **"Value Trap" Filter**: Eliminated peak-earnings cyclical trap vulnerability.
- **Cross-Sectional Winsorization**: Prevented extreme variance distortion across factor scores.
- **LaTeX Whitepaper Synchronization**: Reconciled Section 8-9 and Table 8/9 metrics.

---

## Release v2.0.0: Audited, Vectorized, Zero-Lookahead Munger Factor Engine

- **Eliminated terminal-price lookahead bias**: Replaced backward-discounting fallback with causal point-in-time estimator (`build_point_in_time_ttm_eps_matrix`) enforcing 45-day SEC reporting lag.
- **Replaced synthetic +14 bps MOC execution credit with realistic slippage (-5 bps) and terminal delisting haircut (30%)**.
- **Vectorized [dates × tickers] factor matrix**: Replaced Python loops with single NumPy/Pandas array operations.
- **Analytical $O(1)$ Black-Scholes-Merton strike inversion**: Replaced 50-step binary search with closed-form inverse normal CDF.
- **Historical US Federal LTCG tax brackets**: Applied era-specific tax rates (1971–2026).
