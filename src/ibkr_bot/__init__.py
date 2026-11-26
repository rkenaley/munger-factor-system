"""
Dual-Tranche Tax-Aware IBKR Algorithmic Trading Bot (`ibkr_bot`).

Architecture validated by ruthless adversarial backtesting & Quant Critic #1:
  - Taxable Tranche: Core ETF compounding (ITOT/SCHB, SCHG/VUG — zero Fidelity
    overlap) + automated cross-index Tax-Loss Harvesting + state-tax-exempt SGOV
    cash sweep + strictly VRP-gated Section 1256 European XSP put spreads.
  - Roth IRA Tranche: Tax-free Core Growth ETFs (VOO, VGT — zero Taxable
    overlap) + USFR cash sweep + drift rebalancing + strictly VRP-gated XSP
    put spreads (auto-skipped while account NAV is too small for 2.5% cap).
  - Global Risk Firewall: Configurable COI sector hard block, IRS Rev. Rul. 2008-5
    cross-account 61-day wash-sale lock, IRC §1092(c)(4) QCC/LTCG protection,
    Fidelity shadow CSV awareness, circuit breaker, and idempotent SQLite ledger.
  - Advisory Only: Munger Strike-Zone & 3/4 Near-Miss signals reported for manual
    conviction only (zero automated single-stock churn).
"""

__version__ = "1.0.0"
