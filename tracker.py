"""
tracker.py — Next-day trade outcome tracker.

Run this after market hours to review yesterday's signals:
  - Did price hit the target?
  - Did price hit the stop-loss?
  - Neither (still open)?

Writes results to the outcomes table in SQLite.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime

import database
from utils import get_logger, today_iso

logger = get_logger(__name__)


def _classify_outcome(signal: pd.Series, today_bar: pd.Series) -> tuple[str, float]:
    """
    Given a signal row and today's OHLCV bar, determine if the trade
    hit target, hit stop-loss, or is still open.

    Simplified intraday order assumption:
    - If low <= SL  → stop-loss hit   (assume worst case fills at SL)
    - If high >= target (fixed) → target hit
    - Otherwise → still open (mark-to-market at close)
    """
    sl = signal["sl"]
    buy = signal["buy"]
    qty = signal["qty"]
    target_type = signal["target_type"]
    target_val = signal["target_val"]

    low = today_bar["Low"]
    high = today_bar["High"]
    close = today_bar["Close"]

    # Stop-loss check (priority — conservative)
    if low <= sl:
        pnl = round((sl - buy) * qty, 2)
        return "SL_HIT", pnl

    # Fixed-target check
    if target_type == "FIXED" and target_val and high >= target_val:
        pnl = round((target_val - buy) * qty, 2)
        return "TARGET_HIT", pnl

    # Trailing target (EMA-20) — mark open, P&L at close
    pnl = round((close - buy) * qty, 2)
    return "OPEN", pnl


def run_tracking(cfg: dict, dry_run: bool = False) -> list[dict]:
    """
    Fetch yesterday's unfollowed signals and check their outcome
    against today's market data.

    Returns a list of outcome dicts for reporting.
    """
    db_path = cfg["app"]["db_file"]
    pending = database.get_signals_for_tracking(db_path)

    if pending.empty:
        logger.info("No pending signals to track today.")
        return []

    logger.info(f"Tracking {len(pending)} signal(s) from yesterday...")
    outcomes = []

    for _, signal in pending.iterrows():
        ticker = signal["ticker"] + ".NS"
        try:
            df = yf.Ticker(ticker).history(period="5d")
            if df.empty or len(df) < 1:
                logger.warning(f"No price data for {ticker}. Skipping.")
                continue

            today_bar = df.iloc[-1]
            result, pnl = _classify_outcome(signal, today_bar)

            outcome = {
                "signal_id": int(signal["id"]),
                "ticker": signal["ticker"],
                "result": result,
                "pnl": pnl,
                "buy": signal["buy"],
                "sl": signal["sl"],
                "target_val": signal.get("target_val"),
                "close": round(float(today_bar["Close"]), 2),
            }
            outcomes.append(outcome)

            logger.info(
                f"📋 {signal['ticker']} | {result} | "
                f"P&L: ₹{pnl:+,.2f}"
            )

            if not dry_run:
                database.insert_outcome(
                    db_path,
                    signal_id=int(signal["id"]),
                    review_date=today_iso(),
                    open_p=float(today_bar["Open"]),
                    high_p=float(today_bar["High"]),
                    low_p=float(today_bar["Low"]),
                    close_p=float(today_bar["Close"]),
                    result=result,
                    pnl=pnl,
                )

        except Exception as e:
            logger.warning(f"Error tracking {ticker}: {e}")
            continue

    total_pnl = sum(o["pnl"] for o in outcomes)
    wins = sum(1 for o in outcomes if o["result"] == "TARGET_HIT")
    losses = sum(1 for o in outcomes if o["result"] == "SL_HIT")
    logger.info(
        f"Tracking done | {wins} wins, {losses} losses | "
        f"Total P&L: ₹{total_pnl:+,.2f}"
    )

    return outcomes
