"""
database.py — SQLite persistence layer.

Tables
------
signals     : Every trade signal emitted by the scanner.
outcomes    : Next-day outcome (hit target / hit SL / open) per signal.
scan_runs   : One row per scan execution (guards against duplicate runs).
"""

import sqlite3
import contextlib
from datetime import date, timedelta
from typing import Optional
import pandas as pd

from utils import get_logger

logger = get_logger(__name__)


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    wallet      TEXT    NOT NULL,
    logic       TEXT    NOT NULL,
    buy         REAL    NOT NULL,
    sl          REAL    NOT NULL,
    qty         INTEGER NOT NULL,
    target_type TEXT    NOT NULL,
    target_val  REAL,           -- NULL for trailing targets
    atr         REAL,
    rsi         REAL,
    confidence  INTEGER,
    rr_ratio    REAL,
    UNIQUE (date, ticker, wallet)
);

CREATE TABLE IF NOT EXISTS outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER NOT NULL REFERENCES signals(id),
    review_date TEXT    NOT NULL,
    open_price  REAL,
    high_price  REAL,
    low_price   REAL,
    close_price REAL,
    result      TEXT,           -- 'TARGET_HIT' | 'SL_HIT' | 'OPEN'
    pnl         REAL,
    UNIQUE (signal_id)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT    NOT NULL UNIQUE,
    regime      TEXT,           -- 'bull' | 'mixed' | 'bear' | 'safety_off'
    vix         REAL,
    breadth_pct REAL,
    signal_count INTEGER DEFAULT 0,
    completed_at TEXT
);
"""


# ── Connection context manager ─────────────────────────────────────────────────

@contextlib.contextmanager
def get_conn(db_path: str):
    """Yield a SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Initialisation ─────────────────────────────────────────────────────────────

def init_db(db_path: str) -> None:
    """Create tables if they don't already exist."""
    with get_conn(db_path) as conn:
        conn.executescript(_SCHEMA)
    logger.info(f"Database initialised at {db_path}")


# ── scan_runs helpers ──────────────────────────────────────────────────────────

def already_ran_today(db_path: str) -> bool:
    """Return True if a scan run exists for today's date."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM scan_runs WHERE run_date = date('now')"
        ).fetchone()
    return row is not None


def record_scan_start(db_path: str, regime: str, vix: float,
                      breadth_pct: float) -> int:
    """Insert a scan_runs row and return its id."""
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO scan_runs
               (run_date, regime, vix, breadth_pct, completed_at)
               VALUES (date('now'), ?, ?, ?, datetime('now'))""",
            (regime, vix, breadth_pct),
        )
        return cur.lastrowid or 0


def update_scan_count(db_path: str, count: int) -> None:
    """Update today's scan_runs row with the final signal count."""
    with get_conn(db_path) as conn:
        conn.execute(
            """UPDATE scan_runs SET signal_count = ?, completed_at = datetime('now')
               WHERE run_date = date('now')""",
            (count,),
        )


# ── signals helpers ────────────────────────────────────────────────────────────

def insert_signals(db_path: str, signals: list[dict]) -> int:
    """
    Bulk-insert signals. Silently ignores duplicates (same date+ticker+wallet).
    Returns the number of rows actually inserted.
    """
    if not signals:
        return 0

    sql = """
        INSERT OR IGNORE INTO signals
            (date, ticker, wallet, logic, buy, sl, qty, target_type,
             target_val, atr, rsi, confidence, rr_ratio)
        VALUES
            (:date, :ticker, :wallet, :logic, :buy, :sl, :qty, :target_type,
             :target_val, :atr, :rsi, :confidence, :rr_ratio)
    """
    with get_conn(db_path) as conn:
        cur = conn.executemany(sql, signals)
        return cur.rowcount


def get_recent_tickers(db_path: str, cooldown_days: int) -> set[str]:
    """
    Return ticker symbols that already received a signal within the cooldown
    window. Used to suppress duplicate alerts.
    """
    cutoff = (date.today() - timedelta(days=cooldown_days)).isoformat()
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM signals WHERE date >= ?", (cutoff,)
        ).fetchall()
    return {r["ticker"] for r in rows}


def get_signals_for_tracking(db_path: str) -> pd.DataFrame:
    """
    Return signals from yesterday that don't yet have an outcome row.
    Used by the outcome tracker.
    """
    with get_conn(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT s.*
            FROM   signals s
            LEFT JOIN outcomes o ON o.signal_id = s.id
            WHERE  s.date = date('now', '-1 day')
              AND  o.id IS NULL
            """,
            conn,
        )
    return df


# ── outcomes helpers ───────────────────────────────────────────────────────────

def insert_outcome(db_path: str, signal_id: int, review_date: str,
                   open_p: float, high_p: float, low_p: float,
                   close_p: float, result: str, pnl: float) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO outcomes
               (signal_id, review_date, open_price, high_price, low_price,
                close_price, result, pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (signal_id, review_date, open_p, high_p, low_p,
             close_p, result, pnl),
        )


# ── reporting helpers ──────────────────────────────────────────────────────────

def get_monthly_pnl(db_path: str) -> float:
    """Sum of realised PnL for the current calendar month."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(o.pnl), 0) AS total
               FROM   outcomes o
               JOIN   signals  s ON s.id = o.signal_id
               WHERE  strftime('%Y-%m', s.date) = strftime('%Y-%m', 'now')
                 AND  o.result != 'OPEN'""",
        ).fetchone()
    return float(row["total"])


def get_weekly_summary(db_path: str) -> pd.DataFrame:
    """Return signals + outcomes for the past 7 days."""
    with get_conn(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT s.date, s.ticker, s.wallet, s.logic,
                   s.buy, s.sl, s.qty, s.confidence, s.rr_ratio,
                   o.result, o.pnl
            FROM   signals s
            LEFT JOIN outcomes o ON o.signal_id = s.id
            WHERE  s.date >= date('now', '-7 days')
            ORDER  BY s.date DESC, s.ticker
            """,
            conn,
        )
    return df


def get_open_positions_value(db_path: str) -> float:
    """
    Sum of (buy_price * qty) for signals that don't yet have a closed outcome.
    Used for portfolio exposure cap check.
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(s.buy * s.qty), 0) AS total
            FROM   signals s
            LEFT JOIN outcomes o ON o.signal_id = s.id
            WHERE  o.id IS NULL OR o.result = 'OPEN'
            """,
        ).fetchone()
    return float(row["total"])
