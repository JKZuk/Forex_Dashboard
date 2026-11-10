"""Commodity prices and China activity, for currencies with a configured
exposure (CAD oil; AUD copper + China; NZD broad commodities + China; lightly
JPY / EUR). Regions without an entry in the yaml do not get this component.
"""
from __future__ import annotations

from typing import Any

from ...regions import Region, series_id
from ..data import Context
from ..signals import blend, clip, fmt_days, fmt_pct, fmt_sigma, level_z, momentum_z, squash, trend_z
from . import ComponentResult, missing, register

COMMODITY_SERIES = {"OIL": "GLOBAL.OIL", "COPPER": "GLOBAL.COPPER", "GOLD": "GLOBAL.GOLD"}
BROAD = ("OIL", "COPPER", "GOLD")
NAMES = {"OIL": "oil", "COPPER": "copper", "GOLD": "gold", "BROAD": "broad commodities", "CHINA": "China activity"}


def china_activity(ctx: Context) -> dict[str, Any] | None:
    """Shared per-date China gauge in [-1, +1]: CN activity vs its own history."""
    return ctx.memo("china_activity", lambda: _china_activity(ctx))


def _china_activity(ctx: Context) -> dict[str, Any] | None:
    c = ctx.cfg.get("china_activity", {})
    vol_days = int(c.get("vol_days", 1825))
    parts: list[tuple[float | None, float]] = []
    used: list[str] = []
    for key, weight in (c.get("indicators") or {}).items():
        ts = ctx.ts(series_id("CN", key))
        if ts is None:
            continue
        lz = level_z(ts, vol_days)
        tz = trend_z(ts, int(c.get("recent_days", 92)), int(c.get("base_days", 365)), vol_days)
        z = blend([(lz, 0.5), (tz[2] if tz else None, 0.5)])
        if z is None:
            continue
        parts.append((z, float(weight)))
        used.append(key)
    z = blend(parts)
    if z is None:
        return None
    return {"z": z, "signal": squash(z, float(c.get("z_scale", 1.0))), "n": len(used)}


def _commodity(ctx: Context, name: str, params: dict) -> tuple[float, str] | None:
    """(signal, text) for one commodity or the BROAD average."""
    if name == "BROAD":
        got = [g for g in (_commodity(ctx, n, params) for n in BROAD) if g is not None]
        if not got:
            return None
        return sum(g[0] for g in got) / len(got), f"broad commodities ({len(got)}) {sum(g[0] for g in got) / len(got):+.2f}"
    ts = ctx.ts(COMMODITY_SERIES.get(name, f"GLOBAL.{name}"))
    if ts is None:
        return None
    mz = momentum_z(ts, params.get("lookbacks", [{"days": 91}]), int(params.get("vol_days", 365)))
    if mz is None:
        return None
    days, move = mz[1][0]
    return squash(mz[0], float(params.get("z_scale", 1.25))), f"{NAMES.get(name, name.lower())} {fmt_days(days)} {fmt_pct(float(move))}"


@register("commodity_china")
def commodity_china(ctx: Context, region: Region, params: dict) -> ComponentResult:
    spec = (params.get("regions") or {}).get(region.code)
    if not spec or not spec.get("exposures"):
        return ComponentResult(None, applicable=False)
    mult = float(spec.get("weight_mult", 1.0))
    parts: list[tuple[float | None, float]] = []
    bits: list[str] = []
    drivers: list[str] = []
    for name, beta in spec["exposures"].items():
        beta = float(beta)
        if name == "CHINA":
            gauge = china_activity(ctx)
            got = None if gauge is None else (gauge["signal"], f"China activity {fmt_sigma(gauge['z'])} ({gauge['n']} series)")
        else:
            got = ctx.memo(("commodity", name, id(params)), lambda n=name: _commodity(ctx, n, params))
        if got is None:
            continue
        sign = 1.0 if beta >= 0 else -1.0
        parts.append((sign * got[0], abs(beta)))
        bits.append(got[1])
        drivers.append(f"{NAMES.get(name, name)} ({'+' if beta >= 0 else '-'})")
    signal = blend(parts)
    if signal is None:
        return missing("No commodity price or China activity data available.", mult)
    signal = clip(signal)
    effect = "a tailwind" if signal > 0.1 else "a headwind" if signal < -0.1 else "broadly neutral"
    detail = (f"{region.currency} is sensitive to {', '.join(drivers)}; current readings ({'; '.join(bits)}) "
              f"are {effect} for the currency.")
    return ComponentResult(signal, "; ".join(bits), detail, mult)
