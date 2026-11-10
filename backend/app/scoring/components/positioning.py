"""Speculative positioning from CFTC Commitments of Traders.

Two ideas, combined explicitly:
  trend    - specs adding to net longs (change in net % of open interest over
             4 and 13 weeks) is trend-following bullish;
  extreme  - when net positioning sits in the top / bottom tail of its 3-year
             range the trade is crowded and the tilt turns contrarian.

signal = trend_weight * trend * (1 - crowding_damp * |extreme|  if the trend is
         pushing further into the extreme) + extreme_weight * extreme
"""
from __future__ import annotations

import numpy as np

from ...regions import Region
from ..data import Context
from ..signals import blend, clip, fmt_pp, ordinal, percentile_rank, squash
from . import ComponentResult, missing, register


@register("cot_positioning")
def cot_positioning(ctx: Context, region: Region, params: dict) -> ComponentResult:
    ts = ctx.cot(region.currency)
    if ts is None or len(ts) < 5:
        return missing(f"No recent CFTC positioning data for {region.currency}.")
    net = ts.last
    scale = float(params.get("trend_scale", 10.0))
    changes = [(w, ts.chg(int(w) * 7)) for w in params.get("trend_weeks", [4, 13])]
    trend = blend([(squash(c, scale), 1.0) for _, c in changes if c is not None])

    pct_weeks = int(params.get("percentile_weeks", 156))
    hist = ts.since(pct_weeks * 7)
    extreme: float | None = None
    pct: float | None = None
    if len(hist) >= int(params.get("min_percentile_weeks", 52)):
        pct = percentile_rank(hist, net)
        hi, lo = float(params.get("extreme_high", 0.85)), float(params.get("extreme_low", 0.15))
        if pct >= hi:
            extreme = -clip((pct - hi) / max(1e-9, 1 - hi))
        elif pct <= lo:
            extreme = clip((lo - pct) / max(1e-9, lo))
        else:
            extreme = 0.0

    if trend is None and extreme is None:
        return missing(f"Not enough CFTC history for {region.currency}.")
    t_w, e_w = float(params.get("trend_weight", 0.6)), float(params.get("extreme_weight", 0.4))
    damped = trend
    if trend is not None and extreme is not None and extreme != 0.0 and np.sign(trend) == -np.sign(extreme):
        damped = trend * (1.0 - float(params.get("crowding_damp", 0.5)) * abs(extreme))
    if extreme is None:
        signal = damped
    elif damped is None:
        signal = e_w * extreme
    else:
        signal = t_w * damped + e_w * extreme
    assert signal is not None
    signal = clip(signal)

    chg_txt = ", ".join(f"{w}w {fmt_pp(c, 1)}" for w, c in changes if c is not None)
    raw = f"net {net:+.1f}% of OI"
    if pct is not None:
        raw += f", {ordinal(round(pct * 100))} pct of {round(len(hist) / 52)}y"
    if chg_txt:
        raw += f"; chg {chg_txt}"
    side = "long" if net >= 0 else "short"
    parts = [f"Speculators are net {side} {region.currency} ({net:+.1f}% of open interest)"]
    if trend is not None:
        parts.append(f"and have been {'adding to longs / covering shorts' if trend > 0.05 else 'cutting longs / adding shorts' if trend < -0.05 else 'holding steady'} ({chg_txt})")
    text = " ".join(parts) + "."
    if extreme is not None and extreme != 0.0 and pct is not None:
        text += (f" Positioning is at the {ordinal(round(pct * 100))} percentile of its {round(len(hist) / 52)}-year range - "
                 f"{'crowded long, a contrarian bearish tilt' if extreme < 0 else 'crowded short, a contrarian bullish tilt'}.")
    elif pct is not None:
        text += f" Positioning is not extreme ({ordinal(round(pct * 100))} percentile), so the trend dominates."
    return ComponentResult(signal, raw, text)
