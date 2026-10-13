"""Fidelity Control Center - Model Context Protocol (MCP) Server.

Enables AI agents (Antigravity, Claude, etc.) to query portfolio state,
run extremizing scans, generate Fidelity orders, and sync account balances.
"""

from __future__ import annotations
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

from .control_center import run_pipeline
from .fidelity_connector import generate_fidelity_deeplink, parse_fidelity_positions_csv, sync_config_with_fidelity
from .munger_gating import evaluate_munger_gating
from .screener import run_extremizing_screener, FALLBACK_FUNDAMENTALS
from .config import DEFAULT_CONFIG_PATH

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("fidelity-mcp")

TOOLS = [
    {
        "name": "get_portfolio_state",
        "description": "Returns current multi-account portfolio balances, cash drag, NVDA concentration, and drift alerts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "run_daily_scan",
        "description": "Executes the full quantitative cycle (Extremizing index, Munger 200w gating, DCF margin-of-safety, and Fidelity daily orders).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force_refresh": {
                    "type": "boolean",
                    "description": "Whether to force fresh market quotes and recalculate all metrics."
                }
            }
        }
    },
    {
        "name": "get_daily_orders",
        "description": "Returns today's actionable order tickets formatted for manual entry or review on Fidelity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_filter": {
                    "type": "string",
                    "description": "Optional account name filter (e.g., 'Taxable', 'Roth', 'Traditional')."
                }
            }
        }
    },
    {
        "name": "check_munger_gating",
        "description": "Checks whether a security is within Charlie Munger's 200-week moving average gating buy zone (Price <= 1.05x SMA200w).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., 'AVGO', 'ARE', 'TSM', 'ASML', 'NEE')."
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_fidelity_deeplink",
        "description": "Generates direct web links to open the Fidelity Trade Ticket and Research Dashboard for a symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., 'VTI', 'NVDA', 'GRID', 'ARE')."
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "sync_fidelity_positions",
        "description": "Ingests exported Fidelity Positions CSV text and updates actual account cash and equity holdings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_text": {
                    "type": "string",
                    "description": "Raw text content of an exported Fidelity Portfolio Positions CSV file."
                }
            },
            "required": ["csv_text"]
        }
    },
    {
        "name": "screen_extremizing_candidates",
        "description": "Executes the Extremizing Stock Screener filtering for low P/E, low P/B, high sales growth, and low debt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sales_growth_min": {
                    "type": "number",
                    "description": "Minimum 5-year sales growth percentage (default: 18.0)."
                },
                "max_de_limit": {
                    "type": "number",
                    "description": "Maximum Debt-to-Equity limit percentage (default: 120.0)."
                }
            }
        }
    }
]


def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Handles tool execution for MCP."""
    try:
        if name == "get_portfolio_state":
            report_path = Path(__file__).resolve().parents[2] / "output" / "daily_report.json"
            if not report_path.exists():
                run_pipeline()
            with open(report_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            summary = data.get("portfolio_summary", {})
            return [{"type": "text", "text": json.dumps(summary, indent=2)}]

        elif name == "run_daily_scan":
            res = run_pipeline()
            return [{
                "type": "text",
                "text": f"Scan completed successfully.\nTotal Balance Sheet: ${res['total_net_worth']:,.2f}\nOrders Generated: {res['orders_count']}\nAlerts Configured: {res['alerts_count']}\nFiles exported to output directory."
            }]

        elif name == "get_daily_orders":
            report_path = Path(__file__).resolve().parents[2] / "output" / "daily_report.json"
            if not report_path.exists():
                run_pipeline()
            with open(report_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            orders = data.get("daily_orders", [])
            acc_filter = arguments.get("account_filter", "").lower()
            if acc_filter:
                orders = [o for o in orders if acc_filter in o.get("account_name", "").lower()]
            return [{"type": "text", "text": json.dumps(orders, indent=2)}]

        elif name == "check_munger_gating":
            ticker = arguments.get("ticker", "").upper()
            report_path = Path(__file__).resolve().parents[2] / "output" / "daily_report.json"
            if not report_path.exists():
                run_pipeline()
            with open(report_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metrics = data.get("metrics", {}).get(ticker)
            if not metrics:
                return [{"type": "text", "text": f"Ticker {ticker} not found in monitored universe."}]
            return [{
                "type": "text",
                "text": (
                    f"Munger Gating Check for {ticker} ({metrics.get('name', '')}):\n"
                    f"• Current Price: ${metrics.get('current_price', 0):.2f}\n"
                    f"• 200-Week SMA: ${metrics.get('sma200w', 0):.2f}\n"
                    f"• Buy Threshold (1.05x SMA): ${metrics.get('sma200w', 0) * 1.05:.2f}\n"
                    f"• Distance to 200w SMA: {metrics.get('dist_200w_pct', 0):+.1f}%\n"
                    f"• Gating Status: {'OPEN (BUY ZONE)' if metrics.get('munger_gating_open') else 'CLOSED (HOLD/ACCUMULATE CASH)'}\n"
                    f"• Master Signal: {metrics.get('master_signal', 'HOLD')}"
                )
            }]

        elif name == "get_fidelity_deeplink":
            ticker = arguments.get("ticker", "").upper()
            trade_link = generate_fidelity_deeplink(ticker)
            research_link = f"https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol={ticker}"
            return [{
                "type": "text",
                "text": f"Fidelity Links for {ticker}:\n• Trade Ticket: {trade_link}\n• Research Dashboard: {research_link}"
            }]

        elif name == "sync_fidelity_positions":
            csv_text = arguments.get("csv_text", "")
            parsed = parse_fidelity_positions_csv(csv_text)
            sync_config_with_fidelity(parsed, DEFAULT_CONFIG_PATH)
            # Re-run pipeline with fresh accounts
            res = run_pipeline()
            return [{
                "type": "text",
                "text": f"Successfully synced Fidelity accounts! Found {parsed['accounts_found']} accounts with total net worth ${parsed['total_net_worth']:,.2f}. Pipeline re-executed with {res['orders_count']} updated orders."
            }]

        elif name == "screen_extremizing_candidates":
            sales_min = arguments.get("sales_growth_min", 18.0)
            max_de = arguments.get("max_de_limit", 120.0)
            res = run_extremizing_screener(FALLBACK_FUNDAMENTALS, sales_growth_min=sales_min, max_de_limit=max_de)
            return [{"type": "text", "text": json.dumps(res, indent=2)}]

        else:
            return [{"type": "text", "text": f"Error: Unknown tool '{name}'"}]

    except Exception as e:
        logger.exception("Error executing MCP tool")
        return [{"type": "text", "text": f"Error executing tool '{name}': {str(e)}"}]


def run_stdio_server():
    """Runs the standard MCP stdio JSON-RPC 2.0 loop."""
    logger.info("Starting Fidelity Control Center MCP Server over stdio...")
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            msg = json.loads(line)
            msg_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params", {})

            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {
                            "name": "fidelity-control-center",
                            "version": "1.0.0"
                        },
                        "capabilities": {
                            "tools": {}
                        }
                    }
                }
            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": TOOLS
                    }
                }
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                content = handle_call_tool(tool_name, tool_args)
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": content
                    }
                }
            elif method == "notifications/initialized":
                continue
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found"
                    }
                }

            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

        except Exception as e:
            logger.exception("Error in MCP stdio loop")


if __name__ == "__main__":
    run_stdio_server()
