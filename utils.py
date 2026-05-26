"""
utils.py — Logging, config loading, and shared helpers.
"""

import logging
import os
import yaml
from pathlib import Path
from datetime import datetime


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    """Load and return the YAML config. Raises FileNotFoundError if missing."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(cfg_path, "r") as f:
        return yaml.safe_load(f)


# ── Logger factory ─────────────────────────────────────────────────────────────

def get_logger(name: str, log_file: str = "sniper_bot.log",
               level: str = "INFO") -> logging.Logger:
    """
    Return a logger that writes to both a rotating file and stdout.
    Calling this multiple times with the same name returns the same logger
    (standard Python logging behaviour — no duplicate handlers).
    """
    logger = logging.getLogger(name)
    if logger.handlers:          # Already configured
        return logger

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(numeric_level)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(numeric_level)
    logger.addHandler(ch)

    return logger


# ── Environment validation ─────────────────────────────────────────────────────

def validate_env() -> None:
    """
    Assert all required environment variables are set at startup.
    Fail fast rather than discovering a missing secret at send-time.
    """
    required = ["EMAIL_SENDER", "EMAIL_PASSWORD", "EMAIL_RECEIVER"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {missing}\n"
            "Set them in your shell or GitHub Secrets."
        )


# ── Date helpers ───────────────────────────────────────────────────────────────

def today_str() -> str:
    """Return today's date as 'DD-Mon-YYYY', e.g. '26-May-2026'."""
    return datetime.now().strftime("%d-%b-%Y")


def today_iso() -> str:
    """Return today's date as 'YYYY-MM-DD' for DB storage."""
    return datetime.now().strftime("%Y-%m-%d")


# ── Misc helpers ───────────────────────────────────────────────────────────────

def safe_round(value, decimals: int = 2):
    """Round a value safely, returning None if value is None/NaN."""
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None
