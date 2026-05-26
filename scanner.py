"""
scanner.py — Main scan orchestration.

Flow
----
1.  Load config + validate env
2.  Check monthly circuit breaker
3.  Nifty 50 macro guard
4.  Fetch India VIX → derive dynamic risk %
5.  Calculate market breadth → derive regime
6.  For each wallet active in the current regime:
      a. Fetch ticker list
      b. Download OHLCV in batch
      c. Add indicators
      d. Evaluate strategy
      e. Apply risk filters (R:R, qty, portfolio cap, sector cap, cooldown)
7.  Save signals to DB
8.  Return structured signal list
"""

import time
from datetime import datetime
from typing import Optional

import pandas as pd

import data
import database
import indicators
import strategies
import risk
from utils import get_logger, today_str, today_iso

logger = get_logger(__name__)


def run_scan(cfg: dict, dry_run: bool = False) -> tuple[Optional[list[dict]], str, str]:
    """
    Execute the full scan.

    Returns
    -------
    (signals, date_str, regime)

    signals = None       → safety mode (Nifty below EMA)
    signals = []         → market healthy but no setups found
    signals = [...]      → trade signals to act on
    """
    date_str = today_str()
    db_path = cfg["app"]["db_file"]
    total_cap = sum(w["cap"] for w in cfg["wallets"].values())

    # ── 0. Duplicate run guard ─────────────────────────────────────────────────
    if not dry_run and database.already_ran_today(db_path):
        logger.warning("Scan already completed today. Exiting to avoid duplicates.")
        return [], date_str, "already_ran"

    # ── 1. Monthly drawdown circuit breaker ────────────────────────────────────
    monthly_pnl = database.get_monthly_pnl(db_path)
    if risk.is_circuit_breaker_tripped(monthly_pnl, total_cap, cfg):
        return [], date_str, "circuit_breaker"

    # ── 2. Nifty 50 macro guard ────────────────────────────────────────────────
    logger.info("Checking Nifty 50 health...")
    nifty_healthy, nifty_close, nifty_ema = data.get_nifty_health(
        cfg["market"]["index_ticker"],
        cfg["market"]["ema_guard_period"],
    )
    if not nifty_healthy:
        logger.warning(
            f"🛡️ Nifty ({nifty_close:.0f}) is below its "
            f"{cfg['market']['ema_guard_period']}-EMA ({nifty_ema:.0f}). "
            "Safety mode active."
        )
        return None, date_str, "safety_off"

    # ── 3. India VIX ───────────────────────────────────────────────────────────
    logger.info("Fetching India VIX...")
    vix = data.get_india_vix(cfg["market"]["vix_ticker"])
    risk_pct = risk.get_risk_pct(vix, cfg)
    logger.info(f"VIX={f'{vix:.1f}' if vix else 'N/A'}  →  Risk/trade={risk_pct*100:.2f}%")
    # ── 4. Market breadth ──────────────────────────────────────────────────────
    logger.info("Calculating market breadth...")
    breadth_pct = data.get_market_breadth(
        cfg["market"]["breadth_universe_url"],
        max_retries=cfg["network"]["max_retries"],
        backoff_base=cfg["network"]["retry_backoff_base"],
    )
    regime = data.classify_regime(
        breadth_pct,
        nifty_healthy,
        cfg["market"]["breadth_bull_threshold"],
        cfg["market"]["breadth_mixed_threshold"],
    )
    logger.info(f"Market breadth: {breadth_pct or 'N/A'}%  →  Regime: {regime.upper()}")

    if regime == "bear":
        logger.warning("🐻 Bear market regime detected. Pausing all new entries.")
        return [], date_str, "bear"

    # ── 5. Record scan start ───────────────────────────────────────────────────
    if not dry_run:
        database.record_scan_start(db_path, regime, vix or 0.0, breadth_pct or 0.0)

    # ── 6. Strategy scan loop ──────────────────────────────────────────────────
    signals: list[dict] = []
    recent_tickers = database.get_recent_tickers(
        db_path, cfg["app"]["alert_cooldown_days"]
    )
    current_exposure = database.get_open_positions_value(db_path)

    sector_tracker = risk.SectorTracker(cfg["risk"]["max_sector_signals"])

    for w_name, w_conf in cfg["wallets"].items():

        # Skip wallets not active in the current regime
        if regime not in w_conf.get("active_in", ["bull"]):
            logger.info(f"Skipping wallet {w_name} (not active in '{regime}' regime)")
            continue

        logger.info(f"──── Scanning {w_name} ({w_conf['logic']}) ────")
        tickers = data.get_tickers(
            w_conf["url"],
            max_retries=cfg["network"]["max_retries"],
            backoff_base=cfg["network"]["retry_backoff_base"],
            timeout=cfg["network"]["request_timeout"],
        )

        if not tickers:
            logger.warning(f"No tickers for {w_name}. Skipping.")
            continue

        ohlcv = data.download_ohlcv(
            tickers,
            fetch_delay=cfg["network"]["ticker_fetch_delay"],
        )

        for ticker, df_raw in ohlcv.items():
            ticker_sym = ticker.replace(".NS", "")

            try:
                # Minimum data requirement
                if len(df_raw) < cfg["app"]["min_data_rows"]:
                    logger.debug(f"{ticker_sym}: insufficient data ({len(df_raw)} rows)")
                    continue

                # Cooldown check
                if ticker_sym in recent_tickers:
                    logger.debug(f"{ticker_sym}: in cooldown window, skipping")
                    continue

                # Add all technical indicators
                df = indicators.add_all_indicators(df_raw)

                # Drop rows with NaN indicators (startup period)
                df = df.dropna(subset=["SMA_200", "ATR", "RSI", "ADX"])
                if len(df) < 2:
                    continue

                # Weekly trend multi-timeframe confirmation
                weekly_trend = data.get_weekly_trend(ticker)
                if weekly_trend == "bearish":
                    logger.debug(f"{ticker_sym}: weekly trend bearish, skipping")
                    continue

                # Evaluate strategy
                signal = strategies.evaluate(w_conf["logic"], df, cfg)
                if signal is None:
                    continue

                entry = signal["entry"]
                sl = signal["sl"]

                # R:R filter
                passes, rr = risk.passes_rr_filter(
                    entry, sl, signal["target_val"], cfg
                )
                if not passes:
                    continue
                signal["rr_ratio"] = rr

                # Position sizing
                qty, pos_val = risk.calculate_position(
                    w_conf["cap"], entry, sl, risk_pct, cfg
                )
                if qty == 0:
                    logger.debug(f"{ticker_sym}: qty=0 after sizing, skipping")
                    continue

                # Portfolio exposure cap
                if not risk.check_portfolio_cap(pos_val, current_exposure, total_cap, cfg):
                    continue

                # Sector concentration (using wallet as sector proxy)
                if not sector_tracker.can_add(w_name):
                    logger.info(f"Sector cap hit for {w_name}. Skipping {ticker_sym}.")
                    continue

                # ── Signal accepted ────────────────────────────────────────────
                current_exposure += pos_val
                sector_tracker.add(w_name)
                recent_tickers.add(ticker_sym)  # Prevent same ticker in another wallet today

                target_display = (
                    f"₹{signal['target_val']:.2f}"
                    if signal["target_val"]
                    else "Trailing EMA-20"
                )

                signals.append({
                    "date": today_iso(),
                    "ticker": ticker_sym,
                    "wallet": w_name,
                    "logic": w_conf["logic"],
                    "buy": entry,
                    "sl": sl,
                    "qty": qty,
                    "target_type": signal["target_type"],
                    "target_val": signal["target_val"],
                    "target_display": target_display,
                    "atr": round(signal["atr"], 2),
                    "rsi": signal["rsi"],
                    "confidence": signal["confidence"],
                    "rr_ratio": signal.get("rr_ratio", 0.0),
                    "pos_value": pos_val,
                    "vix": vix,
                    "regime": regime,
                    "risk_pct": risk_pct,
                })

                logger.info(
                    f"✅ SIGNAL | {ticker_sym} | {w_conf['logic']} | "
                    f"Buy ₹{entry} | SL ₹{sl} | Qty {qty} | "
                    f"Conf {signal['confidence']}/10 | R:R {rr:.1f}"
                )

            except Exception as e:
                logger.warning(f"Error processing {ticker_sym}: {e}", exc_info=False)
                continue

        time.sleep(0.5)  # Brief pause between wallet scans

    # ── 7. Persist to DB ───────────────────────────────────────────────────────
    if signals and not dry_run:
        inserted = database.insert_signals(db_path, signals)
        database.update_scan_count(db_path, inserted)
        logger.info(f"Saved {inserted} new signals to database.")

    logger.info(
        f"Scan complete: {len(signals)} signal(s) | "
        f"Regime: {regime} | VIX: {vix or 'N/A'}"
    )
    return signals, date_str, regime
