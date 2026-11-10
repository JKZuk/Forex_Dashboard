"""Price momentum (all horizons) and long-term valuation.

The price used is the currency's log index against an equal-weight basket of
the other currencies (see DataStore._build_fx_basket), so the reading is the
currency's own strength, not just the mirror image of the dollar.
"""
from __future__ import annotations

import numpy as np

from ...regions import Region
from ..data import Context
from ..signals import blend, fmt_days, fmt_pct, fmt_sigma, squash
from . import ComponentResult, missing, register


@register("price_momentum")
def price_momentum(ctx: Context, region: Region, params: dict) -> ComponentResult:
    ts = ctx.fx(region.currency)
    if ts is None:
        return missing(f"No recent FX price history for {region.currency}.")
    basis = ctx.fx_basis(region.currency)
    vol_days = int(params.get("vol_days", 365))
    z_scale = float(params.get("z_scale", 1.25))

    parts: list[tuple[float | None, float]] = []
    bits: list[str] = []
    zs: list[float] = []
    for lb in params.get("lookbacks", []):
        days = int(lb["days"])
        past = ts.ago(days)
        window = ts.since(vol_days + 1)
        if past is None or len(window) < 30:
            continue
        steps = np.diff(window)
        sd = float(np.std(steps, ddof=1))
        if sd <= 1e-12:
            continue
        move = ts.last - past  # log return (series is a log index)
        z = move / (sd * np.sqrt(max(1.0, days * 5.0 / 7.0)))
        parts.append((squash(float(z), z_scale), float(lb.get("weight", 1.0))))
        bits.append(f"{fmt_days(days)} {fmt_pct(float(np.expm1(move)))}")
        zs.append(float(z))
    trend = blend(parts)

    mr_cfg = params.get("mean_reversion") or {}
    reversion: float | None = None
    mr_text = mr_bit = ""
    if mr_cfg and float(mr_cfg.get("weight", 0)) > 0:
        window = ts.since(int(mr_cfg.get("window_days", 1825)))
        span = int(ts.d[-1] - ts.d[-len(window)]) if len(window) else 0
        if span >= int(mr_cfg.get("min_days", 1095)) and len(window) >= 100:
            sd = float(np.std(window, ddof=1))
            if sd > 1e-12:
                dev = ts.last - float(np.mean(window))
                reversion = -squash(dev / sd, float(mr_cfg.get("z_scale", 2.0)))
                mr_bit = f"{fmt_pct(float(np.expm1(dev)))} vs {round(span / 365)}y avg"
                mr_text = (f" It trades {fmt_pct(float(np.expm1(dev)))} from its {round(span / 365)}-year average "
                           f"({fmt_sigma(dev / sd)}), a {'contrarian headwind' if reversion < -0.1 else 'contrarian tailwind' if reversion > 0.1 else 'neutral valuation'}.")

    if trend is None and reversion is None:
        return missing(f"Not enough FX price history for {region.currency}.")
    mr_w = float(mr_cfg.get("weight", 0)) if mr_cfg else 0.0
    signal = blend([(trend, 1.0 - mr_w if mr_cfg else 1.0), (reversion, mr_w)])
    assert signal is not None
    raw = f"{', '.join(bits)} {basis}".strip() + (f"; {mr_bit}" if mr_bit else "")
    if trend is None:
        lead = f"{region.currency} lacks enough history for a trend reading."
    else:
        word = "risen" if trend > 0.1 else "fallen" if trend < -0.1 else "moved sideways"
        lead = (f"{region.currency} has {word} {basis} ({', '.join(bits)}; "
                f"{fmt_sigma(float(np.mean(zs)))} relative to its normal volatility); momentum is trend-following.")
    return ComponentResult(signal, raw, lead + mr_text)
