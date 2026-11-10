"""Shared helpers for the macro ingest modules (macro_fred, macro_official).

Conventions used by every macro series:
  * obs_date of a monthly / quarterly observation is the FIRST day of the
    reference period (2026-09 -> 2026-09-01, 2026-Q3 -> 2026-07-01). It is the
    reference period, not the release date.
  * History is kept from HISTORY_START onward.
"""
from __future__ import annotations

import bisect
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..catalog import GLOBAL_SERIES, INDICATOR_BY_KEY
from ..models import Observation
from ..regions import series_id as make_series_id
from . import save_series

log = logging.getLogger("ingest.macro")

HISTORY_START = date(2010, 1, 1)
# Fetch one extra year so year-over-year transforms are defined from HISTORY_START.
FETCH_START = date(2009, 1, 1)
# Daily sources are re-fetched from (last stored date - this window) on re-runs.
DAILY_REVISION_DAYS = 45

POLITE_SLEEP_SECONDS = 0.4
_RETRY_STATUS = {429, 500, 502, 503, 504}

# A series whose newest observation is older than this is reported as stale.
STALE_AFTER_DAYS = {"D": 14, "W": 28, "M": 125, "Q": 290, "A": 800}

Points = list[tuple[date, float]]

_session: requests.Session | None = None


def _http() -> requests.Session:
    # The default python-requests User-Agent is deliberately kept: it is generic,
    # carries no personal information, and FRED's edge drops unknown custom agents.
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def http_get(
    url: str,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 60,
    retries: int = 3,
) -> requests.Response:
    """GET with a small retry/backoff on transient failures; raises on final failure."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2.0 * 2 ** (attempt - 1))
        try:
            resp = _http().get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            continue
        finally:
            time.sleep(POLITE_SLEEP_SECONDS)
        if resp.status_code in _RETRY_STATUS:
            last_exc = requests.HTTPError(f"HTTP {resp.status_code} for {resp.url.split('?')[0]}")
            continue
        resp.raise_for_status()
        return resp
    assert last_exc is not None
    raise last_exc


# --------------------------------------------------------------------------- parsing


def parse_period(text: str) -> date | None:
    """'2026-09-14' / '2026-09' / '2026-Q3' / '2026' -> first day of the period."""
    text = text.strip()
    try:
        if "-Q" in text:
            year, quarter = text.split("-Q")
            return date(int(year), 3 * int(quarter) - 2, 1)
        parts = [int(p) for p in text.split("-")]
        if len(parts) == 3:
            return date(parts[0], parts[1], parts[2])
        if len(parts) == 2:
            return date(parts[0], parts[1], 1)
        if len(parts) == 1:
            return date(parts[0], 1, 1)
    except ValueError:
        pass
    return None


def parse_float(text: object) -> float | None:
    """Lenient numeric parse; '', '.', '-', 'NA', ':' and friends -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        try:
            value = float(str(text).strip().replace(",", ""))
        except ValueError:
            return None
    return value if value == value else None  # drop NaN


# ------------------------------------------------------------------------ transforms


def clean(points: Iterable[tuple[date, float | None]]) -> Points:
    """Drop missing values, de-duplicate dates (last wins) and sort."""
    by_date = {d: float(v) for d, v in points if d is not None and v is not None and v == v}
    return sorted(by_date.items())


def _year_ago_value(by_date: dict[date, float], dates: list[date], target: date, max_gap_days: int) -> float | None:
    """Value at `target`, or a log-linear interpolation between its neighbours.

    Statistical agencies occasionally skip a period (the US published no October
    2025 CPI); interpolating the base keeps the y/y series unbroken a year later.
    """
    if target in by_date:
        return by_date[target]
    i = bisect.bisect_left(dates, target)
    if i == 0 or i == len(dates):
        return None
    before, after = dates[i - 1], dates[i]
    if (target - before).days > max_gap_days or (after - target).days > max_gap_days:
        return None
    lo, hi = by_date[before], by_date[after]
    if lo <= 0 or hi <= 0:
        return None
    weight = (target - before).days / (after - before).days
    return lo * (hi / lo) ** weight


def yoy_pct(points: Points, max_gap_days: int = 35) -> Points:
    """Percent change versus the same calendar date one year earlier.

    A missing year-ago observation is interpolated when both neighbours lie within
    `max_gap_days` of it (i.e. a single missing month); otherwise the point is dropped.
    """
    by_date = dict(points)
    dates = sorted(by_date)
    out: Points = []
    for d, v in points:
        try:
            target = d.replace(year=d.year - 1)
        except ValueError:  # 29 February
            continue
        prev = _year_ago_value(by_date, dates, target, max_gap_days)
        if prev:
            out.append((d, (v / prev - 1.0) * 100.0))
    return out


def backfill(primary: Points, older: Points) -> Points:
    """`primary`, preceded by the part of `older` that predates its first observation."""
    if not primary:
        return older
    first = primary[0][0]
    return [(d, v) for d, v in older if d < first] + primary


def diff(points: Points, max_gap_days: int = 45) -> Points:
    """Change versus the previous observation (skipped across gaps in the data)."""
    out: Points = []
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if (d1 - d0).days <= max_gap_days:
            out.append((d1, v1 - v0))
    return out


def scale(points: Points, factor: float) -> Points:
    return [(d, v * factor) for d, v in points]


def subtract(a: Points, b: Points) -> Points:
    """a - b on the dates both series have."""
    other = dict(b)
    return [(d, v - other[d]) for d, v in a if d in other]


def since(points: Points, start: date = HISTORY_START) -> Points:
    return [(d, v) for d, v in points if d >= start]


# --------------------------------------------------------------------------- storage


@dataclass
class Fetched:
    """One ready-to-store series produced by a provider."""

    region: str
    indicator: str  # catalog key, or the suffix of a GLOBAL.* id
    points: Points
    frequency: str  # D / W / M / Q
    source: str
    source_code: str
    note: str = ""
    unit: str | None = None  # None -> catalog unit

    @property
    def series_id(self) -> str:
        return make_series_id(self.region, self.indicator)


def last_stored_date(session: Session, sid: str) -> date | None:
    return session.execute(
        select(func.max(Observation.obs_date)).where(Observation.series_id == sid)
    ).scalar()


def store(session: Session, item: Fetched) -> int:
    """Upsert a fetched series using the catalog's name / category / unit."""
    sid = item.series_id
    if item.region == "GLOBAL":
        name, category, unit = GLOBAL_SERIES.get(sid, sid), "global", item.unit or ""
    else:
        ind = INDICATOR_BY_KEY[item.indicator]
        name, category, unit = ind.name, ind.category, item.unit or ind.unit
    return save_series(
        session,
        series_id=sid,
        region=item.region,
        indicator=item.indicator,
        name=name,
        category=category,
        points=since(item.points),
        unit=unit,
        frequency=item.frequency,
        source=item.source,
        source_code=item.source_code[:160],
        note=item.note[:255],
    )


def is_stale(frequency: str, last: date, today: date | None = None) -> bool:
    limit = STALE_AFTER_DAYS.get(frequency, 125)
    return ((today or date.today()) - last) > timedelta(days=limit)


@dataclass
class RunLog:
    """Collects per-series outcomes into one IngestResult message."""

    rows: int = 0
    saved: int = 0
    failures: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    def save(self, session: Session, item: Fetched) -> None:
        points = since(item.points)
        if not points:
            self.failures.append(f"{item.series_id}: no data ({item.source_code})")
            return
        self.rows += store(session, item)
        self.saved += 1
        newest = points[-1][0]
        if is_stale(item.frequency, newest):
            self.stale.append(f"{item.series_id}@{newest.isoformat()}")

    def fail(self, what: str, exc: Exception) -> None:
        log.warning("macro ingest: %s failed: %s", what, exc)
        self.failures.append(f"{what}: {type(exc).__name__}: {str(exc)[:120]}")

    def message(self) -> str:
        parts = [f"{self.saved} series saved"]
        if self.failures:
            parts.append(f"{len(self.failures)} failed: " + "; ".join(self.failures))
        if self.stale:
            parts.append("stale: " + ", ".join(self.stale))
        return " | ".join(parts)
