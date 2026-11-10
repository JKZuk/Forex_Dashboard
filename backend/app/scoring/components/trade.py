"""External balance components: trade balance trend (medium + long, heavier
for trade-sensitive regions) and the current account (long)."""
from __future__ import annotations

from ...regions import Region
from ..data import Context
from ..signals import blend, fmt_days, fmt_pp, fmt_sigma, squash, trend_z
from . import ComponentResult, missing, register


def _compact(x: float) -> str:
    a = abs(x)
    for div, suffix in ((1e12, "T"), (1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if a >= div:
            return f"{x / div:+.1f}{suffix}"
    return f"{x:+.1f}"


@register("trade_trend")
def trade_trend(ctx: Context, region: Region, params: dict) -> ComponentResult:
    mult = float(params.get("trade_sensitive_multiplier", 1.0)) if region.trade_sensitive else 1.0
    recent, base = int(params.get("recent_days", 92)), int(params.get("base_days", 365))
    ts = ctx.region_ts(region.code, "TRADE_BALANCE")
    tz = trend_z(ts, recent, base, int(params.get("vol_days", 1095))) if ts is not None else None
    bal = squash(tz[2], float(params.get("z_scale", 1.0))) if tz else None

    ex_w = float(params.get("exports_weight", 0.0))
    ex = ctx.relative("level:EXPORTS_YOY", region, lambda r: ctx.latest(r.code, "EXPORTS_YOY")) if ex_w > 0 else None
    ex_sig = squash(ex.diff, float(params.get("exports_scale", 10.0))) if ex else None

    signal = blend([(bal, 1.0 - ex_w), (ex_sig, ex_w)])
    if signal is None:
        return missing("No trade balance (or export growth) data for this region.", mult)
    bits, sentences = [], []
    if tz:
        bits.append(f"{fmt_days(recent)} avg {_compact(tz[0])} vs prior {fmt_days(base)} {_compact(tz[1])}, {fmt_sigma(tz[2])}")
        sentences.append(f"The trade balance over the last {fmt_days(recent)} averaged {_compact(tz[0])} against "
                         f"{_compact(tz[1])} in the preceding {fmt_days(base)} - "
                         f"{'an improvement' if tz[2] > 0.1 else 'a deterioration' if tz[2] < -0.1 else 'little change'} "
                         f"of {fmt_sigma(tz[2])} relative to its own history")
    if ex:
        bits.append(f"exports y/y {ex.own:+.1f}%, {fmt_pp(ex.diff, 1)} vs peers")
        sentences.append(f"export growth of {ex.own:+.1f}% y/y is {abs(ex.diff):.1f}pp "
                         f"{'above' if ex.diff >= 0 else 'below'} peers")
    tail = " Trade carries extra weight for this trade-sensitive economy." if mult > 1 else ""
    return ComponentResult(signal, "; ".join(bits), "; ".join(sentences) + "." + tail, mult)


@register("current_account")
def current_account(ctx: Context, region: Region, params: dict) -> ComponentResult:
    days = int(params.get("change_days", 730))
    mix = params.get("mix", {})
    lvl = ctx.relative("level:CURRENT_ACCOUNT_GDP", region, lambda r: ctx.latest(r.code, "CURRENT_ACCOUNT_GDP"))
    ts = ctx.region_ts(region.code, "CURRENT_ACCOUNT_GDP")
    own_chg = ts.chg(days) if ts is not None else None
    signal = blend([
        (squash(lvl.diff, float(params.get("level_scale", 4.0))) if lvl else None, float(mix.get("level", 0.7))),
        (squash(own_chg, float(params.get("change_scale", 2.0))) if own_chg is not None else None, float(mix.get("change", 0.3))),
    ])
    if signal is None or ts is None:
        return missing("No current account data for this region.")
    raw = f"{ts.last:+.1f}% of GDP"
    if lvl:
        raw += f", {fmt_pp(lvl.diff, 1)} vs peers"
    if own_chg is not None:
        raw += f", {fmt_days(days)} chg {fmt_pp(own_chg, 1)}"
    kind = "surplus" if ts.last >= 0 else "deficit"
    detail = f"The current account is in {kind} at {ts.last:+.1f}% of GDP"
    if lvl:
        detail += f", {abs(lvl.diff):.1f}pp {'better' if lvl.diff >= 0 else 'worse'} than the peer average"
    if own_chg is not None:
        detail += f", and has {'improved' if own_chg >= 0 else 'deteriorated'} by {abs(own_chg):.1f}pp over {fmt_days(days)}"
    detail += (": structural demand for the currency." if signal > 0.1 else
               ": a structural funding need that weighs on the currency." if signal < -0.1 else ": broadly neutral.")
    return ComponentResult(signal, raw, detail)
