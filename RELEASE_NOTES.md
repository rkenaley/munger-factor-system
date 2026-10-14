# Release Notes: MUNGER Factor System

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
- **Admiral Ackbar Cyclical-Trap Immunization**:
  - Cross-sectional winsorization (2nd / 98th percentile clipping) eliminates scraper artifacts (e.g. Micron's 1-quarter YoY revenue spike misrecorded as 5-year CAGR).
  - 5-year full-cycle profitability gating blocks commoditized boom-bust cyclicals with negative trailing TTM EPS.
  - Cyclically adjusted earnings yield blends TTM EPS with 5-year median EPS.
- **Turnkey Production CLI**: Added `scan_strike_zone.py` and `munger-scan` entrypoint.

---

## Release v2.1.0: Cyclical-Trap-Immunized & Audited Whitepaper Synchronization

- **Admiral Ackbar Filter**: Eliminated peak-earnings cyclical trap vulnerability.
- **Cross-Sectional Winsorization**: Prevented extreme variance distortion across factor scores.
- **LaTeX Whitepaper Synchronization**: Reconciled Section 8-9 and Table 8/9 metrics.

---

## Release v2.0.0: Audited, Vectorized, Zero-Lookahead Munger Factor Engine

- **Eliminated terminal-price lookahead bias**: Replaced backward-discounting fallback with causal point-in-time estimator (`build_point_in_time_ttm_eps_matrix`) enforcing 45-day SEC reporting lag.
- **Replaced synthetic +14 bps MOC execution credit with realistic slippage (-5 bps) and terminal delisting haircut (30%)**.
- **Vectorized [dates × tickers] factor matrix**: Replaced Python loops with single NumPy/Pandas array operations.
- **Analytical $O(1)$ Black-Scholes-Merton strike inversion**: Replaced 50-step binary search with closed-form inverse normal CDF.
- **Historical US Federal LTCG tax brackets**: Applied era-specific tax rates (1971–2026).
