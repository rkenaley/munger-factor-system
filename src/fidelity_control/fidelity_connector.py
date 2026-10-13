"""Fidelity Connector: Account CSV Ingestion, Deep-Linking, and Watchlist Formatting."""

from __future__ import annotations
import csv
import io
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple


# Known Fidelity cash and money market symbols
FIDELITY_CASH_SYMBOLS = {
    'SPAXX', 'FDRXX', 'FCASH', 'FDLXX', 'SPRXX', 'FZCXX', 
    'VMSXX', 'CORE', 'CASH', 'HELD_IN_CASH', 'HELD IN CASH'
}


def generate_fidelity_deeplink(ticker: str, action: str = "BUY") -> str:
    """Generates direct URL into Fidelity's web trading ticket."""
    clean_ticker = ticker.upper().strip()
    return f"https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol={clean_ticker}"


def generate_fidelity_research_link(ticker: str) -> str:
    """Generates direct URL to Fidelity's equity research dashboard."""
    clean_ticker = ticker.upper().strip()
    return f"https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol={clean_ticker}"


def parse_fidelity_positions_csv(csv_content: str | Path) -> Dict[str, Any]:
    """
    Parses an exported Fidelity Portfolio Positions CSV file.
    
    Extracts:
    - Cash / Money Market balances (SPAXX, FDLXX, FCASH, etc.)
    - Securities holdings (shares, cost basis, current value, total gain/loss)
    - Categorization across accounts (Individual/Taxable, Roth IRA, Traditional IRA/Rollover)
    """
    if isinstance(csv_content, Path) or (isinstance(csv_content, str) and Path(csv_content).exists()):
        with open(csv_content, 'r', encoding='utf-8-sig', errors='replace') as f:
            lines = f.readlines()
    else:
        lines = csv_content.splitlines()

    # Find the header row (Fidelity CSVs often have disclaimer rows at top)
    header_idx = -1
    for idx, line in enumerate(lines):
        if "Symbol" in line and ("Quantity" in line or "Current Value" in line or "Account Name" in line):
            header_idx = idx
            break
            
    if header_idx == -1:
        # Try finding standard comma line
        for idx, line in enumerate(lines):
            if "Account Number" in line or "Symbol" in line:
                header_idx = idx
                break
                
    if header_idx == -1:
        raise ValueError("Could not detect valid Fidelity Positions CSV header row.")

    reader = csv.DictReader(lines[header_idx:])
    
    accounts: Dict[str, Dict[str, Any]] = {}
    total_net_worth = 0.0
    
    for row in reader:
        # Fidelity footer starts with notes or empty symbol
        symbol = str(row.get('Symbol', '')).strip().upper()
        if not symbol or symbol.startswith('*') or 'TOTAL' in symbol or 'DISCLAIMER' in symbol:
            continue
            
        acc_name = str(row.get('Account Name', 'Fidelity Account')).strip()
        acc_num = str(row.get('Account Number', '')).strip()
        acc_key = f"{acc_name}_{acc_num}" if acc_num else acc_name
        
        # Determine account tax type from name
        acc_lower = acc_name.lower()
        if 'roth' in acc_lower:
            tax_type = 'Roth'
        elif 'ira' in acc_lower or '401' in acc_lower or 'rollover' in acc_lower or 'traditional' in acc_lower:
            tax_type = 'Traditional'
        else:
            tax_type = 'Taxable'
            
        if acc_key not in accounts:
            accounts[acc_key] = {
                "name": acc_name,
                "account_number": acc_num,
                "tax_type": tax_type,
                "cash_usd": 0.0,
                "holdings": {},
                "total_value_usd": 0.0
            }
            
        # Parse numeric values safely
        def to_float(val: Any) -> float:
            if not val:
                return 0.0
            cleaned = str(val).replace('$', '').replace(',', '').replace('%', '').strip()
            if cleaned.startswith('(') and cleaned.endswith(')'):
                cleaned = '-' + cleaned[1:-1]
            try:
                return float(cleaned)
            except ValueError:
                return 0.0

        current_val = to_float(row.get('Current Value', 0))
        qty = to_float(row.get('Quantity', 0))
        cost_basis = to_float(row.get('Cost Basis Total', 0))
        gain_loss = to_float(row.get('Total Gain/Loss Dollar', 0))
        
        total_net_worth += current_val
        accounts[acc_key]['total_value_usd'] += current_val
        
        if symbol in FIDELITY_CASH_SYMBOLS or 'MONEY MARKET' in str(row.get('Description', '')).upper():
            accounts[acc_key]['cash_usd'] += current_val
        else:
            accounts[acc_key]['holdings'][symbol] = {
                "shares": qty,
                "est_value_usd": current_val,
                "cost_basis_usd": cost_basis,
                "unrealized_gain_usd": gain_loss,
                "description": row.get('Description', symbol)
            }

    return {
        "status": "SUCCESS",
        "accounts_found": len(accounts),
        "total_net_worth": total_net_worth,
        "accounts": accounts
    }


def sync_config_with_fidelity(parsed_data: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    """Updates config.json with actual accounts and balances imported from Fidelity."""
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # Update accounts list
    new_accounts = []
    for acc_key, acc_data in parsed_data['accounts'].items():
        acc_id = acc_key.lower().replace(' ', '_').replace('-', '_').replace('(', '').replace(')', '')
        new_accounts.append({
            "id": acc_id,
            "name": acc_data['name'],
            "broker": "Fidelity",
            "tax_type": acc_data['tax_type'],
            "current_cash_usd": round(acc_data['cash_usd'], 2),
            "holdings": acc_data['holdings']
        })

    # Preserve non-Fidelity accounts like Roots REIT or Crypto if not present in Fidelity export
    existing_taxable = next((a for a in cfg.get('accounts', []) if a.get('id') == 'taxable_brokerage'), None)
    if existing_taxable and 'holdings' in existing_taxable:
        # Check if ROOTS or Crypto exist and carry them over
        for t in ['ROOTS', 'BTC', 'ETH']:
            if t in existing_taxable['holdings']:
                # Find taxable in new accounts or create it
                taxable_target = next((a for a in new_accounts if a['tax_type'] == 'Taxable'), None)
                if taxable_target:
                    taxable_target['holdings'][t] = existing_taxable['holdings'][t]

    if new_accounts:
        cfg['accounts'] = new_accounts
        cfg['investor_profile']['total_net_worth_target'] = round(parsed_data['total_net_worth'], 2)

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)

    return cfg
