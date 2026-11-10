"""Shared query helpers for the API routers.

Everything here is written to avoid N+1 access: "latest per key" is always one
grouped / windowed query, never a loop of per-key queries.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Iterator, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .. import regions as reg
from ..db import SessionLocal
from ..models import Conclusion, Observation, Score, SeriesMeta
from .schemas import HorizonScore, PriceSnapshot, RegionOut, SeriesOut, SeriesPoint

HORIZONS: list[str] = ["short", "medium", "long"]
NO_DATA_LABEL = "No Data"  # scoring engine's label for score 0 / coverage 0


def is_no_data(label: str, coverage: float) -> bool:
    return (label or "").strip().lower() == NO_DATA_LABEL.lower() or coverage <= 0


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- regions -----------------------------------------------------------------
def region_out(r: reg.Region) -> RegionOut:
    return RegionOut(
        code=r.code,
        name=r.name,
        currency=r.currency,
        group=r.group,
        core=r.core,
        trade_sensitive=r.trade_sensitive,
        scored=r.scored,
        fx_series_id=reg.fx_series_id(r.currency),
    )


def get_region(code: str) -> reg.Region:
    region = reg.REGION_BY_CODE.get(code.upper()) or reg.REGION_BY_CURRENCY.get(code.upper())
    if region is None:
        raise HTTPException(status_code=404, detail=f"Unknown region: {code}")
    return region


def fx_name(currency: str) -> str:
    return "USD index" if currency == "USD" else f"{currency}/USD"


def parse_csv(value: Optional[str]) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def parse_horizons(value: Optional[str]) -> list[str]:
    wanted = [h.lower() for h in parse_csv(value)] or HORIZONS
    bad = [h for h in wanted if h not in HORIZONS]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown horizon(s): {', '.join(bad)}")
    return [h for h in HORIZONS if h in wanted]


# --- scores ------------------------------------------------------------------
def latest_scores(
    session: Session, region_codes: Optional[Sequence[str]] = None
) -> dict[str, dict[str, HorizonScore]]:
    """Latest score per (region, horizon) plus its change vs ~one week earlier.

    Two queries total: the per-region latest date, then a three-week window of
    rows from which both the current and the week-ago values are picked.
    """
    latest_q = select(Score.region, func.max(Score.as_of)).group_by(Score.region)
    if region_codes is not None:
        latest_q = latest_q.where(Score.region.in_(list(region_codes)))
    latest = {r: d for r, d in session.execute(latest_q) if d is not None}
    if not latest:
        return {}

    floor = min(latest.values()) - timedelta(days=21)
    rows = session.execute(
        select(Score).where(Score.region.in_(list(latest)), Score.as_of >= floor)
    ).scalars()
    by_key: dict[tuple[str, str], list[Score]] = defaultdict(list)
    for row in rows:
        by_key[(row.region, row.horizon)].append(row)

    out: dict[str, dict[str, HorizonScore]] = defaultdict(dict)
    for (region, horizon), items in by_key.items():
        current = next((s for s in items if s.as_of == latest[region]), None)
        if current is None:
            continue
        target = current.as_of - timedelta(days=7)
        # Weekly history plus optional daily rows: take the row nearest to 7 days back.
        candidates = [
            s for s in items if timedelta(days=4) <= current.as_of - s.as_of <= timedelta(days=14)
        ]
        prev = min(candidates, key=lambda s: abs((s.as_of - target).days)) if candidates else None
        no_data = is_no_data(current.label, current.coverage)
        if no_data or (prev is not None and is_no_data(prev.label, prev.coverage)):
            prev = None  # a change against / from "no data" is meaningless
        out[region][horizon] = HorizonScore(
            horizon=horizon,
            score=round(current.score, 1),
            label=current.label,
            coverage=round(current.coverage, 3),
            no_data=no_data,
            as_of=current.as_of,
            change_1w=round(current.score - prev.score, 1) if prev else None,
            previous_as_of=prev.as_of if prev else None,
        )
    return dict(out)


def latest_headlines(session: Session) -> dict[str, Conclusion]:
    sub = (
        select(Conclusion.region, func.max(Conclusion.as_of).label("as_of"))
        .group_by(Conclusion.region)
        .subquery()
    )
    rows = session.execute(
        select(Conclusion).join(
            sub, and_(Conclusion.region == sub.c.region, Conclusion.as_of == sub.c.as_of)
        )
    ).scalars()
    return {c.region: c for c in rows}


# --- observations ------------------------------------------------------------
def _pct(now: float, then: Optional[float]) -> Optional[float]:
    if then is None or then == 0:
        return None
    return round((now / then - 1.0) * 100.0, 3)


def recent_window(
    session: Session, series_ids: Sequence[str], days: int = 50
) -> dict[str, list[tuple[date, float]]]:
    """Last `days` calendar days of each series (relative to its own latest date)."""
    if not series_ids:
        return {}
    sub = (
        select(Observation.series_id, func.max(Observation.obs_date).label("last"))
        .where(Observation.series_id.in_(list(series_ids)))
        .group_by(Observation.series_id)
    )
    last = {sid: d for sid, d in session.execute(sub) if d is not None}
    if not last:
        return {}
    floor = min(last.values()) - timedelta(days=days)
    rows = session.execute(
        select(Observation.series_id, Observation.obs_date, Observation.value)
        .where(Observation.series_id.in_(list(last)), Observation.obs_date >= floor)
        .order_by(Observation.series_id, Observation.obs_date)
    )
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for sid, d, v in rows:
        if d >= last[sid] - timedelta(days=days):
            out[sid].append((d, v))
    return dict(out)


def changes(points: list[tuple[date, float]]) -> dict[str, Optional[float]]:
    """1d / 1w / 1m percentage change of the last point of an ascending series."""
    if not points:
        return {"d1": None, "w1": None, "m1": None}
    dates = [p[0] for p in points]
    last_date, last_value = points[-1]

    def at_or_before(d: date) -> Optional[float]:
        i = bisect_right(dates, d)
        return points[i - 1][1] if i else None

    return {
        "d1": _pct(last_value, points[-2][1]) if len(points) > 1 else None,
        "w1": _pct(last_value, at_or_before(last_date - timedelta(days=7))),
        "m1": _pct(last_value, at_or_before(last_date - timedelta(days=30))),
    }


def price_snapshots(session: Session, regions: Iterable[reg.Region]) -> dict[str, PriceSnapshot]:
    """Latest FX price and 1d/1w/1m % change per region code (one windowed fetch)."""
    by_series = {reg.fx_series_id(r.currency): r for r in regions}
    window = recent_window(session, list(by_series))
    out: dict[str, PriceSnapshot] = {}
    for sid, points in window.items():
        region = by_series[sid]
        ch = changes(points)
        out[region.code] = PriceSnapshot(
            series_id=sid,
            name=fx_name(region.currency),
            date=points[-1][0],
            value=points[-1][1],
            change_1d_pct=ch["d1"],
            change_1w_pct=ch["w1"],
            change_1m_pct=ch["m1"],
        )
    return out


def last_n_per_series(
    session: Session, series_ids: Sequence[str], n: int
) -> dict[str, list[tuple[date, float]]]:
    """Most recent `n` observations of each series, oldest first (one window query)."""
    if not series_ids:
        return {}
    rn = (
        func.row_number()
        .over(partition_by=Observation.series_id, order_by=Observation.obs_date.desc())
        .label("rn")
    )
    sub = (
        select(Observation.series_id, Observation.obs_date, Observation.value, rn)
        .where(Observation.series_id.in_(list(series_ids)))
        .subquery()
    )
    rows = session.execute(
        select(sub.c.series_id, sub.c.obs_date, sub.c.value)
        .where(sub.c.rn <= n)
        .order_by(sub.c.series_id, sub.c.obs_date)
    )
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for sid, d, v in rows:
        out[sid].append((d, v))
    return dict(out)


def load_series(
    session: Session,
    series_ids: Sequence[str],
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, SeriesOut]:
    """Metadata + points for several series in two queries. Absent ids -> found=False."""
    out = {sid: SeriesOut(series_id=sid, found=False) for sid in series_ids}
    if not series_ids:
        return out
    metas = session.execute(
        select(SeriesMeta).where(SeriesMeta.series_id.in_(list(series_ids)))
    ).scalars()
    for m in metas:
        out[m.series_id] = SeriesOut(
            series_id=m.series_id,
            found=True,
            region=m.region,
            indicator=m.indicator,
            name=m.name,
            category=m.category,
            unit=m.unit or "",
            frequency=m.frequency or "",
            source=m.source or "",
            note=m.note or "",
            last_updated=m.last_updated,
        )
    q = select(Observation.series_id, Observation.obs_date, Observation.value).where(
        Observation.series_id.in_(list(series_ids))
    )
    if start:
        q = q.where(Observation.obs_date >= start)
    if end:
        q = q.where(Observation.obs_date <= end)
    for sid, d, v in session.execute(q.order_by(Observation.series_id, Observation.obs_date)):
        item = out[sid]
        item.found = True  # observations without a metadata row still count
        item.points.append(SeriesPoint(date=d, value=v))
    return out
