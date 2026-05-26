"""
indicators.py — Technical indicator calculations.

All functions take a DataFrame with OHLCV columns and add indicator columns
in-place (or return a scalar). Kept pure and independently testable.
"""

import pandas as pd
import numpy as np


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMA_200, EMA_20, EMA_50 columns."""
    df = df.copy()
    df["SMA_200"] = df["Close"].rolling(200).mean()
    df["EMA_20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA_50"] = df["Close"].ewm(span=50, adjust=False).mean()
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Add ATR (Average True Range) column.
    True Range = max(H-L, |H-PrevC|, |L-PrevC|)
    """
    df = df.copy()
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            (df["High"] - df["Low"]),
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.rolling(period).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add RSI column using Wilder's smoothing (EWM with alpha=1/period)."""
    df = df.copy()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    return df


def add_bollinger_bands(df: pd.DataFrame, period: int = 20,
                        std_dev: float = 2.0) -> pd.DataFrame:
    """Add BB_upper, BB_mid, BB_lower columns."""
    df = df.copy()
    mid = df["Close"].rolling(period).mean()
    std = df["Close"].rolling(period).std()
    df["BB_mid"] = mid
    df["BB_upper"] = mid + std_dev * std
    df["BB_lower"] = mid - std_dev * std
    return df


def add_volume_sma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Add Vol_SMA column."""
    df = df.copy()
    df["Vol_SMA"] = df["Volume"].rolling(period).mean()
    return df


def add_prev_high(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Add rolling N-day previous high (shifted by 1 to avoid lookahead)."""
    df = df.copy()
    df["Prev_High_20"] = df["High"].rolling(period).max().shift(1)
    return df


def add_52w_high(df: pd.DataFrame) -> pd.DataFrame:
    """Add 52-week rolling high column (shifted by 1)."""
    df = df.copy()
    df["High_52W"] = df["High"].rolling(252).max().shift(1)
    return df


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Add ADX (Average Directional Index) column.
    ADX > 25 indicates a strong trend.
    """
    df = df.copy()
    prev_high = df["High"].shift(1)
    prev_low = df["Low"].shift(1)

    plus_dm = (df["High"] - prev_high).clip(lower=0)
    minus_dm = (prev_low - df["Low"]).clip(lower=0)
    # Where both move, zero out the smaller one
    both = (plus_dm > 0) & (minus_dm > 0)
    plus_dm[both & (minus_dm >= plus_dm)] = 0
    minus_dm[both & (plus_dm > minus_dm)] = 0

    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [(df["High"] - df["Low"]),
         (df["High"] - prev_close).abs(),
         (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.ewm(alpha=1 / period, adjust=False).mean()
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Convenience: add every indicator used by the strategy engine."""
    df = add_moving_averages(df)
    df = add_atr(df)
    df = add_rsi(df)
    df = add_bollinger_bands(df)
    df = add_volume_sma(df)
    df = add_prev_high(df)
    df = add_52w_high(df)
    df = add_adx(df)
    return df
