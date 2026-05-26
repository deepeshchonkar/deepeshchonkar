"""
main.py — Entry point for the Sniper Trading Bot.

Commands
--------
python main.py                          Daily scan (default)
python main.py --dry-run                Scan without saving or emailing
python main.py --track                  Run next-day outcome tracker
python main.py --weekly-summary         Send weekly performance email
python main.py --backtest               Backtest all wallets
python main.py --backtest --wallet X    Backtest a specific wallet
python main.py --backtest --start YYYY-MM-DD   Custom start date
"""

import argparse
import sys
from datetime import datetime

import database
import notifications
import scanner
import tracker
import backtester
from utils import get_logger, load_config, validate_env, today_str

# ── Bootstrap ──────────────────────────────────────────────────────────────────

cfg = load_config("config.yaml")
logger = get_logger(
    __name__,
    log_file=cfg["app"]["log_file"],
    level=cfg["app"]["log_level"],
)
database.init_db(cfg["app"]["db_file"])


# ── CLI argument parsing ───────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Sniper Trading Bot")
    p.add_argument("--dry-run", action="store_true",
                   help="Run scan but skip DB writes and email")
    p.add_argument("--track", action="store_true",
                   help="Run the next-day outcome tracker")
    p.add_argument("--weekly-summary", action="store_true",
                   help="Send the weekly performance summary email")
    p.add_argument("--backtest", action="store_true",
                   help="Run backtesting mode")
    p.add_argument("--wallet", type=str, default=None,
                   help="Wallet to backtest (default: all wallets)")
    p.add_argument("--start", type=str, default="2022-01-01",
                   help="Backtest start date YYYY-MM-DD (default: 2022-01-01)")
    return p.parse_args()


# ── Mode handlers ──────────────────────────────────────────────────────────────

def run_daily_scan(dry_run: bool) -> None:
    """Execute the daily scan and send the appropriate email."""
    if not dry_run:
        validate_env()

    date_str = today_str()
    logger.info(f"{'='*55}")
    logger.info(f"SNIPER BOT — DAILY SCAN — {date_str}")
    logger.info(f"{'='*55}")

    signals, date_str, regime = scanner.run_scan(cfg, dry_run=dry_run)

    # ── already ran guard ──
    if regime == "already_ran":
        logger.info("Scan already ran today. Nothing to do.")
        return

    # ── circuit breaker ──
    if regime == "circuit_breaker":
        html, plain = notifications.build_safety_email("circuit_breaker", date_str)
        notifications.send_email(
            f"🛑 Circuit Breaker Active: {date_str}", html, plain, cfg, dry_run
        )
        return

    # ── bear market ──
    if regime == "bear":
        html, plain = notifications.build_safety_email("bear", date_str)
        notifications.send_email(
            f"🐻 Bear Market Mode: {date_str}", html, plain, cfg, dry_run
        )
        return

    # ── safety mode (Nifty below EMA) ──
    if signals is None:
        html, plain = notifications.build_safety_email("safety_off", date_str)
        notifications.send_email(
            f"🛡️ Market Safety Mode: {date_str}", html, plain, cfg, dry_run
        )
        return

    # ── no signals found ──
    if not signals:
        html, plain = notifications.build_safety_email("no_signals", date_str)
        notifications.send_email(
            f"📉 No Signals Today: {date_str}", html, plain, cfg, dry_run
        )
        return

    # ── signals found — send report ──
    vix = signals[0].get("vix") if signals else None
    breadth_pct = None  # Optionally store in DB and retrieve here

    html, plain = notifications.build_signal_email(
        signals, date_str, regime, vix, breadth_pct
    )
    notifications.send_email(
        f"🎯 {len(signals)} Signal(s) Found: {date_str}",
        html, plain, cfg, dry_run,
    )
    logger.info(f"Daily scan done: {len(signals)} signal(s) emitted.")


def run_outcome_tracker(dry_run: bool) -> None:
    """Check outcomes of yesterday's signals and optionally email a report."""
    if not dry_run:
        validate_env()

    logger.info("Running outcome tracker...")
    outcomes = tracker.run_tracking(cfg, dry_run=dry_run)

    if not outcomes:
        logger.info("No outcomes to report.")
        return

    # Build a simple outcome summary email
    date_str = today_str()
    wins = [o for o in outcomes if o["result"] == "TARGET_HIT"]
    losses = [o for o in outcomes if o["result"] == "SL_HIT"]
    total_pnl = sum(o["pnl"] for o in outcomes)

    lines = [f"OUTCOME TRACKER — {date_str}", "=" * 45]
    for o in outcomes:
        icon = {"TARGET_HIT": "✅", "SL_HIT": "❌", "OPEN": "🔄"}.get(o["result"], "")
        lines.append(
            f"{icon} {o['ticker']} | {o['result']} | P&L: ₹{o['pnl']:+,.2f}"
        )
    lines.append("-" * 45)
    lines.append(f"Total P&L: ₹{total_pnl:+,.2f}  |  Wins: {len(wins)}  |  Losses: {len(losses)}")

    plain = "\n".join(lines)
    html = f"<html><body><pre style='font-family:monospace'>{plain}</pre></body></html>"
    notifications.send_email(
        f"📋 Trade Outcomes: {date_str}", html, plain, cfg, dry_run
    )


def run_weekly_summary(dry_run: bool) -> None:
    """Send the weekly performance summary email."""
    if not dry_run:
        validate_env()

    import pandas as pd
    df = database.get_weekly_summary(cfg["app"]["db_file"])
    date_str = today_str()
    html, plain = notifications.build_weekly_summary_email(df, date_str)
    notifications.send_email(
        f"📅 Weekly Summary: {date_str}", html, plain, cfg, dry_run
    )
    logger.info("Weekly summary email sent.")


def run_backtest_mode(wallet_key=None, start="2022-01-01") -> None:
    """Run backtesting on one or all wallets."""
    wallets = (
        [wallet_key] if wallet_key and wallet_key in cfg["wallets"]
        else list(cfg["wallets"].keys())
    )
    for w in wallets:
        df = backtester.run_backtest(cfg, w, start=start)
        if not df.empty:
            out_path = f"backtest_{w}_{start}.csv"
            df.to_csv(out_path, index=False)
            logger.info(f"Backtest results saved to {out_path}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()
    dry_run = args.dry_run or cfg["app"].get("dry_run", False)

    if args.backtest:
        run_backtest_mode(wallet_key=args.wallet, start=args.start)

    elif args.track:
        run_outcome_tracker(dry_run=dry_run)

    elif args.weekly_summary:
        run_weekly_summary(dry_run=dry_run)

    else:
        run_daily_scan(dry_run=dry_run)
