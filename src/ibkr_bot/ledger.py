"""ACID SQLite persistence for idempotent orders, tax lots, open spreads, and wash-sale locks."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import AccountType
from .models import ExecutionResult, OpenSpreadPosition, OrderIntent, TaxLot, is_irs_long_term


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateLedger:
    """
    Persistent SQLite state store using WAL mode, strictly scoped by `mode`
    (`dry-run`, `paper`, `live`) so simulated runs can NEVER pollute paper/live state.

    Enforces:
      - Mode-scoped atomic order idempotency with safe retry on CANCELLED/REJECTED/ERROR
      - Full 61-day global cross-account IRS Rev. Rul. 2008-5 wash-sale tracking
        (30 days BEFORE and 30 days AFTER a Taxable loss sale!)
      - Partial-fill-safe lot splitting & FIFO lot consumption on equity sales
      - Open XSP spread inventory, IBKR reconciliation, & entry-stagger enforcement
      - Cash-flow-safe intraday HWM & rolling 30-day drawdown calculation
      - Clean context-manager (`with StateLedger(...)`) WAL handle closing
    """

    def __init__(self, db_path: Path | str, mode: str = "dry-run") -> None:
        self.db_path = Path(db_path)
        self.mode = str(mode).lower()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._closed = False
        self._init_schema()

    def __enter__(self) -> "StateLedger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            try:
                self._conn.commit()
            finally:
                self._conn.close()
                self._closed = True

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                mode TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                account_id TEXT NOT NULL,
                account_type TEXT NOT NULL,
                intent_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                filled_qty INTEGER NOT NULL DEFAULT 0,
                limit_price REAL NOT NULL,
                status TEXT NOT NULL,
                broker_order_id TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (mode, idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS fills (
                fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                account_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                fill_price REAL NOT NULL,
                mid_price REAL,
                slippage_bps REAL NOT NULL DEFAULT 0.0,
                commission_usd REAL NOT NULL DEFAULT 0.0,
                trade_date TEXT NOT NULL,
                filled_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_fills_mode_sym_date
                ON fills (mode, symbol, side, trade_date);

            CREATE TABLE IF NOT EXISTS tax_lots (
                mode TEXT NOT NULL,
                lot_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                account_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                cost_basis_per_share REAL NOT NULL,
                acquired_at TEXT NOT NULL,
                closed_at TEXT,
                close_price REAL,
                realized_pnl REAL,
                is_long_term INTEGER,
                PRIMARY KEY (mode, lot_id)
            );

            CREATE INDEX IF NOT EXISTS idx_lots_mode_sym_acq
                ON tax_lots (mode, symbol, acquired_at);

            CREATE TABLE IF NOT EXISTS open_spreads (
                mode TEXT NOT NULL,
                spread_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                account_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                expiry TEXT NOT NULL,
                short_strike REAL NOT NULL,
                long_strike REAL NOT NULL,
                right TEXT NOT NULL,
                contracts INTEGER NOT NULL,
                entry_credit REAL NOT NULL,
                max_loss_total REAL NOT NULL,
                opened_at TEXT NOT NULL,
                short_con_id INTEGER,
                long_con_id INTEGER,
                status TEXT NOT NULL DEFAULT 'OPEN',
                closed_at TEXT,
                realized_pnl REAL,
                PRIMARY KEY (mode, spread_id)
            );

            CREATE TABLE IF NOT EXISTS wash_sale_blocks (
                block_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                loss_account_id TEXT NOT NULL,
                loss_date TEXT NOT NULL,
                expires_date TEXT NOT NULL,
                realized_loss_usd REAL NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_wash_mode_sym_exp
                ON wash_sale_blocks (mode, symbol, expires_date);

            CREATE TABLE IF NOT EXISTS daily_nav (
                trade_date TEXT NOT NULL,
                account_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                nav_usd REAL NOT NULL,
                hwm_intraday_usd REAL NOT NULL,
                cash_usd REAL NOT NULL,
                options_risk_usd REAL NOT NULL,
                net_cash_flow_usd REAL NOT NULL DEFAULT 0.0,
                vix REAL,
                vrp_pts REAL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, account_id, mode)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                event_type TEXT NOT NULL,
                account_id TEXT,
                symbol TEXT,
                message TEXT NOT NULL,
                details_json TEXT
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Audit Logging
    # ------------------------------------------------------------------
    def log_event(
        self,
        event_type: str,
        message: str,
        level: str = "INFO",
        account_id: Optional[str] = None,
        symbol: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO audit_log (mode, created_at, level, event_type, account_id, symbol, message, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.mode,
                _utc_now_iso(),
                level,
                event_type,
                account_id,
                symbol,
                message,
                json.dumps(details or {}, default=str),
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Idempotent Order Lifecycle (Critic #2 Fixes #2, #5, #7)
    # ------------------------------------------------------------------
    def has_active_or_completed_order(self, idempotency_key: str) -> bool:
        row = self._conn.execute(
            "SELECT status, filled_qty FROM orders WHERE mode = ? AND idempotency_key = ?",
            (self.mode, idempotency_key),
        ).fetchone()
        if row is None:
            return False
        # Block duplicates if active, filled, or partially filled!
        if int(row["filled_qty"] or 0) > 0:
            return True
        return str(row["status"]) in {"RESERVED", "SUBMITTED", "FILLED", "PARTIALLY_FILLED", "SIMULATED"}

    def reserve_order(self, intent: OrderIntent) -> bool:
        """
        Critic #2 Fix #7: Atomically reserves an order slot by (mode, idempotency_key).
        Allows safe retry when a prior attempt ended in CANCELLED/REJECTED/ERROR/INACTIVE
        with `filled_qty == 0` (preventing permanent stop-loss deadlocks!).
        """
        now = _utc_now_iso()
        payload = {
            "rationale": intent.rationale,
            "is_risk_reducing": intent.is_risk_reducing,
            "spread_id": intent.spread_id,
            "lot_id": intent.lot_id,
            "legs": [
                {
                    "symbol": l.symbol,
                    "expiry": l.expiry,
                    "strike": l.strike,
                    "right": l.right,
                    "action": l.action,
                }
                for l in intent.legs
            ],
        }
        cur = self._conn.execute(
            """
            INSERT INTO orders (
                mode, idempotency_key, created_at, updated_at, account_id, account_type,
                intent_type, symbol, action, quantity, filled_qty, limit_price, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'RESERVED', ?)
            ON CONFLICT(mode, idempotency_key) DO UPDATE SET
                status = 'RESERVED',
                updated_at = excluded.updated_at,
                quantity = excluded.quantity,
                limit_price = excluded.limit_price,
                payload_json = excluded.payload_json
            WHERE orders.status IN ('CANCELLED', 'REJECTED', 'ERROR', 'INACTIVE')
              AND orders.filled_qty = 0
            """,
            (
                self.mode,
                intent.idempotency_key,
                now,
                now,
                intent.account_id,
                intent.account_type.value,
                intent.intent_type.value,
                intent.symbol.upper(),
                intent.action,
                int(intent.quantity),
                float(intent.limit_price),
                json.dumps(payload, default=str),
            ),
        )
        self._conn.commit()
        return cur.rowcount == 1

    def finalize_order(self, intent: OrderIntent, result: ExecutionResult) -> None:
        """
        Critic #2 Fix #2: Always records fills whenever `result.filled_qty > 0`, even if
        terminal status was PARTIALLY_FILLED or CANCELLED after a partial/racing fill!
        """
        now = _utc_now_iso()
        self._conn.execute(
            """
            UPDATE orders
               SET status = ?, broker_order_id = ?, filled_qty = ?, updated_at = ?
             WHERE mode = ? AND idempotency_key = ?
            """,
            (
                result.status,
                result.broker_order_id,
                int(result.filled_qty),
                now,
                self.mode,
                intent.idempotency_key,
            ),
        )
        if result.filled_qty > 0:
            self._conn.execute(
                """
                INSERT INTO fills (
                    mode, idempotency_key, account_id, symbol, side, quantity,
                    fill_price, mid_price, slippage_bps, commission_usd, trade_date, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.mode,
                    intent.idempotency_key,
                    intent.account_id,
                    intent.symbol.upper(),
                    intent.action,
                    int(result.filled_qty),
                    float(result.avg_fill_price),
                    intent.estimated_mid_price,
                    float(result.slippage_bps),
                    float(result.commission_usd),
                    intent.trade_date.isoformat(),
                    now,
                ),
            )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Global Cross-Account 61-Day Wash-Sale Registry (Critic #2 Fix #8)
    # ------------------------------------------------------------------
    def record_wash_sale_block(
        self,
        symbol: str,
        loss_account_id: str,
        loss_date: date,
        realized_loss_usd: float,
        window_days: int = 31,
        reason: str = "Taxable realized loss — Rev. Rul. 2008-5 cross-account wash-sale lock",
    ) -> None:
        expires = loss_date + timedelta(days=window_days)
        self._conn.execute(
            """
            INSERT INTO wash_sale_blocks (
                mode, symbol, loss_account_id, loss_date, expires_date, realized_loss_usd, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.mode,
                symbol.upper(),
                loss_account_id,
                loss_date.isoformat(),
                expires.isoformat(),
                float(realized_loss_usd),
                reason,
            ),
        )
        self._conn.commit()

    def get_active_wash_sale_block(self, symbol: str, as_of: date) -> Optional[Dict[str, Any]]:
        """FORWARD 30-day check across ALL accounts in the current mode."""
        row = self._conn.execute(
            """
            SELECT * FROM wash_sale_blocks
             WHERE mode = ?
               AND symbol = ?
               AND loss_date <= ?
               AND expires_date >= ?
             ORDER BY expires_date DESC
             LIMIT 1
            """,
            (self.mode, symbol.upper(), as_of.isoformat(), as_of.isoformat()),
        ).fetchone()
        return dict(row) if row else None

    def get_recent_acquisition(
        self,
        symbol: str,
        as_of: date,
        window_days: int = 30,
        exclude_lot_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Critic #2 Fix #8: BACKWARD 30-day check across ALL accounts (Taxable AND Roth!).
        If ANY account acquired `symbol` within `[as_of - 30d, as_of]` (other than the
        exact lot being completely liquidated), selling at a loss triggers an immediate
        retroactive IRS §1091 / Rev. Rul. 2008-5 wash sale!
        """
        start_dt = (as_of - timedelta(days=window_days)).isoformat()
        end_dt = as_of.isoformat()
        sym = symbol.upper()

        lot_row = self._conn.execute(
            """
            SELECT account_id, account_type, lot_id, acquired_at, quantity
              FROM tax_lots
             WHERE mode = ?
               AND symbol = ?
               AND acquired_at >= ?
               AND acquired_at <= ?
               AND (? IS NULL OR lot_id != ?)
             ORDER BY acquired_at DESC
             LIMIT 1
            """,
            (self.mode, sym, start_dt, end_dt, exclude_lot_id, exclude_lot_id),
        ).fetchone()
        if lot_row:
            return {
                "source": "tax_lots",
                "account_id": str(lot_row["account_id"]),
                "account_type": str(lot_row["account_type"]),
                "lot_id": str(lot_row["lot_id"]),
                "acquired_at": str(lot_row["acquired_at"]),
            }

        fill_row = self._conn.execute(
            """
            SELECT account_id, trade_date, quantity
              FROM fills
             WHERE mode = ?
               AND symbol = ?
               AND side = 'BUY'
               AND trade_date >= ?
               AND trade_date <= ?
             ORDER BY trade_date DESC
             LIMIT 1
            """,
            (self.mode, sym, start_dt, end_dt),
        ).fetchone()
        if fill_row:
            return {
                "source": "fills",
                "account_id": str(fill_row["account_id"]),
                "account_type": "UNKNOWN",
                "lot_id": None,
                "acquired_at": str(fill_row["trade_date"]),
            }
        return None

    # ------------------------------------------------------------------
    # Tax Lot Tracking (Partial-Fill Safe & FIFO Closing — Fixes #9, #10, #17, #22)
    # ------------------------------------------------------------------
    def add_tax_lot(self, lot: TaxLot) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO tax_lots (
                mode, lot_id, account_id, account_type, symbol, quantity,
                cost_basis_per_share, acquired_at, closed_at, close_price, realized_pnl, is_long_term
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
            """,
            (
                self.mode,
                lot.lot_id,
                lot.account_id,
                lot.account_type.value,
                lot.symbol.upper(),
                float(lot.quantity),
                float(lot.cost_basis_per_share),
                lot.acquired_at.isoformat(),
            ),
        )
        self._conn.commit()

    def close_tax_lot(
        self,
        lot_id: str,
        close_date: date,
        close_price: float,
        sold_qty: Optional[float] = None,
        wash_window_days: int = 31,
    ) -> Optional[float]:
        """
        Closes all or part (`sold_qty`) of a specific lot. On partial fills, splits the
        closed portion cleanly and keeps remaining shares open in the original lot!
        Uses exact IRS calendar-year LTCG rule (`is_irs_long_term`).
        """
        row = self._conn.execute(
            "SELECT * FROM tax_lots WHERE mode = ? AND lot_id = ? AND closed_at IS NULL",
            (self.mode, lot_id),
        ).fetchone()
        if not row:
            return None

        acq = date.fromisoformat(str(row["acquired_at"]))
        orig_qty = float(row["quantity"])
        qty_to_close = orig_qty if sold_qty is None else min(orig_qty, max(0.0, float(sold_qty)))
        if qty_to_close <= 1e-9:
            return 0.0

        basis = float(row["cost_basis_per_share"])
        pnl = (close_price - basis) * qty_to_close
        is_lt = 1 if is_irs_long_term(acq, close_date) else 0
        remaining = orig_qty - qty_to_close

        if remaining > 1e-6:
            # Reduce open original lot quantity and insert closed child slice
            self._conn.execute(
                "UPDATE tax_lots SET quantity = ? WHERE mode = ? AND lot_id = ?",
                (remaining, self.mode, lot_id),
            )
            child_id = f"{lot_id}-PART-{close_date.isoformat()}-{int(qty_to_close)}"
            self._conn.execute(
                """
                INSERT OR REPLACE INTO tax_lots (
                    mode, lot_id, account_id, account_type, symbol, quantity,
                    cost_basis_per_share, acquired_at, closed_at, close_price, realized_pnl, is_long_term
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.mode,
                    child_id,
                    str(row["account_id"]),
                    str(row["account_type"]),
                    str(row["symbol"]),
                    qty_to_close,
                    basis,
                    str(row["acquired_at"]),
                    close_date.isoformat(),
                    float(close_price),
                    float(pnl),
                    is_lt,
                ),
            )
        else:
            self._conn.execute(
                """
                UPDATE tax_lots
                   SET closed_at = ?, close_price = ?, realized_pnl = ?, is_long_term = ?
                 WHERE mode = ? AND lot_id = ?
                """,
                (close_date.isoformat(), float(close_price), float(pnl), is_lt, self.mode, lot_id),
            )
        self._conn.commit()

        # Automatically arm global cross-account wash-sale lock if Taxable lot closed at a loss!
        if str(row["account_type"]) == AccountType.TAXABLE.value and pnl < 0.0:
            self.record_wash_sale_block(
                symbol=str(row["symbol"]),
                loss_account_id=str(row["account_id"]),
                loss_date=close_date,
                realized_loss_usd=abs(pnl),
                window_days=wash_window_days,
            )
        return pnl

    def close_tax_lots_fifo(
        self,
        account_id: str,
        symbol: str,
        sold_qty: float,
        close_date: date,
        close_price: float,
        wash_window_days: int = 31,
    ) -> float:
        """Critic #2 Fix #22: Consumes open tax lots FIFO on any non-lot-specific equity sell."""
        rem = float(sold_qty)
        tot_pnl = 0.0
        for lot in self.get_open_tax_lots(account_id, symbol):
            if rem <= 1e-6:
                break
            take = min(rem, lot.quantity)
            pnl = self.close_tax_lot(
                lot.lot_id,
                close_date=close_date,
                close_price=close_price,
                sold_qty=take,
                wash_window_days=wash_window_days,
            )
            if pnl is not None:
                tot_pnl += pnl
            rem -= take
        return tot_pnl

    def get_open_tax_lots(self, account_id: str, symbol: Optional[str] = None) -> List[TaxLot]:
        if symbol:
            rows = self._conn.execute(
                """
                SELECT * FROM tax_lots
                 WHERE mode = ? AND account_id = ? AND symbol = ? AND closed_at IS NULL
                 ORDER BY acquired_at ASC
                """,
                (self.mode, account_id, symbol.upper()),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM tax_lots
                 WHERE mode = ? AND account_id = ? AND closed_at IS NULL
                 ORDER BY acquired_at ASC
                """,
                (self.mode, account_id),
            ).fetchall()
        return [
            TaxLot(
                lot_id=str(r["lot_id"]),
                account_id=str(r["account_id"]),
                account_type=AccountType(str(r["account_type"])),
                symbol=str(r["symbol"]),
                quantity=float(r["quantity"]),
                cost_basis_per_share=float(r["cost_basis_per_share"]),
                acquired_at=date.fromisoformat(str(r["acquired_at"])),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Open Option Spread Inventory (Mode-Scoped + Partial Close Safe)
    # ------------------------------------------------------------------
    def record_open_spread(self, spread: OpenSpreadPosition) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO open_spreads (
                mode, spread_id, account_id, account_type, symbol, expiry,
                short_strike, long_strike, right, contracts, entry_credit,
                max_loss_total, opened_at, short_con_id, long_con_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """,
            (
                self.mode,
                spread.spread_id,
                spread.account_id,
                spread.account_type.value,
                spread.symbol.upper(),
                spread.expiry,
                float(spread.short_strike),
                float(spread.long_strike),
                spread.right,
                int(spread.contracts),
                float(spread.entry_credit),
                float(spread.max_loss_total),
                spread.opened_at.isoformat(),
                spread.short_con_id,
                spread.long_con_id,
            ),
        )
        self._conn.commit()

    def close_spread(
        self,
        spread_id: str,
        close_date: date,
        realized_pnl: float,
        closed_contracts: Optional[int] = None,
        terminal_status: str = "CLOSED",
    ) -> None:
        """Handles both full closes and partial contract closes safely."""
        row = self._conn.execute(
            "SELECT contracts, max_loss_total FROM open_spreads WHERE mode = ? AND spread_id = ? AND status = 'OPEN'",
            (self.mode, spread_id),
        ).fetchone()
        if not row:
            return
        cur_c = int(row["contracts"])
        if closed_contracts is not None and 0 < closed_contracts < cur_c:
            rem_c = cur_c - closed_contracts
            new_ml = float(row["max_loss_total"]) * (rem_c / float(cur_c))
            self._conn.execute(
                """
                UPDATE open_spreads
                   SET contracts = ?, max_loss_total = ?, realized_pnl = COALESCE(realized_pnl, 0.0) + ?
                 WHERE mode = ? AND spread_id = ?
                """,
                (rem_c, new_ml, float(realized_pnl), self.mode, spread_id),
            )
        else:
            self._conn.execute(
                """
                UPDATE open_spreads
                   SET status = ?, closed_at = ?, realized_pnl = COALESCE(realized_pnl, 0.0) + ?
                 WHERE mode = ? AND spread_id = ?
                """,
                (terminal_status, close_date.isoformat(), float(realized_pnl), self.mode, spread_id),
            )
        self._conn.commit()

    def get_open_spreads(self, account_id: str) -> List[OpenSpreadPosition]:
        rows = self._conn.execute(
            """
            SELECT * FROM open_spreads
             WHERE mode = ? AND account_id = ? AND status = 'OPEN'
             ORDER BY opened_at ASC
            """,
            (self.mode, account_id),
        ).fetchall()
        return [
            OpenSpreadPosition(
                spread_id=str(r["spread_id"]),
                account_id=str(r["account_id"]),
                account_type=AccountType(str(r["account_type"])),
                symbol=str(r["symbol"]),
                expiry=str(r["expiry"]),
                short_strike=float(r["short_strike"]),
                long_strike=float(r["long_strike"]),
                right=str(r["right"]),
                contracts=int(r["contracts"]),
                entry_credit=float(r["entry_credit"]),
                max_loss_total=float(r["max_loss_total"]),
                opened_at=date.fromisoformat(str(r["opened_at"])),
                short_con_id=r["short_con_id"],
                long_con_id=r["long_con_id"],
            )
            for r in rows
        ]

    def get_last_spread_entry_date(self, account_id: str, symbol: str = "XSP") -> Optional[date]:
        row = self._conn.execute(
            """
            SELECT MAX(opened_at) AS max_dt FROM open_spreads
             WHERE mode = ? AND account_id = ? AND symbol = ?
            """,
            (self.mode, account_id, symbol.upper()),
        ).fetchone()
        if not row or not row["max_dt"]:
            return None
        return date.fromisoformat(str(row["max_dt"]))

    # ------------------------------------------------------------------
    # Daily NAV, Intraday HWM, Cash-Flow-Adjusted Drawdowns & Paper Soak
    # ------------------------------------------------------------------
    def record_daily_nav(
        self,
        trade_date: date,
        account_id: str,
        nav_usd: float,
        cash_usd: float,
        options_risk_usd: float,
        net_cash_flow_usd: float = 0.0,
        vix: Optional[float] = None,
        vrp_pts: Optional[float] = None,
    ) -> None:
        """Critic #2 Fix #24: Preserves intraday high-water mark on repeated same-day runs."""
        existing = self._conn.execute(
            """
            SELECT hwm_intraday_usd FROM daily_nav
             WHERE trade_date = ? AND account_id = ? AND mode = ?
            """,
            (trade_date.isoformat(), account_id, self.mode),
        ).fetchone()
        hwm = max(float(nav_usd), float(existing["hwm_intraday_usd"])) if existing else float(nav_usd)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO daily_nav (
                trade_date, account_id, mode, nav_usd, hwm_intraday_usd,
                cash_usd, options_risk_usd, net_cash_flow_usd, vix, vrp_pts, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_date.isoformat(),
                account_id,
                self.mode,
                float(nav_usd),
                hwm,
                float(cash_usd),
                float(options_risk_usd),
                float(net_cash_flow_usd),
                vix,
                vrp_pts,
                _utc_now_iso(),
            ),
        )
        self._conn.commit()

    def get_rolling_drawdowns(
        self, account_id: str, current_nav: float, as_of: date
    ) -> Tuple[float, float]:
        """
        Critic #2 Fix #24: Computes cash-flow-adjusted (TWR-indexed) daily AND
        rolling 30-calendar-day drawdowns, including intraday drop from today's HWM!
        """
        cutoff = (as_of - timedelta(days=30)).isoformat()
        rows = self._conn.execute(
            """
            SELECT trade_date, nav_usd, hwm_intraday_usd, net_cash_flow_usd
              FROM daily_nav
             WHERE account_id = ? AND mode = ? AND trade_date >= ? AND trade_date <= ?
             ORDER BY trade_date ASC
            """,
            (account_id, self.mode, cutoff, as_of.isoformat()),
        ).fetchall()
        if not rows or current_nav <= 0.0:
            return 0.0, 0.0

        prior_rows = [r for r in rows if str(r["trade_date"]) < as_of.isoformat()]
        today_rows = [r for r in rows if str(r["trade_date"]) == as_of.isoformat()]

        # Build cash-flow-adjusted TWR wealth index across the 30-day window
        twr_vals: List[float] = []
        twr = 1.0
        prev_nav: Optional[float] = None
        for r in prior_rows:
            nv = float(r["nav_usd"])
            cf = float(r["net_cash_flow_usd"] or 0.0)
            if prev_nav is not None and prev_nav > 0.0:
                ret = (nv - cf - prev_nav) / prev_nav
                twr *= max(0.01, 1.0 + ret)
            twr_vals.append(twr)
            prev_nav = nv

        today_cf = float(today_rows[-1]["net_cash_flow_usd"] or 0.0) if today_rows else 0.0
        today_hwm = float(today_rows[-1]["hwm_intraday_usd"]) if today_rows else current_nav

        if prev_nav is not None and prev_nav > 0.0:
            day_ret = (current_nav - today_cf - prev_nav) / prev_nav
            cur_twr = twr * max(0.01, 1.0 + day_ret)
            hwm_ret = (today_hwm - today_cf - prev_nav) / prev_nav
            hwm_twr = twr * max(0.01, 1.0 + hwm_ret)
            daily_dd = min(0.0, day_ret, (current_nav / max(1e-6, today_hwm)) - 1.0)
            peak_30 = max(twr_vals + [hwm_twr, cur_twr])
            dd_30 = min(0.0, (cur_twr / peak_30) - 1.0)
        else:
            # Day 1 intraday check against today's HWM
            daily_dd = min(0.0, (current_nav / max(1e-6, today_hwm)) - 1.0)
            dd_30 = daily_dd

        return daily_dd, dd_30

    def get_paper_soak_metrics(self, paper_db_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Reads paper-mode soak progress. When running in LIVE mode (separate DB),
        inspects `paper_db_path` safely in read-only fashion so the 90-day paper
        soak gate verifies REAL logged paper trading days!
        """
        conn = self._conn
        close_temp = False
        if self.mode != "paper" and paper_db_path is not None:
            if not paper_db_path.exists():
                return {"paper_days_logged": 0, "paper_fills_logged": 0, "avg_paper_slippage_bps": 0.0}
            conn = sqlite3.connect(str(paper_db_path), timeout=5.0)
            conn.row_factory = sqlite3.Row
            close_temp = True
        try:
            day_row = conn.execute(
                "SELECT COUNT(DISTINCT trade_date) AS n_days FROM daily_nav WHERE mode = 'paper'"
            ).fetchone()
            slip_row = conn.execute(
                "SELECT COUNT(*) AS n_fills, AVG(slippage_bps) AS avg_slip FROM fills WHERE mode = 'paper'"
            ).fetchone()
            return {
                "paper_days_logged": int(day_row["n_days"]) if day_row else 0,
                "paper_fills_logged": int(slip_row["n_fills"]) if slip_row else 0,
                "avg_paper_slippage_bps": round(float(slip_row["avg_slip"]), 2)
                if slip_row and slip_row["avg_slip"] is not None
                else 0.0,
            }
        except sqlite3.OperationalError:
            return {"paper_days_logged": 0, "paper_fills_logged": 0, "avg_paper_slippage_bps": 0.0}
        finally:
            if close_temp:
                conn.close()
