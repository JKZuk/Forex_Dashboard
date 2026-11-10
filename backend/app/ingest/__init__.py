"""Data ingestion layer.

Each source is a module in this package exposing

    def run(session: Session) -> IngestResult

It fetches from one free provider and upserts into the tables in app.models.
To add a source (free or paid): create the module and add its name to SOURCES.
Sources must be idempotent, incremental where practical, and must raise only
when the whole source failed (partial gaps go in IngestResult.message).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from sqlalchemy.orm import Session

from ..db import upsert
from ..models import Observation, SeriesMeta

# Run order of the refresh pipeline. Module name == job name in refresh_runs.
SOURCES: list[str] = [
    "macro_fred",      # macro releases, yields, risk gauges (FRED)
    "macro_official",  # gaps FRED cannot fill: official statistics / central bank APIs
    "fx_prices",       # daily FX rates + USD index
    "cot",             # CFTC Commitments of Traders
    "news",            # RSS feeds + local sentiment
    "calendar",        # upcoming high-impact events
]


@dataclass
class IngestResult:
    rows: int = 0
    message: str = ""


def save_series(
    session: Session,
    *,
    series_id: str,
    region: str,
    indicator: str,
    name: str,
    category: str,
    points: Iterable[tuple[date, float]],
    unit: str = "",
    frequency: str = "",
    source: str = "",
    source_code: str = "",
    note: str = "",
) -> int:
    """Upsert one series' metadata and observations. Returns observation count."""
    rows = [
        {"series_id": series_id, "obs_date": d, "value": float(v)}
        for d, v in points
        if v is not None and v == v  # drop None / NaN
    ]
    upsert(
        session,
        SeriesMeta,
        [
            {
                "series_id": series_id,
                "region": region,
                "indicator": indicator,
                "name": name,
                "category": category,
                "unit": unit,
                "frequency": frequency,
                "source": source,
                "source_code": source_code,
                "note": note,
                "last_updated": datetime.utcnow(),
            }
        ],
    )
    return upsert(session, Observation, rows)
