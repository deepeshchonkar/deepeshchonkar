"""
risk.py — Risk management layer.

Responsibilities
----------------
- Dynamic position sizing scaled by India VIX
- Minimum R:R ratio filter
- Portfolio exposure cap
- Sector concentration limit (max N signals per sector per day)
- Monthly drawdown circuit breaker
"""

from typing import Optional
from utils import get_logger

logger = get_logger(__name__)


# ── Position sizing ────────────────────────────────────────────────────────────

def get_risk_pct(vix: Optional[float], cfg: dict) -> float:
    """
    Return risk percentage per trade based on current VIX level.

    VIX > high_threshold  → reduce risk (more volatile environment)
    VIX < low_threshold   → allow higher risk (calm market)
    Otherwise             → use base risk
    """
    r = cfg["risk"]
    if vix is None:
        logger.debug("VIX unavailable. Using base risk %.")
        return r["base_risk_pct"]

    if vix > r["high_vix_threshold"]:
        logger.info(f"VIX={vix:.1f} (HIGH). Risk reduced to {r['high_vix_risk_pct']*100:.1f}%")
        return r["high_vix_risk_pct"]

    if vix < r["low_vix_threshold"]:
        logger.info(f"VIX={vix:.1f} (LOW). Risk raised to {r['low_vix_risk_pct']*100:.1f}%")
        return r["low_vix_risk_pct"]

    return r["base_risk_pct"]


def calculate_position(
    wallet_cap: float,
    entry: float,
    sl: float,
    risk_pct: float,
    cfg: dict,
) -> tuple[int, float]:
    """
    Calculate quantity and total position value.

    Formula: qty = floor( (cap * risk_pct) / (entry - sl) )

    Returns (qty, position_value). Returns (0, 0) on invalid inputs.
    """
    risk = cfg["risk"]
    risk_amount = wallet_cap * risk_pct
    risk_per_share = entry - sl

    if risk_per_share <= 0:
        logger.debug(f"Invalid risk_per_share={risk_per_share:.2f} (entry={entry}, sl={sl})")
        return 0, 0.0

    qty = int(risk_amount // risk_per_share)
    position_value = qty * entry

    # Guard: qty must be at least min_qty
    if qty < risk["min_qty"]:
        return 0, 0.0

    # Guard: position value cannot exceed wallet cap
    if position_value > wallet_cap:
        qty = int(wallet_cap // entry)
        position_value = qty * entry

    if qty < risk["min_qty"]:
        return 0, 0.0

    return qty, round(position_value, 2)


# ── R:R filter ─────────────────────────────────────────────────────────────────

def passes_rr_filter(entry: float, sl: float, target_val: Optional[float],
                     cfg: dict) -> tuple[bool, float]:
    """
    Check if the trade meets the minimum reward:risk ratio.
    Trailing-target strategies (target_val=None) are assumed to meet R:R
    as the exit is managed dynamically.

    Returns (passes, rr_ratio).
    """
    min_rr = cfg["risk"]["min_rr_ratio"]

    if target_val is None:
        # Trailing target — assume it will be managed
        return True, 0.0

    risk = entry - sl
    reward = target_val - entry

    if risk <= 0:
        return False, 0.0

    rr = round(reward / risk, 2)
    passes = rr >= min_rr

    if not passes:
        logger.debug(f"R:R {rr:.2f} below minimum {min_rr}. Signal rejected.")

    return passes, rr


# ── Portfolio exposure cap ─────────────────────────────────────────────────────

def check_portfolio_cap(
    new_position_value: float,
    current_exposure: float,
    total_cap: float,
    cfg: dict,
) -> bool:
    """
    Return True if adding the new position stays within the portfolio
    exposure cap (e.g. max 60% of total capital deployed at once).
    """
    max_exposure = total_cap * cfg["risk"]["max_portfolio_exposure_pct"]
    projected = current_exposure + new_position_value

    if projected > max_exposure:
        logger.info(
            f"Portfolio cap hit: projected exposure ₹{projected:,.0f} "
            f"> max ₹{max_exposure:,.0f}. Signal skipped."
        )
        return False
    return True


# ── Sector concentration ──────────────────────────────────────────────────────

class SectorTracker:
    """
    Tracks how many signals have been emitted per sector today.
    Rejects signals that would breach the per-sector cap.

    NSE tickers don't carry sector info via yfinance easily, so we use
    a simple heuristic: count per wallet (proxy for sector/universe).
    For a richer implementation, integrate a sector mapping CSV.
    """

    def __init__(self, max_per_sector: int):
        self._max = max_per_sector
        self._counts: dict[str, int] = {}

    def can_add(self, sector_key: str) -> bool:
        return self._counts.get(sector_key, 0) < self._max

    def add(self, sector_key: str) -> None:
        self._counts[sector_key] = self._counts.get(sector_key, 0) + 1

    def count(self, sector_key: str) -> int:
        return self._counts.get(sector_key, 0)


# ── Monthly drawdown circuit breaker ──────────────────────────────────────────

def is_circuit_breaker_tripped(
    monthly_pnl: float,
    total_cap: float,
    cfg: dict,
) -> bool:
    """
    Return True if the monthly realised loss has exceeded the configured
    drawdown limit. When tripped, no new entries should be made.
    """
    limit = -abs(total_cap * cfg["risk"]["monthly_drawdown_limit_pct"])

    if monthly_pnl < limit:
        logger.warning(
            f"🛑 Circuit breaker TRIPPED: monthly P&L ₹{monthly_pnl:,.0f} "
            f"< limit ₹{limit:,.0f}. No new entries this month."
        )
        return True
    return False
