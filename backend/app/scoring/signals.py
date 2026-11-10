"""Shared signal helpers: squashing, blending, time-series statistics, formatting.

Everything here is pure (no DB, no config lookups) so it is easy to unit test
and to reuse from new components.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterable, Sequence

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .data import TS

BDAYS_PER_DAY = 5.0 / 7.0  # business days per calendar day


# --------------------------------------------------------------- squashing ---
def squash(x: float, scale: float) -> float:
    """Robust map of an unbounded reading to (-1, +1): tanh(x / scale)."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    return float(math.tanh(x / scale))


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, x)))


def blend(parts: Iterable[tuple[float | None, float]]) -> float | None:
    """Weighted average of (value, weight) pairs, ignoring None values.

    Returns None when nothing is available, so callers degrade gracefully.
    """
    num = den = 0.0
    for value, weight in parts:
        if value is None or weight <= 0 or value != value:
            continue
        num += value * weight
        den += weight
    return num / den if den > 0 else None


# ------------------------------------------------------- series statistics ---
def _std(values: np.ndarray) -> float | None:
    if len(values) < 3:
        return None
    sd = float(np.std(values, ddof=1))
    return sd if sd > 1e-12 else None


def vol_z(ts: "TS", days: int, vol_days: int = 365, log: bool = True) -> tuple[float, float] | None:
    """Volatility-standardised move over `days` calendar days.

    Returns (move, z) where move is the log return (or plain change when
    log=False) and z = move / (daily sigma * sqrt(business days)). Daily sigma
    comes from the last `vol_days` of one-step changes. None if history is short.
    """
    past = ts.ago(days)
    if past is None:
        return None
    window = ts.since(vol_days + 1)
    if len(window) < 30:  # low-frequency series (e.g. monthly copper): use a longer window
        window = ts.since(3 * vol_days + 1)
    if len(window) < 10:
        return None
    if log:
        if past <= 0 or ts.last <= 0 or np.any(window <= 0):
            return None
        move = math.log(ts.last / past)
        steps = np.diff(np.log(window))
    else:
        move = ts.last - past
        steps = np.diff(window)
    sd = _std(steps)
    if sd is None:
        return None
    # one observation step ~ (span / n) calendar days; convert sigma to per business day
    span_days = max(1.0, float(ts.d[-1] - ts.d[-len(window)]))
    step_bdays = max(1.0, span_days * BDAYS_PER_DAY / max(1, len(window) - 1))
    sd_daily = sd / math.sqrt(step_bdays)
    z = move / (sd_daily * math.sqrt(max(1.0, days * BDAYS_PER_DAY)))
    return move, float(z)


def momentum_z(ts: "TS", lookbacks: Sequence[dict], vol_days: int, log: bool = True
               ) -> tuple[float, list[tuple[int, float]]] | None:
    """Blend of vol-standardised moves over several lookbacks.

    Returns (z, [(days, move), ...]) or None when no lookback has history.
    """
    parts: list[tuple[float | None, float]] = []
    moves: list[tuple[int, float]] = []
    for lb in lookbacks:
        r = vol_z(ts, int(lb["days"]), vol_days, log=log)
        if r is None:
            continue
        parts.append((r[1], float(lb.get("weight", 1.0))))
        moves.append((int(lb["days"]), r[0]))
    z = blend(parts)
    return (z, moves) if z is not None else None


def level_z(ts: "TS", vol_days: int, min_obs: int = 8) -> float | None:
    """Latest value as a z-score against its own history over `vol_days`."""
    window = ts.since(vol_days)
    if len(window) < min_obs:
        return None
    sd = _std(window)
    if sd is None:
        return None
    return float((ts.last - float(np.mean(window))) / sd)


def trend_z(ts: "TS", recent_days: int, base_days: int, vol_days: int) -> tuple[float, float, float] | None:
    """Recent average vs the preceding base-period average, in own-history sigmas.

    Returns (recent_mean, base_mean, z). Works for any frequency because the
    windows are in calendar days measured back from the latest observation.
    """
    end = ts.last_date
    recent = ts.between(end - recent_days, end)
    base = ts.between(end - recent_days - base_days, end - recent_days)
    hist = ts.since(vol_days)
    if len(recent) < 1 or len(base) < 3 or len(hist) < 8:
        return None
    sd = _std(hist)
    if sd is None:
        return None
    r, b = float(np.mean(recent)), float(np.mean(base))
    return r, b, float((r - b) / sd)


def percentile_rank(values: np.ndarray, x: float) -> float:
    """Mid-rank percentile of x within `values` (0..1); ties count half, so it is symmetric."""
    if not len(values):
        return 0.5
    return float((np.sum(values < x) + 0.5 * np.sum(values == x)) / len(values))


# -------------------------------------------------------------- formatting ---
def fmt_pp(x: float, digits: int = 2) -> str:
    return f"{x:+.{digits}f}pp"


def fmt_pct(x: float, digits: int = 1) -> str:
    """Format a fraction (0.021) as a signed percentage (+2.1%)."""
    return f"{x * 100:+.{digits}f}%"


def fmt_sigma(x: float) -> str:
    return f"{x + 0.0:+.1f}σ".replace("-0.0σ", "0.0σ").replace("+0.0σ", "0.0σ")


def fmt_days(days: int) -> str:
    if days < 28:
        return f"{days // 7}w" if days % 7 == 0 else f"{days}d"
    return f"{round(days / 30.4)}m"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
