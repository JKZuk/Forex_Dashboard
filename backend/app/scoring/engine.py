"""Scoring engine: combine component signals into scores and persist them.

score = 100 * k * sum_i( w_i / W_available * signal_i )

where w_i is the configured weight (times any region multiplier) and
W_available the total weight of components that had data. Components without
data are stored with signal NULL and contribution 0; coverage = W_available /
W_configured. k = 1 normally; when coverage is below
general.coverage_shrink_below, k = coverage / that threshold (thin evidence is
pulled towards neutral). Contributions always sum to the score.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..db import upsert
from ..models import Conclusion, Score, ScoreComponent
from ..regions import REGIONS, Region
from .components import REGISTRY, ComponentResult, missing
from .conclusions import build_conclusion
from .config import HORIZONS, label_for, load_config
from .data import Context, DataStore

log = logging.getLogger("scoring")


@dataclass
class ComponentRow:
    component: str
    label: str
    raw_display: str
    signal: float | None
    weight: float
    contribution: float
    detail: str


@dataclass
class HorizonScore:
    horizon: str
    score: float
    label: str
    coverage: float
    components: list[ComponentRow] = field(default_factory=list)


def _fit(text: str, limit: int) -> str:
    """Trim to the column width, dropping trailing clauses rather than cutting mid-number."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    for sep in ("; ", ", "):
        if sep in cut[limit // 2:]:
            cut = cut[: cut.rindex(sep)]
            break
    return cut.rstrip(" ,;") + "…"


def score_horizon(ctx: Context, region: Region, horizon: str) -> HorizonScore:
    """Compute one region x horizon score with its full component breakdown."""
    specs: dict[str, dict] = (ctx.cfg["horizons"].get(horizon) or {}).get("components") or {}
    evaluated: list[tuple[str, dict, ComponentResult, float]] = []
    for name, spec in specs.items():
        base_weight = float(spec.get("weight", 0.0))
        if base_weight <= 0:
            continue
        kind = spec.get("kind", name)
        fn = REGISTRY.get(kind)
        if fn is None:
            raise ValueError(f"scoring.yaml: unknown component kind '{kind}' ({horizon}.{name})")
        try:
            result = fn(ctx, region, spec)
        except Exception as exc:  # noqa: BLE001 - one broken input must not kill the run
            log.exception("component %s.%s failed for %s on %s", horizon, name, region.code, ctx.as_of)
            result = missing(f"Component error: {type(exc).__name__}: {exc}")
        if not result.applicable:
            continue
        if result.signal is not None and result.signal != result.signal:  # NaN guard
            result = missing(weight_mult=result.weight_mult)
        evaluated.append((name, spec, result, base_weight * result.weight_mult))

    total_w = sum(w for *_, w in evaluated)
    avail_w = sum(w for _, _, r, w in evaluated if r.signal is not None)
    raw_coverage = avail_w / total_w if total_w > 0 else 0.0
    # Thin evidence: pull the score towards neutral in proportion to the missing
    # weight, so one surviving component cannot print a "Strong" reading alone.
    shrink_below = float(ctx.general.get("coverage_shrink_below", 0.0) or 0.0)
    shrink = raw_coverage / shrink_below if 0 < raw_coverage < shrink_below else 1.0
    rows: list[ComponentRow] = []
    for name, spec, result, weight in evaluated:
        signal = None if result.signal is None else max(-1.0, min(1.0, float(result.signal)))
        contribution = 0.0 if signal is None or avail_w <= 0 else round(100.0 * shrink * weight / avail_w * signal, 3)
        rows.append(ComponentRow(
            component=name[:48],
            label=str(spec.get("label", name))[:128],
            raw_display=_fit(result.raw or "", 96),
            signal=None if signal is None else round(signal, 4),
            weight=round(weight, 4),
            contribution=contribution,
            detail=_fit(result.detail or "", 512),
        ))
    score = round(sum(r.contribution for r in rows), 3)
    score = max(-100.0, min(100.0, score))
    coverage = round(raw_coverage, 4)
    labels = ctx.cfg["labels"]
    label = label_for(score, labels) if avail_w > 0 else str(labels.get("no_data", "No Data"))
    return HorizonScore(horizon, score, label, coverage, rows)


def score_region(ctx: Context, region: Region) -> dict[str, HorizonScore]:
    return {h: score_horizon(ctx, region, h) for h in HORIZONS if h in ctx.cfg["horizons"]}


# ---------------------------------------------------------------- persistence
def _rows_for_date(ctx: Context, with_detail: bool) -> tuple[list[dict], list[dict], list[dict]]:
    scores, comps, concl = [], [], []
    for region in REGIONS:
        if not region.scored:
            continue
        result = score_region(ctx, region)
        for h, hs in result.items():
            scores.append({"region": region.code, "as_of": ctx.as_of, "horizon": h, "score": hs.score,
                           "label": hs.label, "coverage": hs.coverage})
            if with_detail:
                for c in hs.components:
                    comps.append({"region": region.code, "as_of": ctx.as_of, "horizon": h,
                                  "component": c.component, "label": c.label, "raw_display": c.raw_display,
                                  "signal": c.signal, "weight": c.weight, "contribution": c.contribution,
                                  "detail": c.detail})
        if with_detail:
            headline, text = build_conclusion(region, result, ctx.cfg)
            concl.append({"region": region.code, "as_of": ctx.as_of, "headline": headline[:256], "text": text})
    return scores, comps, concl


def _persist(session: Session, scores: list[dict], comps: list[dict], concl: list[dict], detail_dates: list[date]) -> None:
    # Drop old component rows for the dates being rewritten so components that
    # were removed from the yaml do not linger.
    for i in range(0, len(detail_dates), 200):
        session.execute(delete(ScoreComponent).where(ScoreComponent.as_of.in_(detail_dates[i:i + 200])))
    upsert(session, Score, scores)
    upsert(session, ScoreComponent, comps)
    upsert(session, Conclusion, concl)
    session.flush()


def run(session: Session, as_of: date | None = None, config: Any = None) -> int:
    """Score every region for `as_of` (default today). Returns Score rows written."""
    cfg = load_config(config)
    as_of = as_of or date.today()
    store = DataStore.load(session, cfg)
    scores, comps, concl = _rows_for_date(Context(store, cfg, as_of), with_detail=True)
    _persist(session, scores, comps, concl, [as_of])
    return len(scores)


def backfill(session: Session, start: date, end: date, step_days: int = 7, config: Any = None) -> int:
    """Score every region on start, start+step, ... <= end (plus `end` itself).

    Publication lags are applied to historical dates (see Context). Component
    rows and conclusions are kept for every date or only the last one,
    depending on backfill.store_detail in scoring.yaml.
    """
    if step_days < 1:
        raise ValueError("step_days must be >= 1")
    cfg = load_config(config)
    dates: list[date] = []
    d = start
    while d <= end:
        dates.append(d)
        d += timedelta(days=step_days)
    if end >= start and dates[-1] != end:
        dates.append(end)
    if not dates:
        return 0
    keep_all = str((cfg.get("backfill") or {}).get("store_detail", "all")).lower() == "all"
    store = DataStore.load(session, cfg)
    scores, comps, concl, detail_dates = [], [], [], []
    for d in dates:
        with_detail = keep_all or d == dates[-1]
        s, c, k = _rows_for_date(Context(store, cfg, d), with_detail)
        scores += s
        comps += c
        concl += k
        if with_detail:
            detail_dates.append(d)
    _persist(session, scores, comps, concl, detail_dates)
    return len(scores)
