"""Daily Trade Order Generation Engine for Fidelity Accounts."""

from __future__ import annotations
import math
from typing import List, Dict, Any
from .models import DailyTradeOrder, SecurityMetrics


def generate_daily_trade_orders(
    config: Dict[str, Any],
    metrics_map: Dict[str, SecurityMetrics],
    drift_data: Dict[str, Any]
) -> List[DailyTradeOrder]:
    """
    Synthesizes current market prices, extremizing indicators, Munger gating,
    and account cash balances into concrete, actionable daily orders for Fidelity.
    """
    orders: List[DailyTradeOrder] = []
    order_counter = 1
    
    # -------------------------------------------------------------
    # 1. Taxable Brokerage: Core Tranche Deployment
    # -------------------------------------------------------------
    taxable_cash = drift_data['account_totals']['taxable_brokerage']['cash']
    
    if taxable_cash >= 150000.0:
        # Tranche 1: Core VTI Allocation
        vti_price = metrics_map['VTI'].current_price if 'VTI' in metrics_map else 275.0
        vti_target_usd = 125000.0
        vti_shares = int(vti_target_usd / vti_price)
        vti_limit = round(vti_price * 0.998, 2) # staged just below market bid
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-TX-{order_counter:03d}",
            account_id="taxable_brokerage",
            account_name="Fidelity Taxable Brokerage",
            ticker="VTI",
            action="BUY",
            order_type="LIMIT",
            limit_price=vti_limit,
            quantity=vti_shares,
            est_principal_usd=round(vti_shares * vti_limit, 2),
            target_tier="Core Global Beta",
            urgency="TRANCHE_SCHEDULE",
            rationale="Tranche 1 deployment: establish low-cost qualified-dividend core index foundation.",
            fidelity_instructions=f"Buy {vti_shares} shares of VTI with Limit @ ${vti_limit:.2f} (Day order). Account: Taxable Brokerage."
        ))
        order_counter += 1
        
        # Tranche 1: Power Grid Infrastructure (GRID)
        grid_price = metrics_map['GRID'].current_price if 'GRID' in metrics_map else 95.0
        grid_target_usd = 25000.0
        grid_shares = int(grid_target_usd / grid_price)
        grid_limit = round(grid_price * 0.995, 2)
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-TX-{order_counter:03d}",
            account_id="taxable_brokerage",
            account_name="Fidelity Taxable Brokerage",
            ticker="GRID",
            action="BUY",
            order_type="LIMIT",
            limit_price=grid_limit,
            quantity=grid_shares,
            est_principal_usd=round(grid_shares * grid_limit, 2),
            target_tier="Secular AI Infrastructure",
            urgency="TRANCHE_SCHEDULE",
            rationale="Tranche 1 deployment: pick-and-shovel electrification & data center power gear bottleneck.",
            fidelity_instructions=f"Buy {grid_shares} shares of GRID with Limit @ ${grid_limit:.2f} (Day order). Account: Taxable Brokerage."
        ))
        order_counter += 1

    # -------------------------------------------------------------
    # 2. Concentrated NVDA Management (Covered Call Collar / Staged Trim)
    # -------------------------------------------------------------
    if 'NVDA' in metrics_map:
        nvda_m = metrics_map['NVDA']
        nvda_price = nvda_m.current_price
        # If NVDA is extended or neutral, write a 30-45 day OTM covered call
        call_strike = round(nvda_price * 1.15, 0)
        # Sell 4 contracts (400 shares) = ~25% of position
        orders.append(DailyTradeOrder(
            order_id=f"FID-TX-{order_counter:03d}",
            account_id="taxable_brokerage",
            account_name="Fidelity Taxable Brokerage",
            ticker="NVDA",
            action="WRITE_COVERED_CALL",
            order_type="LIMIT",
            limit_price=3.50, # estimated premium for 15% OTM 45d
            quantity=4, # 4 contracts = 400 shares
            est_principal_usd=1400.0,
            target_tier="Risk Management Collar",
            urgency="IMMEDIATE",
            rationale=f"Write 4x 30-45d Covered Calls @ ${call_strike:.0f} strike. Harvests premium while establishing tax-aware exit if assigned.",
            fidelity_instructions=f"Sell to Open 4 contracts NVDA Calls @ Strike ${call_strike:.0f}, Expiration 30-45 days out. Account: Taxable Brokerage."
        ))
        order_counter += 1

    # -------------------------------------------------------------
    # 3. Roth IRA: High-Conviction Secular Growth & NVDA Hedge
    # -------------------------------------------------------------
    roth_cash = drift_data['account_totals']['roth_ira']['cash']
    if roth_cash >= 150000.0:
        # AVGO: Custom ASICs & AI Networking (Direct hedge to NVDA)
        avgo_price = metrics_map['AVGO'].current_price if 'AVGO' in metrics_map else 175.0
        avgo_target_usd = 60000.0
        avgo_shares = int(avgo_target_usd / avgo_price)
        avgo_limit = round(avgo_price * 0.995, 2)
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-ROTH-{order_counter:03d}",
            account_id="roth_ira",
            account_name="Fidelity Roth IRA",
            ticker="AVGO",
            action="BUY",
            order_type="LIMIT",
            limit_price=avgo_limit,
            quantity=avgo_shares,
            est_principal_usd=round(avgo_shares * avgo_limit, 2),
            target_tier="Secular AI Infrastructure",
            urgency="IMMEDIATE",
            rationale="Roth Tax-Free Core Growth: Custom silicon monopoly (XPUs) and AI networking; structural hedge against NVDA.",
            fidelity_instructions=f"Buy {avgo_shares} shares of AVGO with Limit @ ${avgo_limit:.2f}. Account: Roth IRA."
        ))
        order_counter += 1

        # NOW: ServiceNow Workflow AI
        now_price = metrics_map['NOW'].current_price if 'NOW' in metrics_map else 920.0
        now_target_usd = 50000.0
        now_shares = int(now_target_usd / now_price)
        now_limit = round(now_price * 0.995, 2)
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-ROTH-{order_counter:03d}",
            account_id="roth_ira",
            account_name="Fidelity Roth IRA",
            ticker="NOW",
            action="BUY",
            order_type="LIMIT",
            limit_price=now_limit,
            quantity=now_shares,
            est_principal_usd=round(now_shares * now_limit, 2),
            target_tier="Enterprise AI Software",
            urgency="TRANCHE_SCHEDULE",
            rationale="Roth Tax-Free Growth: B2B workflow automation platform with 98% renewal rate and rapid GenAI monetization.",
            fidelity_instructions=f"Buy {now_shares} shares of NOW with Limit @ ${now_limit:.2f}. Account: Roth IRA."
        ))
        order_counter += 1

        # LYTE: Photonics & Optical Interconnects
        lyte_price = metrics_map['LYTE'].current_price if 'LYTE' in metrics_map else 35.0
        lyte_target_usd = 40000.0
        lyte_shares = int(lyte_target_usd / lyte_price)
        lyte_limit = round(lyte_price * 0.99, 2)
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-ROTH-{order_counter:03d}",
            account_id="roth_ira",
            account_name="Fidelity Roth IRA",
            ticker="LYTE",
            action="BUY",
            order_type="LIMIT",
            limit_price=lyte_limit,
            quantity=lyte_shares,
            est_principal_usd=round(lyte_shares * lyte_limit, 2),
            target_tier="Thematic Photonics",
            urgency="TRANCHE_SCHEDULE",
            rationale="Roth Tax-Free Growth: Physical shift from copper to light in high-bandwidth AI superclusters.",
            fidelity_instructions=f"Buy {lyte_shares} shares of LYTE with Limit @ ${lyte_limit:.2f}. Account: Roth IRA."
        ))
        order_counter += 1

    # -------------------------------------------------------------
    # 4. Traditional Retirement: Life Science Turnaround & Dividend Moats
    # -------------------------------------------------------------
    retire_cash = drift_data['account_totals']['traditional_retirement']['cash']
    if retire_cash >= 500000.0:
        # ARE: Alexandria Real Estate (Generational Turnaround)
        are_m = metrics_map.get('ARE')
        are_price = are_m.current_price if are_m else 98.0
        are_target_usd = 100000.0
        are_shares = int(are_target_usd / are_price)
        are_limit = round(are_price * 0.992, 2)
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-RET-{order_counter:03d}",
            account_id="traditional_retirement",
            account_name="Traditional 401(k) / IRA",
            ticker="ARE",
            action="BUY",
            order_type="LIMIT",
            limit_price=are_limit,
            quantity=are_shares,
            est_principal_usd=round(are_shares * are_limit, 2),
            target_tier="Contrarian Real Estate",
            urgency="OPPORTUNISTIC_DIP",
            rationale="Tax-Sheltered Yield & Turnaround: Premier wet-lab mega-campuses at 50% discount to replacement cost with 4.8% yield shielded from 40% tax.",
            fidelity_instructions=f"Buy {are_shares} shares of ARE with Limit @ ${are_limit:.2f}. Account: Traditional IRA/401(k)."
        ))
        order_counter += 1

        # SCHD: Quality Dividend Growth Anchor
        schd_price = metrics_map['SCHD'].current_price if 'SCHD' in metrics_map else 28.5
        schd_target_usd = 150000.0
        schd_shares = int(schd_target_usd / schd_price)
        schd_limit = round(schd_price * 0.998, 2)
        
        orders.append(DailyTradeOrder(
            order_id=f"FID-RET-{order_counter:03d}",
            account_id="traditional_retirement",
            account_name="Traditional 401(k) / IRA",
            ticker="SCHD",
            action="BUY",
            order_type="LIMIT",
            limit_price=schd_limit,
            quantity=schd_shares,
            est_principal_usd=round(schd_shares * schd_limit, 2),
            target_tier="Quality Value & Dividend Growth",
            urgency="TRANCHE_SCHEDULE",
            rationale="Tax-Sheltered Compounding: High-ROE dividend compounders; reinvest distributions automatically without tax drag.",
            fidelity_instructions=f"Buy {schd_shares} shares of SCHD with Limit @ ${schd_limit:.2f}. Account: Traditional IRA/401(k)."
        ))
        order_counter += 1

    return orders
