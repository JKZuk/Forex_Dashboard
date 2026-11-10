"""Data surprises: how recent releases compare with their own recent trend.

Free data has no consensus forecasts, so the surprise proxy for a series is

    change mode : (x_t - x_{t-1}) - mean(previous k changes)
    raw change  : x_t - x_{t-1}                           (policy rate: any move is news)
    level mode  : x_t - mean(previous k levels)          (noisy flows: NFP, claims)

divided by the historical standard deviation of the same quantity, clipped at
+/- z_clip and signed by catalog `bullish_when`. Where the calendar feed has
both actual and forecast, a genuine actual-vs-forecast surprise is added.
Releases are weighted by catalog impact and by exponential recency decay.
"""
from __future__ import annotations

import numpy as np

from ...catalog import INDICATORS
from ...regions import Region, series_id
from ..data import Context
from ..signals import fmt_sigma, squash
from . import ComponentResult, missing, register

SHORT_NAMES = {
    "POLICY_RATE": "policy rate", "CPI_YOY": "CPI", "CORE_CPI_YOY": "core CPI", "PCE_YOY": "PCE",
    "CORE_PCE_YOY": "core PCE", "UNEMPLOYMENT": "unemployment", "EMPLOYMENT_CHG": "employment change",
    "JOBLESS_CLAIMS": "jobless claims", "PMI": "PMI", "CONSUMER_CONF": "consumer confidence",
    "RETAIL_SALES_YOY": "retail sales", "INDUSTRIAL_PROD_YOY": "industrial production", "GDP_YOY": "GDP",
    "TRADE_BALANCE": "trade balance", "EXPORTS_YOY": "exports", "IMPORTS_YOY": "imports",
    "CURRENT_ACCOUNT_GDP": "current account",
}


def _calendar_z(actual: float, forecast: float, previous: float | None, cal_z: float) -> float:
    """Crude sigma-equivalent of an actual-vs-forecast miss (no history needed)."""
    gap = actual - forecast
    if gap == 0:
        return 0.0
    ref = max(0.1 * abs(forecast), abs(forecast - previous) if previous is not None else 0.0, 1e-9)
    magnitude = min(1.0, abs(gap) / ref)
    return (1.0 if gap > 0 else -1.0) * (0.5 + 0.5 * magnitude) * cal_z


@register("data_surprise")
def data_surprise(ctx: Context, region: Region, params: dict) -> ComponentResult:
    scfg = ctx.cfg.get("surprise", {})
    impact_w = {int(k): float(v) for k, v in scfg.get("impact_weight", {1: 1, 2: 2, 3: 3.5}).items()}
    exclude = set(scfg.get("exclude", []) or [])
    level_mode = set(scfg.get("level_mode", []) or [])
    raw_change = set(scfg.get("raw_change", []) or [])
    sd_floor = scfg.get("sd_floor", {}) or {}
    k, vol_obs = int(scfg.get("trend_obs", 3)), int(scfg.get("vol_obs", 36))
    min_hist, z_clip = int(scfg.get("min_history", 12)), float(scfg.get("z_clip", 3.0))
    window = float(params.get("window_days", 28))
    half_life = float(params.get("half_life_days", 10))

    items: list[tuple[str, float, float]] = []  # (name, directional z, weight)
    for ind in INDICATORS:
        if ind.key in exclude:
            continue
        sid = series_id(region.code, ind.key)
        ts = ctx.ts(sid)
        if ts is None:
            continue
        mode = "level" if ind.key in level_mode else "raw_change" if ind.key in raw_change else "change"
        z_all = ctx.store.surprise_z(sid, mode, k, vol_obs, min_hist, z_clip, float(sd_floor.get(ind.key, 0.0)))
        if z_all is None:
            continue
        if ts.freq == "D" and mode != "raw_change":
            continue  # a daily market series is not a "release"
        lo = max(0, len(ts) - 400)
        z = z_all[lo: len(ts)]
        age = ctx.release_age(ts)[lo:]
        keep = (age <= window) & np.isfinite(z)
        if mode == "raw_change":
            keep &= z != 0  # only actual moves count; an unchanged rate adds no information
        direction = 1.0 if ind.bullish_when == "higher" else -1.0
        for i in np.nonzero(keep)[0]:
            w = impact_w.get(ind.impact, 1.0) * 0.5 ** (float(age[i]) / half_life)
            items.append((SHORT_NAMES.get(ind.key, ind.name), direction * float(z[i]), w))

    cal_w = {str(k_): float(v) for k_, v in scfg.get("calendar_impact_weight", {}).items()}
    lower_words = [w.lower() for w in scfg.get("calendar_lower_is_bullish", [])]
    cal_z = float(scfg.get("calendar_z", 1.5))
    for age_days, ev in ctx.calendar(region.code, window):
        direction = -1.0 if any(w in ev.title.lower() for w in lower_words) else 1.0
        z_ev = direction * _calendar_z(ev.actual, ev.forecast, ev.previous, cal_z)
        w = cal_w.get(ev.impact, 2.0) * 0.5 ** (age_days / half_life)
        items.append((f"{ev.title[:28]} (vs forecast)", z_ev, w))

    if not items:
        return missing(f"No data releases in the last {int(window)} days.")

    total_w = sum(w for _, _, w in items)
    avg = sum(z * w for _, z, w in items) / (total_w + float(params.get("prior_weight", 3.0)))
    signal = squash(avg, float(params.get("z_scale", 0.6)))

    ranked = sorted(items, key=lambda it: abs(it[1] * it[2]), reverse=True)
    top = ", ".join(f"{name} {fmt_sigma(z)}" for name, z, _ in ranked[:3])
    raw = f"net {fmt_sigma(avg)} from {len(items)} releases; {top}"
    if signal > 0.1:
        verdict = "have on balance come in stronger / more hawkish than their recent trend, which supports the currency"
    elif signal < -0.1:
        verdict = "have on balance come in weaker / more dovish than their recent trend, which weighs on the currency"
    else:
        verdict = "have been broadly in line with their recent trend"
    detail = (f"The {len(items)} releases of the last {int(window)} days {verdict} "
              f"(impact- and recency-weighted surprise {fmt_sigma(avg)}; biggest: {top}; "
              f"σ signed so that + is currency-positive).")
    return ComponentResult(signal, raw, detail)
