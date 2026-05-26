"""
backtester.py — Vectorized backtesting module.

Runs a strategy's signal logic over historical data and reports:
  - Win rate, profit factor, max drawdown, avg R:R
  - Per-trade log
  - Equity curve

Usage:
  python main.py --backtest --wallet NIFTY_PULLBACK --start 2022-01-01
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Optional

import indicators
import strategies
from data import get_tickers
from utils import get_logger

logger = get_logger(__name__)


# ── Core backtester ────────────────────────────────────────────────────────────

def backtest_ticker(
    ticker: str,
    logic: str,
    cfg: dict,
    start: str = "2022-01-01",
    risk_pct: float = 0.01,
    wallet_cap: float = 100_000,
) -> list[dict]:
    """
    Replay the strategy signal logic bar-by-bar on historical data.
    When a signal fires, simulate the trade outcome using subsequent bars.

    Returns a list of trade dicts.
    """
    try:
        df_full = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    except Exception as e:
        logger.warning(f"Backtest: could not fetch {ticker}: {e}")
        return []

    if len(df_full) < 250:
        return []

    df_full = indicators.add_all_indicators(df_full).dropna()
    trades = []
    in_trade = False
    entry_price = sl = target_val = target_type = entry_idx = qty = 0

    for i in range(1, len(df_full)):
        df_window = df_full.iloc[: i + 1]

        # ── Manage open trade ──────────────────────────────────────────────────
        if in_trade:
            bar = df_full.iloc[i]
            result = "OPEN"
            pnl = 0.0

            if bar["Low"] <= sl:
                result = "SL_HIT"
                pnl = (sl - entry_price) * qty
            elif target_type == "FIXED" and bar["High"] >= target_val:
                result = "TARGET_HIT"
                pnl = (target_val - entry_price) * qty
            elif target_type == "TRAILING_EMA20":
                ema20 = float(bar["EMA_20"])
                if bar["Close"] < ema20:
                    result = "TARGET_HIT"   # Trailing stop hit EMA
                    pnl = (bar["Close"] - entry_price) * qty

            if result != "OPEN":
                trades.append({
                    "ticker": ticker.replace(".NS", ""),
                    "entry_date": df_full.index[entry_idx],
                    "exit_date": df_full.index[i],
                    "entry": entry_price,
                    "sl": sl,
                    "target": target_val,
                    "qty": qty,
                    "result": result,
                    "pnl": round(pnl, 2),
                    "bars_held": i - entry_idx,
                })
                in_trade = False
            continue

        # ── Check for new signal ───────────────────────────────────────────────
        signal = strategies.evaluate(logic, df_window, cfg)
        if signal is None:
            continue

        risk_per_share = signal["entry"] - signal["sl"]
        if risk_per_share <= 0:
            continue

        _qty = int((wallet_cap * risk_pct) // risk_per_share)
        if _qty < 1:
            continue

        in_trade = True
        entry_price = signal["entry"]
        sl = signal["sl"]
        target_val = signal["target_val"] or (entry_price + 3 * signal["atr"])
        target_type = signal["target_type"]
        entry_idx = i
        qty = _qty

    return trades


def run_backtest(cfg: dict, wallet_key: str,
                 start: str = "2022-01-01") -> pd.DataFrame:
    """
    Run backtest across all tickers in a wallet universe.
    Returns a DataFrame of all trades with a summary printed to log.
    """
    w = cfg["wallets"][wallet_key]
    logger.info(f"Starting backtest: {wallet_key} | Logic: {w['logic']} | From: {start}")

    tickers = get_tickers(
        w["url"],
        max_retries=cfg["network"]["max_retries"],
        backoff_base=cfg["network"]["retry_backoff_base"],
    )
    all_trades: list[dict] = []

    for t in tickers:
        trades = backtest_ticker(
            t, w["logic"], cfg,
            start=start,
            risk_pct=cfg["risk"]["base_risk_pct"],
            wallet_cap=w["cap"],
        )
        all_trades.extend(trades)
        if trades:
            logger.info(f"  {t.replace('.NS','')}: {len(trades)} trades")

    if not all_trades:
        logger.warning("No trades generated in backtest.")
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    _print_backtest_summary(df, wallet_key)
    return df


def _print_backtest_summary(df: pd.DataFrame, label: str) -> None:
    """Print key backtest statistics to the log."""
    closed = df[df["result"] != "OPEN"]
    if closed.empty:
        logger.info("No closed trades in backtest.")
        return

    wins = (closed["result"] == "TARGET_HIT")
    losses = (closed["result"] == "SL_HIT")

    win_rate = wins.mean() * 100
    total_pnl = closed["pnl"].sum()
    avg_win = closed.loc[wins, "pnl"].mean() if wins.any() else 0
    avg_loss = closed.loc[losses, "pnl"].mean() if losses.any() else 0
    profit_factor = (
        closed.loc[wins, "pnl"].sum() / abs(closed.loc[losses, "pnl"].sum())
        if losses.any() and closed.loc[losses, "pnl"].sum() != 0
        else float("inf")
    )

    # Max drawdown via equity curve
    equity = closed["pnl"].cumsum()
    rolling_max = equity.cummax()
    drawdown = equity - rolling_max
    max_dd = drawdown.min()

    logger.info(
        f"\n{'='*55}\n"
        f"BACKTEST RESULTS: {label}\n"
        f"{'='*55}\n"
        f"  Trades       : {len(closed)}\n"
        f"  Win Rate     : {win_rate:.1f}%\n"
        f"  Total P&L    : ₹{total_pnl:,.0f}\n"
        f"  Avg Win      : ₹{avg_win:,.0f}\n"
        f"  Avg Loss     : ₹{avg_loss:,.0f}\n"
        f"  Profit Factor: {profit_factor:.2f}\n"
        f"  Max Drawdown : ₹{max_dd:,.0f}\n"
        f"  Avg Bars Held: {closed['bars_held'].mean():.1f}\n"
        f"{'='*55}"
    )
