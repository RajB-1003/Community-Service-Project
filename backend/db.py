"""
db.py — Hardened SQLite storage layer (v7.0 — Financial Advisor)

Changes from v6.2:
  1. NEW TABLE: savings_goals (user_id, target_amount, duration_days, created_at)
  2. NEW QUERIES:
       get_last_7_days_expense  → (total, active_days) for expense prediction
       get_monthly_income       → current-month income total
       get_monthly_expense      → current-month expense total
       get_goal                 → fetch active savings goal row
       upsert_goal              → create or replace a savings goal
  3. BACKWARD COMPATIBLE: All v6.2 functions unchanged.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from datetime import date
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger("db")

# ─── DB Path ──────────────────────────────────────────────────────────────────

DB_DIR  = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "finance.db"


def _get_conn() -> sqlite3.Connection:
    """Return a WAL-mode connection with Row factory."""
    DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Schema ───────────────────────────────────────────────────────────────────

_TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT     NOT NULL,
    amount      INTEGER  NOT NULL DEFAULT 0,
    type        TEXT     NOT NULL,
    category    TEXT     NOT NULL DEFAULT 'Other',
    ts          DATETIME DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS user_state (
    user_id     TEXT     PRIMARY KEY,
    goal_amt    INTEGER  DEFAULT 0,
    has_debt    INTEGER  DEFAULT 0,
    updated     DATETIME DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS savings_goals (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT     NOT NULL UNIQUE,
    target_amount INTEGER  NOT NULL DEFAULT 0,
    duration_days INTEGER  NOT NULL DEFAULT 100,
    created_at    DATETIME DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_tx_user  ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_ts    ON transactions(ts);
CREATE INDEX IF NOT EXISTS idx_goal_uid ON savings_goals(user_id);
"""

_POST_MIGRATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(tx_date);
CREATE INDEX IF NOT EXISTS idx_tx_cat  ON transactions(user_id, category);
"""


# ─── Migration Utilities ──────────────────────────────────────────────────────

def _add_column(table: str, column: str, definition: str) -> None:
    """
    Add a column using isolation_level=None (autocommit).
    Silently ignores if the column already exists.
    """
    try:
        DB_DIR.mkdir(exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False,
                               isolation_level=None)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.close()
        log.debug("DB migration: added column %s.%s", table, column)
    except Exception:
        pass  # Column already exists — safe


def init_db() -> None:
    """
    Initialise DB schema. Idempotent — safe to call multiple times.

    Execution order (critical for backward compatibility):
      1. Create base tables + indexes  (includes savings_goals — new in v7.0)
      2. ALTER TABLE for v6.1 columns  (idempotent)
      3. Post-migration indexes
    """
    # Step 1: Base tables (CREATE IF NOT EXISTS — idempotent)
    with _get_conn() as conn:
        conn.executescript(_TABLES_SCHEMA)

    # Step 2: v6.1 column migrations (no-op if already applied)
    _add_column("transactions", "tx_date",   "DATE     DEFAULT (date('now','localtime'))")
    _add_column("user_state",   "last_seen", "DATETIME DEFAULT (datetime('now','localtime'))")

    # Step 3: Post-migration indexes
    with _get_conn() as conn:
        conn.executescript(_POST_MIGRATE_INDEXES)

    log.info("DB initialised at %s (v7.0)", DB_PATH)


# ─── CRUD — Transactions ──────────────────────────────────────────────────────

def insert_transaction(
    user_id:  str,
    amount:   int,
    tx_type:  str,
    category: str,
    tx_date:  Optional[date] = None,
) -> int:
    """
    Insert one transaction row and immediately commit.
    Returns: new row id (0 if skipped due to amount <= 0).
    """
    if amount <= 0:
        log.warning("DB | insert_transaction called with amount=%d — skipping", amount)
        return 0

    effective_date = tx_date.isoformat() if tx_date else date.today().isoformat()
    sql = """
        INSERT INTO transactions (user_id, amount, type, category, tx_date)
        VALUES (?, ?, ?, ?, ?)
    """
    conn = _get_conn()
    try:
        cur = conn.execute(sql, (user_id, amount, tx_type, category, effective_date))
        conn.commit()
        row_id = cur.lastrowid or 0
        log.info(
            "DB INSERT | user=%s type=%s cat=%s amt=%d date=%s → id=%d",
            user_id, tx_type, category, amount, effective_date, row_id,
        )
        return row_id
    except Exception as exc:
        conn.rollback()
        log.error("DB INSERT FAILED: %s", exc, exc_info=True)
        raise
    finally:
        conn.close()


def get_recent_transactions(user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Return the most recent transactions for a user, newest first."""
    sql = """
        SELECT id, user_id, amount, type, category,
               COALESCE(tx_date, date(ts)) AS tx_date,
               ts
        FROM   transactions
        WHERE  user_id = ?
        ORDER  BY ts DESC, id DESC
        LIMIT  ?
    """
    with _get_conn() as conn:
        rows = conn.execute(sql, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ─── QUERIES — Monthly Aggregates ─────────────────────────────────────────────

def get_monthly_summary(user_id: str) -> Dict[str, int]:
    """
    Sum income / expense / debt for the CURRENT calendar month.
    Uses COALESCE(tx_date, date(ts)) to include legacy NULL tx_date rows.
    """
    sql = """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income'         THEN amount ELSE 0 END), 0)
                AS income_total,
            COALESCE(SUM(CASE WHEN type = 'expense'        THEN amount ELSE 0 END), 0)
                AS expense_total,
            COALESCE(SUM(CASE WHEN type = 'debt_repayment' THEN amount ELSE 0 END), 0)
                AS debt_total,
            COUNT(*) AS transaction_count
        FROM transactions
        WHERE user_id = ?
          AND strftime('%Y-%m', COALESCE(tx_date, date(ts))) = strftime('%Y-%m', 'now')
    """
    with _get_conn() as conn:
        row = conn.execute(sql, (user_id,)).fetchone()

    result = dict(row) if row else {
        "income_total": 0, "expense_total": 0,
        "debt_total": 0, "transaction_count": 0,
    }
    log.debug(
        "SUMMARY | user=%s income=%s expense=%s debt=%s count=%s",
        user_id,
        result.get("income_total"), result.get("expense_total"),
        result.get("debt_total"),   result.get("transaction_count"),
    )
    return result


def get_monthly_income(user_id: str) -> int:
    """Current-month income total for a user. Convenience wrapper."""
    return int(get_monthly_summary(user_id).get("income_total", 0))


def get_monthly_expense(user_id: str) -> int:
    """Current-month expense total for a user. Convenience wrapper."""
    return int(get_monthly_summary(user_id).get("expense_total", 0))


# ─── QUERIES — 7-Day Expense Window ──────────────────────────────────────────

def get_last_7_days_expense(user_id: str) -> Tuple[int, int]:
    """
    Return (total_expense, active_days) for the last 7 calendar days.

    active_days = number of distinct days that have at least one expense row.
    Used by predict_expense() to determine data sufficiency.

    Edge case handling:
      - If user has no data → (0, 0)
      - COALESCE(tx_date, date(ts)) for legacy NULL tx_date support
    """
    sql = """
        SELECT
            COALESCE(SUM(amount), 0)                     AS total_expense,
            COUNT(DISTINCT COALESCE(tx_date, date(ts)))  AS active_days
        FROM transactions
        WHERE user_id = ?
          AND type    = 'expense'
          AND COALESCE(tx_date, date(ts)) >= date('now', '-6 days')
    """
    with _get_conn() as conn:
        row = conn.execute(sql, (user_id,)).fetchone()

    total       = int(row["total_expense"]) if row else 0
    active_days = int(row["active_days"])   if row else 0
    log.debug("7DAY EXPENSE | user=%s total=%d active_days=%d", user_id, total, active_days)
    return total, active_days


# ─── QUERIES — Category Average ───────────────────────────────────────────────

def get_category_avg(user_id: str, category: str) -> float:
    """90-day rolling average amount for a (user, category) pair."""
    sql = """
        SELECT AVG(amount) AS avg_amt
        FROM   transactions
        WHERE  user_id  = ?
          AND  category = ?
          AND  COALESCE(tx_date, date(ts)) >= date('now', '-90 days')
    """
    with _get_conn() as conn:
        row = conn.execute(sql, (user_id, category)).fetchone()
    val = row["avg_amt"] if row and row["avg_amt"] is not None else None
    return float(val) if val else 0.0


# ─── CRUD — Savings Goals ─────────────────────────────────────────────────────

def get_goal(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the active savings goal for a user.
    Returns dict with keys: user_id, target_amount, duration_days, created_at
    Returns None if no goal has been set.
    """
    sql = """
        SELECT user_id, target_amount, duration_days, created_at
        FROM   savings_goals
        WHERE  user_id = ?
        LIMIT  1
    """
    with _get_conn() as conn:
        row = conn.execute(sql, (user_id,)).fetchone()
    return dict(row) if row else None


def upsert_goal(user_id: str, target_amount: int, duration_days: int) -> None:
    """
    Create or replace a savings goal for a user.
    Uses INSERT OR REPLACE — only one active goal per user (UNIQUE on user_id).

    Validation:
      - target_amount must be > 0
      - duration_days must be >= 1
    """
    if target_amount <= 0:
        raise ValueError("target_amount must be positive")
    if duration_days < 1:
        raise ValueError("duration_days must be at least 1")

    sql = """
        INSERT INTO savings_goals (user_id, target_amount, duration_days, created_at)
        VALUES (?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            target_amount = excluded.target_amount,
            duration_days = excluded.duration_days,
            created_at    = excluded.created_at
    """
    with _get_conn() as conn:
        conn.execute(sql, (user_id, target_amount, duration_days))
    log.info("GOAL UPSERT | user=%s target=%d days=%d", user_id, target_amount, duration_days)


# ─── Utility ──────────────────────────────────────────────────────────────────

def touch_user(user_id: str) -> None:
    """Upsert user_state.last_seen for activity tracking."""
    sql = """
        INSERT INTO user_state (user_id, last_seen)
        VALUES (?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            last_seen = datetime('now','localtime')
    """
    with _get_conn() as conn:
        conn.execute(sql, (user_id,))


def is_db_connected() -> bool:
    """Health probe."""
    try:
        with _get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
