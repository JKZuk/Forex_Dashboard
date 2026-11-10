"""Global risk appetite, mapped to each currency through its risk beta.

The gauge R in [-1, +1] (+ = risk-on) blends: VIX (level vs 1y and recent
change), S&P 500 momentum, US high-yield spread (level and change) and the
tone of GLOBAL-tagged news. signal = clip(R * beta).
"""
from __future__ import annotations

from typing import Any

from ...regions import Region
from ..data import Context
from ..signals import blend, clip, level_z, momentum_z, squash, vol_z
from . import ComponentResult, missing, register
from .news import weighted_tone


def risk_gauge(ctx: Context) -> dict[str, Any] | None:
    """Shared per-date gauge: {"value": R, "parts": {name: signal}, "text": str}."""
    return ctx.memo("risk_gauge", lambda: _risk_gauge(ctx))


def _risk_gauge(ctx: Context) -> dict[str, Any] | None:
    g = ctx.cfg.get("risk_gauge", {})
    weights = g.get("weights", {})
    hist = int(g.get("history_days", 365))
    scale = float(g.get("z_scale", 1.25))
    parts: dict[str, float] = {}
    texts: list[str] = []

    vix = ctx.ts("GLOBAL.VIX")
    if vix is not None:
        lvl = level_z(vix, hist, min_obs=30)
        chg = vol_z(vix, int(g.get("vix_change_days", 7)), hist)
        z = blend([(lvl, 0.5), (chg[1] if chg else None, 0.5)])
        if z is not None:
            parts["vix"] = -squash(z, scale)
            texts.append(f"VIX {vix.last:.1f}")

    spx = ctx.ts("GLOBAL.SPX")
    if spx is not None:
        mz = momentum_z(spx, g.get("spx_lookbacks", [{"days": 7}, {"days": 30}]), hist)
        if mz is not None:
            parts["spx"] = squash(mz[0], scale)
            texts.append(f"SPX {mz[1][-1][1] * 100:+.1f}%/{mz[1][-1][0]}d")

    hy = ctx.ts("GLOBAL.HY_SPREAD")
    if hy is not None:
        lvl = level_z(hy, hist, min_obs=30)
        chg = vol_z(hy, int(g.get("hy_change_days", 30)), hist, log=False)
        z = blend([(lvl, 0.5), (chg[1] if chg else None, 0.5)])
        if z is not None:
            parts["hy_spread"] = -squash(z, scale)
            texts.append(f"HY {hy.last:.2f}%")

    tone = weighted_tone(ctx, "GLOBAL", float(g.get("news_lookback_days", 5)), float(g.get("news_half_life_days", 2)))
    if tone is not None:
        parts["news"] = squash(tone[0], float(g.get("news_scale", 0.25)))
        texts.append(f"news {tone[0]:+.2f}")

    value = blend([(parts.get(name), float(w)) for name, w in weights.items()])
    if value is None:
        return None
    return {"value": value, "parts": parts, "text": ", ".join(texts)}


@register("risk_sentiment")
def risk_sentiment(ctx: Context, region: Region, params: dict) -> ComponentResult:
    gauge = risk_gauge(ctx)
    if gauge is None:
        return missing("No global risk gauges (VIX, S&P 500, HY spread, global news) available.")
    beta = float(ctx.cfg.get("risk_beta", {}).get(region.currency, 0.0))
    r = float(gauge["value"])
    signal = clip(r * beta)
    mood = "risk-on" if r > 0.15 else "risk-off" if r < -0.15 else "neutral"
    raw = f"{mood} {r:+.2f} x beta {beta:+.2f}; {gauge['text']}"
    if beta < -0.05:
        kind = "a safe haven that gains when markets are fearful"
    elif beta > 0.5:
        kind = "a high-beta currency that gains when risk appetite is strong"
    elif beta > 0.05:
        kind = "mildly pro-risk"
    else:
        kind = "insensitive to risk appetite"
    effect = "a tailwind" if signal > 0.1 else "a headwind" if signal < -0.1 else "little net effect"
    detail = (f"Global risk appetite is {mood} ({r:+.2f} on a -1..+1 gauge: {gauge['text']}); "
              f"{region.currency} is {kind} (beta {beta:+.2f}), so this is {effect}.")
    return ComponentResult(signal, raw, detail)
