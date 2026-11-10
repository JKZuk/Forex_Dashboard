"""FRED ingest: US macro releases, yields and the global risk gauges.

Works in two modes:
  * with FRED_API_KEY: the official API (api.stlouisfed.org/fred/series/observations)
  * without a key:     the public CSV download (fred.stlouisfed.org/graph/fredgraph.csv)

The mapping lives in _fred_series.py. Monthly / quarterly series are re-fetched in
full on every run (they are tiny and get revised); daily / weekly series are fetched
incrementally from the last stored date minus a short revision window.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from . import IngestResult
from . import _macro_util as mu
from ._fred_series import FRED_SERIES, FredSeries

SOURCE = "fred"
API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_fred_csv(text: str) -> mu.Points:
    """Parse fredgraph.csv: header `observation_date,<ID>`, missing values are '.' or ''."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2 or "date" not in header[0].lower():
        raise ValueError(f"unexpected FRED CSV header: {header!r}")
    return mu.clean((mu.parse_period(row[0]), mu.parse_float(row[1])) for row in reader if len(row) >= 2)


def parse_fred_json(payload: dict) -> mu.Points:
    """Parse the API's observations payload; missing values are '.'."""
    if "observations" not in payload:
        raise ValueError(str(payload.get("error_message", "no observations in FRED response")))
    return mu.clean(
        (mu.parse_period(obs.get("date", "")), mu.parse_float(obs.get("value")))
        for obs in payload["observations"]
    )


def fetch_observations(fred_id: str, start: date, api_key: str = "") -> mu.Points:
    if api_key:
        resp = mu.http_get(
            API_URL,
            params={
                "series_id": fred_id,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start.isoformat(),
            },
        )
        return parse_fred_json(resp.json())
    resp = mu.http_get(CSV_URL, params={"id": fred_id, "cosd": start.isoformat()}, timeout=40)
    return parse_fred_csv(resp.text)


def apply_transform(spec: FredSeries, raw: mu.Points) -> mu.Points:
    if spec.transform == "yoy":
        points = mu.yoy_pct(raw)
    elif spec.transform == "diff":
        points = mu.diff(raw)
    elif spec.transform == "level":
        points = raw
    else:
        raise ValueError(f"unknown transform {spec.transform!r}")
    return mu.scale(points, spec.scale) if spec.scale != 1.0 else points


def fetch_start(spec: FredSeries, last_stored: date | None) -> date:
    """Incremental window: only untransformed daily / weekly series are fetched partially."""
    if last_stored and spec.frequency in ("D", "W") and spec.transform == "level":
        return max(mu.FETCH_START, last_stored - timedelta(days=mu.DAILY_REVISION_DAYS))
    return mu.FETCH_START


def run(session: Session) -> IngestResult:
    out = mu.RunLog()
    api_key = settings.fred_api_key
    for spec in FRED_SERIES:
        item = mu.Fetched(
            region=spec.region,
            indicator=spec.indicator,
            points=[],
            frequency=spec.frequency,
            source=SOURCE,
            source_code=spec.fred_id,
            note=spec.note,
            unit=spec.unit,
        )
        try:
            start = fetch_start(spec, mu.last_stored_date(session, item.series_id))
            item.points = apply_transform(spec, fetch_observations(spec.fred_id, start, api_key))
            out.save(session, item)
        except Exception as exc:  # noqa: BLE001 - one bad series must not fail the run
            out.fail(f"{item.series_id} ({spec.fred_id})", exc)
    if out.saved == 0:
        raise RuntimeError(f"FRED returned no data: {out.message()}")
    return IngestResult(rows=out.rows, message=out.message())
