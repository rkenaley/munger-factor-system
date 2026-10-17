"""Local HTTP Web Server & API for Fidelity Control Center."""

from __future__ import annotations
import http.server
import socketserver
import json
import io
import time
import sys
from pathlib import Path
from typing import Dict, Any

from .control_center import run_pipeline
from .fidelity_connector import generate_fidelity_deeplink, parse_fidelity_positions_csv, sync_config_with_fidelity
from .config import DEFAULT_CONFIG_PATH

PORT = 8505
BASE_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = BASE_DIR / "web"
OUTPUT_DIR = BASE_DIR / "output"
BACKTEST_CACHE = {}


class ControlCenterHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler serving the web UI and REST endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # Enable CORS for local integration
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.path = "/index.html"
            return super().do_GET()

        # Serve output files (daily_report.json, CSVs)
        if self.path.startswith("/output/") or self.path.startswith("../output/"):
            clean_path = self.path.replace("../output/", "").replace("/output/", "").split("?")[0]
            target_file = OUTPUT_DIR / clean_path
            if target_file.exists():
                self.send_response(200)
                if clean_path.endswith(".json"):
                    self.send_header("Content-Type", "application/json")
                elif clean_path.endswith(".csv"):
                    self.send_header("Content-Type", "text/csv")
                self.end_headers()
                with open(target_file, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, f"Output file {clean_path} not found")
                return

        # API: Get latest report
        if self.path == "/api/report":
            report_file = OUTPUT_DIR / "daily_report.json"
            if not report_file.exists():
                run_pipeline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with open(report_file, "rb") as f:
                self.wfile.write(f.read())
            return

        # API: Run or retrieve backtest
        if self.path.startswith("/api/backtest"):
            from urllib.parse import urlparse, parse_qs
            from .backtester import run_screener_backtest
            query = parse_qs(urlparse(self.path).query)
            mode = query.get("mode", ["relaxed"])[0]
            period = query.get("period", ["2y"])[0]
            cutoff = float(query.get("cutoff", ["10.0"])[0])
            universe = query.get("universe", ["expanded"])[0]
            force = query.get("force", ["0"])[0] == "1"
            
            cache_key = f"{mode}_{period}_{cutoff}_{universe}"
            global BACKTEST_CACHE
            if not force and cache_key in BACKTEST_CACHE:
                res = BACKTEST_CACHE[cache_key]
            else:
                res = run_screener_backtest(period=period, mode=mode, multiplier_cutoff=cutoff, universe=universe)
                BACKTEST_CACHE[cache_key] = res
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Options recommendations & collar tickets
        if self.path == "/api/options":
            from .options_engine import generate_options_recommendations
            report_file = OUTPUT_DIR / "daily_report.json"
            curr_prices = {}
            if report_file.exists():
                try:
                    with open(report_file, "r", encoding="utf-8") as rf:
                        rep = json.load(rf)
                        for m in rep.get("metrics", []):
                            curr_prices[m["ticker"]] = m.get("current_price", 100.0)
                except Exception:
                    pass
            if not curr_prices or 'NVDA' not in curr_prices:
                curr_prices.update({'NVDA': 140.0, 'MU': 110.0, 'GEV': 280.0, 'WDC': 75.0, 'DASH': 155.0, 'SNPS': 560.0, 'IDXX': 490.0})
            
            res = generate_options_recommendations(curr_prices, {})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Munger System Status & Freshness
        if self.path.startswith("/api/munger/status"):
            from .munger_api import get_munger_status_api
            res = get_munger_status_api()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Fidelity Automatic File-Watcher Status
        if self.path.startswith("/api/munger/watcher-status"):
            from .watcher import WATCHER_STATE
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(WATCHER_STATE).encode("utf-8"))
            return

        # API: Munger Options Collars
        if self.path.startswith("/api/munger/options-collars"):
            from .munger_api import get_options_collars_api
            res = get_options_collars_api()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Health check
        if self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ONLINE", "port": PORT, "version": "2.2.0"}).encode("utf-8"))
            return

        # Default static file serving from web/
        return super().do_GET()

    def do_POST(self):
        # API: Munger Live Screener Scan
        if self.path == "/api/munger/scan":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                params = json.loads(body.decode("utf-8", errors="replace")) if body.strip() else {}
            except Exception:
                params = {}
            from .munger_api import run_munger_scan_api
            res = run_munger_scan_api(params)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Munger Refresh Live Prices via yfinance
        if self.path == "/api/munger/refresh-prices":
            from .munger_api import refresh_prices_api
            res = refresh_prices_api()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Munger Fidelity Tax-Lot Reconciliation
        if self.path == "/api/munger/reconcile-fidelity":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8", errors="replace") if content_length > 0 else ""
            csv_text = None
            if body.strip().startswith("{"):
                try:
                    payload = json.loads(body)
                    csv_text = payload.get("csv_text")
                except Exception:
                    csv_text = body
            elif body.strip():
                csv_text = body
            from .munger_api import reconcile_fidelity_api
            res = reconcile_fidelity_api(csv_text)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Force check Fidelity watcher directories now
        if self.path == "/api/munger/watcher-trigger":
            from .watcher import check_and_ingest_once, WATCHER_STATE
            ingest_res = check_and_ingest_once()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp_payload = {
                "status": "SUCCESS",
                "triggered": True,
                "reconciled": ingest_res is not None,
                "watcher_state": WATCHER_STATE,
                "recon_result": ingest_res,
            }
            self.wfile.write(json.dumps(resp_payload).encode("utf-8"))
            return

        # API: Munger Fidelity Order Generator
        if self.path == "/api/munger/generate-orders":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                params = json.loads(body.decode("utf-8", errors="replace")) if body.strip() else {}
            except Exception:
                params = {}
            from .munger_api import generate_fidelity_orders_api
            res = generate_fidelity_orders_api(params)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # API: Trigger live quantitative scan
        if self.path == "/api/run":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            start_time = time.time()
            
            try:
                res = run_pipeline()
                elapsed = time.time() - start_time
                response_payload = {
                    "status": "SUCCESS",
                    "elapsed_seconds": round(elapsed, 2),
                    "result": res
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_payload).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ERROR", "message": str(e)}).encode("utf-8"))
            return

        # API: Ingest Fidelity Positions CSV
        if self.path == "/api/import-fidelity":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            
            try:
                # Handle either JSON wrapped {"csv_text": "..."} or raw CSV
                if body.strip().startswith("{"):
                    data = json.loads(body)
                    csv_text = data.get("csv_text", "")
                else:
                    csv_text = body
                    
                parsed = parse_fidelity_positions_csv(csv_text)
                sync_config_with_fidelity(parsed, DEFAULT_CONFIG_PATH)
                res = run_pipeline()
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "SUCCESS",
                    "accounts_imported": parsed['accounts_found'],
                    "total_net_worth": parsed['total_net_worth'],
                    "pipeline_result": res
                }).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ERROR", "message": str(e)}).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def start_server(port: int = PORT):
    """Starts the local control center web server."""
    # Launch Automatic Local File-Watcher Daemon in background
    try:
        from .watcher import start_fidelity_watcher
        start_fidelity_watcher(poll_interval=2.0)
    except Exception as e:
        print(f"[WARN] Could not initialize Fidelity File-Watcher Daemon: {e}")

    with ThreadedTCPServer(("", port), ControlCenterHandler) as httpd:
        print(f"\n================================================================================")
        print(f" FIDELITY CONTROL CENTER WEB TERMINAL ACTIVE")
        print(f" Web UI:       http://localhost:{port}")
        print(f" API Endpoint: http://localhost:{port}/api/run")
        print(f" File Watcher: Monitoring ~/Downloads for Portfolio_Positions_*.csv")
        print(f"================================================================================\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            httpd.shutdown()


if __name__ == "__main__":
    start_server()
