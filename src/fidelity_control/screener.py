"""Extremizing Stock Screener & Multi-Factor Quality/Value Ranking."""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, Any, List


# Seeded baseline fundamental facts for core research universe
FALLBACK_FUNDAMENTALS = {
    'AVGO': {'name': 'Broadcom Inc.', 'sector': 'Technology', 'theme': 'Custom AI ASICs & Networking', 'pe': 38.5, 'pb': 16.5, 'de': 115.0, 'sales5y': 32.4, 'fcf_margin': 0.44, 'roic_proxy': 0.22},
    'NVDA': {'name': 'NVIDIA Corporation', 'sector': 'Technology', 'theme': 'AI Compute Leadership', 'pe': 52.0, 'pb': 42.0, 'de': 21.0, 'sales5y': 58.0, 'fcf_margin': 0.48, 'roic_proxy': 0.55},
    'LRCX': {'name': 'Lam Research', 'sector': 'Technology', 'theme': 'Semiconductor Etch & Deposition', 'pe': 26.5, 'pb': 11.2, 'de': 52.0, 'sales5y': 21.5, 'fcf_margin': 0.28, 'roic_proxy': 0.35},
    'AMAT': {'name': 'Applied Materials', 'sector': 'Technology', 'theme': 'Materials Engineering & GAA', 'pe': 21.8, 'pb': 7.9, 'de': 32.0, 'sales5y': 19.8, 'fcf_margin': 0.26, 'roic_proxy': 0.32},
    'TSM': {'name': 'Taiwan Semiconductor', 'sector': 'Technology', 'theme': 'Foundry Monopoly', 'pe': 23.5, 'pb': 6.1, 'de': 31.0, 'sales5y': 33.5, 'fcf_margin': 0.38, 'roic_proxy': 0.28},
    'ASML': {'name': 'ASML Holding', 'sector': 'Technology', 'theme': 'EUV Lithography Monopoly', 'pe': 36.0, 'pb': 21.0, 'de': 16.0, 'sales5y': 30.5, 'fcf_margin': 0.31, 'roic_proxy': 0.45},
    'NOW': {'name': 'ServiceNow, Inc.', 'sector': 'Technology', 'theme': 'Enterprise AI Workflow SaaS', 'pe': 54.0, 'pb': 18.0, 'de': 18.0, 'sales5y': 24.5, 'fcf_margin': 0.32, 'roic_proxy': 0.20},
    'MSFT': {'name': 'Microsoft Corporation', 'sector': 'Technology', 'theme': 'Enterprise Cloud & Copilot', 'pe': 33.5, 'pb': 13.5, 'de': 36.0, 'sales5y': 17.5, 'fcf_margin': 0.34, 'roic_proxy': 0.30},
    'GRID': {'name': 'First Trust Smart Grid ETF', 'sector': 'Infrastructure', 'theme': 'AI Power Grid & Transformers', 'pe': 22.0, 'pb': 3.1, 'de': 65.0, 'sales5y': 18.0, 'fcf_margin': 0.15, 'roic_proxy': 0.16},
    'ARE': {'name': 'Alexandria Real Estate', 'sector': 'Real Estate', 'theme': 'Life Science Mega-Campuses', 'pe': 14.5, 'pb': 0.95, 'de': 78.0, 'sales5y': 15.2, 'fcf_margin': 0.35, 'roic_proxy': 0.09},
    'DTCR': {'name': 'Global X Data Center REITs', 'sector': 'Real Estate', 'theme': 'Data Center Landlords', 'pe': 24.0, 'pb': 2.2, 'de': 95.0, 'sales5y': 16.0, 'fcf_margin': 0.25, 'roic_proxy': 0.11},
    'SCHD': {'name': 'Schwab US Dividend Equity', 'sector': 'Large Value', 'theme': 'Quality Dividend Compounding', 'pe': 16.2, 'pb': 2.9, 'de': 55.0, 'sales5y': 12.0, 'fcf_margin': 0.18, 'roic_proxy': 0.18},
    'NEE': {'name': 'NextEra Energy', 'sector': 'Utilities', 'theme': 'Clean Energy & Regulated Power', 'pe': 17.2, 'pb': 2.1, 'de': 112.0, 'sales5y': 28.0, 'fcf_margin': 0.18, 'roic_proxy': 0.12},
    'BEPC': {'name': 'Brookfield Renewable', 'sector': 'Utilities', 'theme': 'Hydro & Solar Power Yield', 'pe': 15.8, 'pb': 1.4, 'de': 140.0, 'sales5y': 26.5, 'fcf_margin': 0.22, 'roic_proxy': 0.10},
    'CWEN': {'name': 'Clearway Energy', 'sector': 'Utilities', 'theme': 'Contracted Clean Power', 'pe': 16.5, 'pb': 1.2, 'de': 180.0, 'sales5y': 25.0, 'fcf_margin': 0.20, 'roic_proxy': 0.09},
    'VTI': {'name': 'Vanguard Total Stock Market', 'sector': 'Blend', 'theme': 'Core Total US Equity Beta', 'pe': 24.0, 'pb': 4.1, 'de': 60.0, 'sales5y': 12.5, 'fcf_margin': 0.20, 'roic_proxy': 0.16},
    'LYTE': {'name': 'Roundhill Photonics ETF', 'sector': 'Technology', 'theme': 'AI Optical Interconnects', 'pe': 31.0, 'pb': 4.8, 'de': 45.0, 'sales5y': 22.0, 'fcf_margin': 0.18, 'roic_proxy': 0.15},
    'IONQ': {'name': 'IonQ, Inc.', 'sector': 'Technology', 'theme': 'Trapped-Ion Quantum Moonshot', 'pe': 999.0, 'pb': 6.5, 'de': 5.0, 'sales5y': 85.0, 'fcf_margin': -0.80, 'roic_proxy': -0.15},
    'NBIS': {'name': 'Nebius Group N.V.', 'sector': 'Technology', 'theme': 'Pure-Play AI GPU Neo-Cloud', 'pe': 999.0, 'pb': 2.2, 'de': 2.0, 'sales5y': 45.0, 'fcf_margin': -0.20, 'roic_proxy': 0.05}
}


def run_extremizing_screener(
    universe_data: Dict[str, Dict[str, Any]],
    sales_growth_min: float = 18.0,
    max_de_limit: float = 120.0
) -> Dict[str, Any]:
    """
    Executes the Extremizing Stock Screen Parameter Algorithm.
    
    Conditions:
    - PE <= Universe Q1 (25th percentile)
    - PB <= Universe Q1 (25th percentile)
    - 5Y Sales Growth >= sales_growth_min
    - D/E <= Universe Median AND <= max_de_limit
    """
    pes = [d['pe'] for d in universe_data.values() if d['pe'] < 500]
    pbs = [d['pb'] for d in universe_data.values() if d['pb'] < 100]
    des = [d['de'] for d in universe_data.values() if d['de'] < 500]
    
    q1_pe = float(np.percentile(pes, 25)) if pes else 18.0
    q1_pb = float(np.percentile(pbs, 25)) if pbs else 2.0
    median_de = float(np.percentile(des, 50)) if des else 60.0
    
    passing = []
    failing = []
    
    for ticker, metrics in universe_data.items():
        pe_pass = metrics['pe'] <= q1_pe
        pb_pass = metrics['pb'] <= q1_pb
        sales_pass = metrics['sales5y'] >= sales_growth_min
        de_pass = metrics['de'] <= median_de and metrics['de'] <= max_de_limit
        
        passes_all = pe_pass and pb_pass and sales_pass and de_pass
        
        record = {
            "ticker": ticker,
            "name": metrics.get("name", ticker),
            "theme": metrics.get("theme", ""),
            "sector": metrics.get("sector", ""),
            "pe": metrics['pe'],
            "pb": metrics['pb'],
            "de": metrics['de'],
            "sales5y": metrics['sales5y'],
            "pe_pass": pe_pass,
            "pb_pass": pb_pass,
            "sales_pass": sales_pass,
            "de_pass": de_pass,
            "passes_all": passes_all
        }
        
        if passes_all:
            passing.append(record)
        else:
            failing.append(record)
            
    return {
        "stats": {
            "q1_pe": q1_pe,
            "q1_pb": q1_pb,
            "median_de": median_de,
            "sales_growth_min": sales_growth_min,
            "max_de_limit": max_de_limit
        },
        "passing_candidates": passing,
        "failing_candidates": failing
    }
