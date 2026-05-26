"""
strategies.py — Strategy engine.

Each strategy function receives the fully-enriched DataFrame and config,
and returns a dict (signal) or None.

Strategies
----------
PULLBACK        : Strong uptrend, price pulls back to EMA-50, bullish candle
BREAKOUT        : Price breaks above 20-day high with volume surge
MEAN_REVERSION  : Oversold (RSI < 35) + lower Bollinger Band touch
52W_HIGH        : First close above 52-week high with volume
"""

from typing import Optional
import pandas as pd

from utils import get_logger

logger = get_logger(__name__)


# ── Confidence scorer ──────────────────────────────────────────────────────────

def _base_confidence(curr: pd.Series, df: pd.DataFrame) -> int:
    """
    Return a 0–10 base confidence score shared across all strategies.
    Each condition adds 1 point.
    """
    score = 0

    # 1. Price above 200 SMA (strong long-term trend)
    if curr["Close"] > curr["SMA_200"]:
        score += 1

    # 2. EMA 20 > EMA 50 (medium-term momentum)
    if curr["EMA_20"] > curr["EMA_50"]:
        score += 1

    # 3. Volume surge vs 20-day average
    if curr["Volume"] > curr["Vol_SMA"]:
        score += 1

    # 4. RSI in healthy range (not overbought)
    if 30 < curr.get("RSI", 0) < 75:
        score += 1

    # 5. ADX > 20 (some trend strength)
    if curr.get("ADX", 0) > 20:
        score += 1

    # 6. EMA stack alignment: close > EMA20 > EMA50 > SMA200
    if (curr["Close"] > curr["EMA_20"] > curr["EMA_50"] > curr["SMA_200"]):
        score += 2  # Double point — strong alignment

    # 7. 3 consecutive higher lows (short-term momentum)
    if len(df) >= 4:
        lows = df["Low"].iloc[-4:-1]
        if all(lows.iloc[i] < lows.iloc[i + 1] for i in range(len(lows) - 1)):
            score += 1

    return score


# ── PULLBACK ──────────────────────────────────────────────────────────────────

def strategy_pullback(df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """
    Entry: Stock in uptrend (above SMA200, EMA20>EMA50), pulls back so
    the daily low touches or breaches EMA-50, and then closes green
    (close > open). RSI must be in 40–65 to confirm healthy pullback.
    """
    curr = df.iloc[-1]
    f = cfg["filters"]

    uptrend = (curr["Close"] > curr["SMA_200"]) and (curr["EMA_20"] > curr["EMA_50"])
    vol_surge = curr["Volume"] > curr["Vol_SMA"]
    pullback_touch = curr["Low"] <= curr["EMA_50"]
    bullish_candle = curr["Close"] > curr["Open"]
    rsi_ok = f["rsi_pullback_min"] <= curr.get("RSI", 0) <= f["rsi_pullback_max"]

    if not (uptrend and vol_surge and pullback_touch and bullish_candle and rsi_ok):
        return None

    confidence = _base_confidence(curr, df) + 2  # +2 for pullback specifics
    if confidence < f["min_confidence_score"]:
        return None

    ent = round(float(curr["Close"]), 2)
    atr = float(curr["ATR"])
    sl = round(ent - 1.0 * atr, 2)
    target_val = round(ent + 3.0 * atr, 2)

    return {
        "logic": "PULLBACK",
        "entry": ent,
        "sl": sl,
        "atr": atr,
        "rsi": round(float(curr.get("RSI", 0)), 1),
        "confidence": min(confidence, 10),
        "target_type": "FIXED",
        "target_val": target_val,
        "sl_mult": 1.0,
    }


# ── BREAKOUT ──────────────────────────────────────────────────────────────────

def strategy_breakout(df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """
    Entry: Stock in uptrend, closes above the 20-day previous high with
    above-average volume. RSI must be above 55 (momentum confirmation).
    """
    curr = df.iloc[-1]
    f = cfg["filters"]

    uptrend = (curr["Close"] > curr["SMA_200"]) and (curr["EMA_20"] > curr["EMA_50"])
    vol_surge = curr["Volume"] > curr["Vol_SMA"]
    breakout = curr["Close"] > curr["Prev_High_20"]
    rsi_ok = curr.get("RSI", 0) >= f["rsi_breakout_min"]

    if not (uptrend and vol_surge and breakout and rsi_ok):
        return None

    confidence = _base_confidence(curr, df) + 1  # +1 for breakout confirmation
    if confidence < f["min_confidence_score"]:
        return None

    ent = round(float(curr["Close"]), 2)
    atr = float(curr["ATR"])
    sl = round(ent - 1.5 * atr, 2)

    return {
        "logic": "BREAKOUT",
        "entry": ent,
        "sl": sl,
        "atr": atr,
        "rsi": round(float(curr.get("RSI", 0)), 1),
        "confidence": min(confidence, 10),
        "target_type": "TRAILING_EMA20",
        "target_val": None,
        "sl_mult": 1.5,
    }


# ── MEAN REVERSION ────────────────────────────────────────────────────────────

def strategy_mean_reversion(df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """
    Entry: RSI < 35 (oversold) + close touches or breaches lower Bollinger
    Band + above-average volume (panic selling). Stock must still be above
    its 200 SMA (not broken downtrend — just a temporary dip).
    """
    curr = df.iloc[-1]
    f = cfg["filters"]

    above_200 = curr["Close"] > curr["SMA_200"]
    oversold = curr.get("RSI", 100) < f["rsi_mean_reversion_max"]
    bb_touch = curr["Close"] <= curr["BB_lower"]
    vol_surge = curr["Volume"] > curr["Vol_SMA"]
    # Reversal candle: closes off the low (wick below body)
    reversal_candle = curr["Close"] > curr["Low"] + 0.3 * (curr["High"] - curr["Low"])

    if not (above_200 and oversold and bb_touch and vol_surge and reversal_candle):
        return None

    confidence = _base_confidence(curr, df)
    if confidence < f["min_confidence_score"]:
        return None

    ent = round(float(curr["Close"]), 2)
    atr = float(curr["ATR"])
    sl = round(float(curr["Low"]) - 0.5 * atr, 2)
    target_val = round(float(curr["BB_mid"]), 2)  # Target = mean (mid BB)

    return {
        "logic": "MEAN_REVERSION",
        "entry": ent,
        "sl": sl,
        "atr": atr,
        "rsi": round(float(curr.get("RSI", 0)), 1),
        "confidence": min(confidence, 10),
        "target_type": "FIXED",
        "target_val": target_val,
        "sl_mult": 0.5,
    }


# ── 52-WEEK HIGH BREAKOUT ─────────────────────────────────────────────────────

def strategy_52w_high(df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """
    Entry: Price makes a fresh 52-week high close for the first time
    (i.e. previous close was NOT a 52W high). Strong volume required.
    ADX > 25 required to confirm it's a genuine trend breakout.
    """
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    f = cfg["filters"]

    # First time crossing 52W high (not already there)
    fresh_52w = (
        curr["Close"] > curr["High_52W"]
        and prev["Close"] <= prev["High_52W"]
    )
    vol_surge = curr["Volume"] > 1.5 * curr["Vol_SMA"]  # Stronger volume bar for 52W
    trend_strong = curr.get("ADX", 0) > 25
    rsi_ok = curr.get("RSI", 0) >= f["rsi_breakout_min"]

    if not (fresh_52w and vol_surge and trend_strong and rsi_ok):
        return None

    confidence = _base_confidence(curr, df) + 3  # +3 for rare, high-quality setup
    if confidence < f["min_confidence_score"]:
        return None

    ent = round(float(curr["Close"]), 2)
    atr = float(curr["ATR"])
    sl = round(ent - 2.0 * atr, 2)

    return {
        "logic": "52W_HIGH",
        "entry": ent,
        "sl": sl,
        "atr": atr,
        "rsi": round(float(curr.get("RSI", 0)), 1),
        "confidence": min(confidence, 10),
        "target_type": "TRAILING_EMA20",
        "target_val": None,
        "sl_mult": 2.0,
    }


# ── Strategy router ────────────────────────────────────────────────────────────

STRATEGY_MAP = {
    "PULLBACK": strategy_pullback,
    "BREAKOUT": strategy_breakout,
    "MEAN_REVERSION": strategy_mean_reversion,
    "52W_HIGH": strategy_52w_high,
}


def evaluate(logic: str, df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """
    Route to the correct strategy function and handle any unexpected errors
    without crashing the overall scan.
    """
    fn = STRATEGY_MAP.get(logic)
    if fn is None:
        logger.warning(f"Unknown strategy logic: '{logic}'")
        return None
    try:
        return fn(df, cfg)
    except Exception as e:
        logger.debug(f"Strategy '{logic}' raised an error: {e}")
        return None
