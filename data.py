"""
data.py — All external data fetching.

Responsibilities
----------------
- NSE ticker list download with retry + disk cache
- Batch yfinance OHLCV download (parallel threads)
- India VIX fetch
- Nifty 50 macro guard
- Market breadth calculation (% of Nifty 500 above 200 DMA)
"""

import json
import time
import requests
import io
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from utils import get_logger

logger = get_logger(__name__)

# ── Disk cache helpers ─────────────────────────────────────────────────────────

_CACHE_DIR = Path(".cache")
_CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(key: str) -> Path:
    safe_key = key.replace("/", "_").replace(":", "_")
    return _CACHE_DIR / f"{safe_key}.json"


def _cache_get(key: str, ttl_hours: float) -> Optional[list]:
    path = _cache_path(key)
    if not path.exists():
        return None
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > timedelta(hours=ttl_hours):
        return None
    with open(path) as f:
        return json.load(f)


def _cache_set(key: str, data: list) -> None:
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


# ── NSE ticker fetching with retry + cache ─────────────────────────────────────

def get_tickers(url: str, max_retries: int = 3,
                backoff_base: int = 2,
                timeout: int = 15,
                cache_ttl_hours: float = 24) -> list[str]:
    """
    Fetch NSE constituent CSV and return a list of '<SYMBOL>.NS' strings.
    Results are cached on disk for `cache_ttl_hours` to reduce NSE requests.
    Retries with exponential backoff on network errors.
    """
    cached = _cache_get(url, cache_ttl_hours)
    if cached:
        logger.debug(f"Ticker list served from cache: {url}")
        return cached

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.nseindia.com",
    }

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            # NSE CSVs use 'Symbol' column
            if "Symbol" not in df.columns:
                raise ValueError(f"'Symbol' column missing. Columns: {list(df.columns)}")
            tickers = (df["Symbol"].str.strip() + ".NS").tolist()
            _cache_set(url, tickers)
            logger.info(f"Fetched {len(tickers)} tickers from NSE.")
            return tickers
        except Exception as e:
            wait = backoff_base ** attempt
            logger.warning(f"Ticker fetch attempt {attempt}/{max_retries} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)

    logger.error(f"All {max_retries} attempts to fetch tickers from {url} failed.")
    return []


# ── Batch OHLCV download ───────────────────────────────────────────────────────

def download_ohlcv(tickers: list[str], period: str = "1y",
                   fetch_delay: float = 0.2) -> dict[str, pd.DataFrame]:
    """
    Download OHLCV history for a list of tickers.
    Uses yfinance's threaded group_by download for speed, then falls back to
    individual downloads for any tickers that returned empty data.

    Returns a dict: { ticker_str: DataFrame }
    """
    if not tickers:
        return {}

    logger.info(f"Downloading {len(tickers)} tickers via batch download...")
    results: dict[str, pd.DataFrame] = {}

    try:
        raw = yf.download(
            tickers,
            period=period,
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=True,
        )
        # yf.download returns MultiIndex when >1 ticker
        if len(tickers) == 1:
            t = tickers[0]
            if not raw.empty:
                results[t] = raw.copy()
        else:
            for t in tickers:
                try:
                    df = raw[t].dropna(how="all")
                    if not df.empty:
                        results[t] = df
                except KeyError:
                    pass
    except Exception as e:
        logger.warning(f"Batch download failed ({e}). Falling back to individual downloads.")

    # Individual fallback for missing tickers
    missing = [t for t in tickers if t not in results]
    if missing:
        logger.info(f"Individual fallback for {len(missing)} tickers...")
        for t in missing:
            try:
                df = yf.Ticker(t).history(period=period)
                if not df.empty:
                    results[t] = df
                time.sleep(fetch_delay)
            except Exception as e:
                logger.debug(f"Could not fetch {t}: {e}")

    logger.info(f"Successfully loaded data for {len(results)}/{len(tickers)} tickers.")
    return results


# ── Weekly confirmation (multi-timeframe) ──────────────────────────────────────

def get_weekly_trend(ticker: str) -> Optional[str]:
    """
    Return 'bullish', 'bearish', or None if data is unavailable.
    Weekly bullish = weekly close > weekly 10-EMA.
    """
    try:
        df = yf.Ticker(ticker).history(period="1y", interval="1wk")
        if len(df) < 10:
            return None
        ema10w = df["Close"].ewm(span=10, adjust=False).mean()
        return "bullish" if df["Close"].iloc[-1] > ema10w.iloc[-1] else "bearish"
    except Exception as e:
        logger.debug(f"Weekly trend fetch failed for {ticker}: {e}")
        return None


# ── India VIX ──────────────────────────────────────────────────────────────────

def get_india_vix(vix_ticker: str = "^INDIAVIX") -> Optional[float]:
    """Return the latest India VIX close value, or None on failure."""
    try:
        df = yf.Ticker(vix_ticker).history(period="5d")
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Could not fetch India VIX: {e}")
        return None


# ── Nifty 50 macro guard ───────────────────────────────────────────────────────

def get_nifty_health(index_ticker: str = "^NSEI",
                     ema_period: int = 50) -> tuple[bool, float, float]:
    """
    Check if Nifty is above its EMA.

    Returns
    -------
    (is_healthy, current_close, ema_value)
    """
    try:
        df = yf.Ticker(index_ticker).history(period="3mo")
        if len(df) < ema_period:
            logger.warning("Insufficient Nifty history for EMA guard. Assuming healthy.")
            return True, 0.0, 0.0
        df["EMA"] = df["Close"].ewm(span=ema_period, adjust=False).mean()
        close = float(df["Close"].iloc[-1])
        ema = float(df["EMA"].iloc[-1])
        return close > ema, close, ema
    except Exception as e:
        logger.error(f"Nifty health check failed: {e}")
        return True, 0.0, 0.0  # Default to healthy if data unavailable


# ── Market breadth ─────────────────────────────────────────────────────────────

def get_market_breadth(breadth_url: str,
                       max_retries: int = 3,
                       backoff_base: int = 2) -> Optional[float]:
    """
    Calculate what percentage of Nifty 500 stocks are currently above their
    200-day SMA. Downloads Nifty 500 list then checks each stock.

    Returns a float between 0–100, or None on failure.
    """
    tickers = get_tickers(breadth_url, max_retries=max_retries,
                          backoff_base=backoff_base, cache_ttl_hours=24)
    if not tickers:
        logger.warning("Could not fetch breadth universe. Skipping breadth check.")
        return None

    above = 0
    checked = 0

    # Batch download to speed things up
    data = download_ohlcv(tickers, period="1y")

    for ticker, df in data.items():
        if len(df) < 200:
            continue
        sma200 = df["Close"].rolling(200).mean().iloc[-1]
        if df["Close"].iloc[-1] > sma200:
            above += 1
        checked += 1

    if checked == 0:
        return None

    pct = round((above / checked) * 100, 1)
    logger.info(f"Market breadth: {above}/{checked} stocks above 200 DMA = {pct}%")
    return pct


def classify_regime(breadth_pct: Optional[float],
                    nifty_healthy: bool,
                    bull_threshold: float = 60,
                    mixed_threshold: float = 40) -> str:
    """
    Derive the current market regime string used to filter wallets.

    Returns: 'bull' | 'mixed' | 'bear' | 'safety_off'
    """
    if not nifty_healthy:
        return "safety_off"
    if breadth_pct is None:
        return "bull"          # Conservative default — allow scanning
    if breadth_pct >= bull_threshold:
        return "bull"
    if breadth_pct >= mixed_threshold:
        return "mixed"
    return "bear"
