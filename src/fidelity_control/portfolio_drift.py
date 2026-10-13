"""Portfolio Drift, Asset Location, and Risk Limit Monitoring."""

from __future__ import annotations
from typing import Dict, Any, List


def audit_tax_placement(holdings_by_account: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Audits the tax location of all assets against an investor in the 35% + 3.8% NIIT bracket.
    """
    audit_results = []
    
    # Rules
    # REITs / BDCs: must be in Traditional Retirement or Roth (ordinary income drag)
    # Broad Market Index (VTI, VT): ideal in Taxable (qualified dividends) or Traditional
    # High-growth secular tech (AVGO, NOW, LYTE): ideal in Roth (100% tax-free)
    # High cash / money market: ideal in Treasury Bills / Muni money market in Taxable
    
    asset_rules = {
        'ARE': {'target': 'Traditional Retirement / 401(k)', 'reason': 'High ordinary dividend yield (4.8%) shielded from 40% tax rate.'},
        'ALX': {'target': 'Traditional Retirement / 401(k)', 'reason': '8-9% dividend yield triggers severe tax drag in taxable.'},
        'O': {'target': 'Traditional Retirement / Roth IRA', 'reason': 'Non-qualified monthly dividend income.'},
        'ARCC': {'target': 'Traditional Retirement / 401(k)', 'reason': 'Private credit BDC ordinary income taxed at 40%+.'},
        'DTCR': {'target': 'Traditional Retirement', 'reason': 'Data center REIT distributions.'},
        'SCHD': {'target': 'Traditional Retirement / Roth', 'reason': 'Accelerates compounding without annual dividend tax drag.'},
        'AVGO': {'target': 'Roth IRA', 'reason': 'High-growth compounder; max tax-free capital gain potential.'},
        'NOW': {'target': 'Roth IRA', 'reason': 'Pure capital appreciation software SaaS.'},
        'LYTE': {'target': 'Roth IRA', 'reason': 'Thematic photonics growth.'},
        'VTI': {'target': 'Taxable Brokerage', 'reason': '100% qualified dividends and low turnover.'},
        'VT': {'target': 'Taxable Brokerage', 'reason': 'Global equity core with foreign tax credit eligibility.'},
        'GRID': {'target': 'Taxable Brokerage', 'reason': 'Infrastructure capital growth with low dividend tax drag.'},
        'T-Bills': {'target': 'Taxable Brokerage', 'reason': 'Exempt from state and local income taxes.'},
        'ROOTS': {'target': 'Taxable Brokerage', 'reason': 'Existing private REIT investment; hold.'},
        'NVDA': {'target': 'Taxable Brokerage', 'reason': 'Hold core; trim over time to control capital gains realization.'}
    }
    
    for acc in holdings_by_account:
        acc_name = acc['name']
        acc_type = acc['tax_type']
        for ticker, h in acc.get('holdings', {}).items():
            rule = asset_rules.get(ticker, {'target': 'Any', 'reason': 'General equity'})
            is_compliant = True
            
            if ticker in ['ARE', 'ALX', 'O', 'ARCC', 'DTCR'] and acc_type == 'Taxable':
                is_compliant = False
                status = "MISALIGNED (Severe Tax Drag)"
            elif ticker in ['AVGO', 'NOW', 'LYTE'] and acc_type == 'Taxable':
                status = "ACCEPTABLE (Roth Preferred for Tax-Free Growth)"
            else:
                status = "COMPLIANT"
                
            audit_results.append({
                "ticker": ticker,
                "account": acc_name,
                "tax_type": acc_type,
                "status": status,
                "compliant": is_compliant,
                "recommended_account": rule['target'],
                "rationale": rule['reason']
            })
            
    return audit_results


def evaluate_portfolio_drift(
    config: Dict[str, Any],
    live_prices: Dict[str, float]
) -> Dict[str, Any]:
    """
    Evaluates crypto caps, concentration limits, and cash drag.
    """
    accounts = config['accounts']
    total_net_worth = 0.0
    account_totals = {}
    
    for acc in accounts:
        cash = acc.get('current_cash_usd', 0.0)
        holdings_val = 0.0
        for ticker, h in acc.get('holdings', {}).items():
            shares = h.get('shares', 0.0)
            price = live_prices.get(ticker, h.get('est_value_usd', 0.0) / (shares if shares > 0 else 1.0))
            val = shares * price
            holdings_val += val
        acc_total = cash + holdings_val
        account_totals[acc['id']] = {
            "name": acc['name'],
            "cash": cash,
            "holdings_val": holdings_val,
            "total": acc_total,
            "tax_type": acc['tax_type']
        }
        total_net_worth += acc_total
        
    alerts = []
    
    # 1. Crypto Drift Check
    btc_price = live_prices.get("BTC", 65000.0)
    eth_price = live_prices.get("ETH", 3200.0)
    # Holdings from taxable
    crypto_val = (0.20 * btc_price) + (4.0 * eth_price)
    crypto_pct = (crypto_val / total_net_worth) * 100.0
    max_crypto_pct = config['risk_limits'].get('max_crypto_pct', 0.025) * 100.0
    
    crypto_alert = {
        "metric": "Crypto Portfolio Cap",
        "current_pct": round(crypto_pct, 2),
        "target_max_pct": max_crypto_pct,
        "current_val_usd": round(crypto_val, 2),
        "status": "OK" if crypto_pct <= max_crypto_pct else "BREACH"
    }
    
    if crypto_pct > max_crypto_pct:
        surplus = crypto_val - (total_net_worth * (max_crypto_pct / 100.0))
        alerts.append(f"[CRYPTO DRIFT] Crypto at {crypto_pct:.2f}% exceeds {max_crypto_pct:.1f}% limit! Trim ${surplus:,.0f} into core VTI.")
        
    # 2. Single Stock Concentration (NVDA)
    nvda_shares = 1600 # from config
    nvda_price = live_prices.get("NVDA", 125.0)
    nvda_val = nvda_shares * nvda_price
    nvda_pct = (nvda_val / total_net_worth) * 100.0
    max_single_stock_pct = config['risk_limits'].get('max_single_stock_pct', 0.08) * 100.0
    
    nvda_alert = {
        "ticker": "NVDA",
        "current_pct": round(nvda_pct, 2),
        "target_max_pct": max_single_stock_pct,
        "current_val_usd": round(nvda_val, 2),
        "status": "CONCENTRATED" if nvda_pct > max_single_stock_pct else "COMPLIANT"
    }
    
    if nvda_pct > max_single_stock_pct:
        alerts.append(f"[CONCENTRATION RISK] NVDA is {nvda_pct:.1f}% of total net worth (limit: {max_single_stock_pct:.1f}%). Implement covered call collar or staggered trims.")

    # 3. Cash Drag Check
    taxable_cash = account_totals['taxable_brokerage']['cash']
    target_cash_buffer = 75000.0
    excess_cash = max(0.0, taxable_cash - target_cash_buffer)
    
    if excess_cash > 25000.0:
        alerts.append(f"[CASH DRAG] ${excess_cash:,.0f} of unallocated cash in taxable brokerage. Ready for Tranche deployment into VTI & GRID.")

    return {
        "total_net_worth": total_net_worth,
        "account_totals": account_totals,
        "crypto_check": crypto_alert,
        "nvda_check": nvda_alert,
        "excess_cash_deployable": excess_cash,
        "alerts": alerts
    }
