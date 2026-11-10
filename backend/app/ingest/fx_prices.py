"""Daily FX rates and a USD basket index.

Stores `FX.<CCY>USD` = USD value of one unit of the currency (rising = that
currency strengthening vs USD) and `FX.USD_INDEX` (rising = stronger dollar).

Source: ECB euro reference rates (one fix per TARGET business day, ~14:15 CET
concertation, published ~16:00 CET) via Frankfurter, falling back to the ECB
Data Portal. These are daily reference fixes, not intraday or closing prices.
See docs/sources/market.md.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Observation
from ..regions import FX_USD_INDEX, REGIONS, fx_series_id
from . import IngestResult, save_series
from ._fx_providers import PROVIDERS, FxProvider, RateTable

log = logging.getLogger("ingest.fx_prices")

HISTORY_START = date(2010, 1, 1)
OVERLAP_DAYS = 7  # re-fetch this many days before the last stored date

# Currencies of every region except the dollar itself.
CURRENCIES: list[str] = [r.currency for r in REGIONS if r.currency != "USD"]

# ICE U.S. Dollar Index (DXY) weights. SEK is fetched only as a basket component.
USD_INDEX_WEIGHTS: dict[str, float] = {
    "EUR": 0.576,
    "JPY": 0.136,
    "GBP": 0.119,
    "CAD": 0.091,
    "SEK": 0.042,
    "CHF": 0.036,
}
BASKET_ONLY: list[str] = [c for c in USD_INDEX_WEIGHTS if c not in CURRENCIES]
FETCH_CURRENCIES: list[str] = CURRENCIES + BASKET_ONLY


def compute_usd_index(
    rates: Mapping[str, Mapping[date, float]],
    weights: Mapping[str, float] = USD_INDEX_WEIGHTS,
) -> list[tuple[date, float]]:
    """Fixed-weight geometric dollar basket, 100 at the first common date.

    `rates[ccy][d]` is USD per one unit of ccy, so the dollar's value against
    each component is the inverse:  index_t = 100 * prod((p_t / p_0) ** -w).
    Only dates on which every component has a rate are used. Weights are
    normalised to sum to 1.
    """
    total = sum(weights.values())
    if total <= 0 or any(c not in rates for c in weights):
        return []
    common = sorted(set.intersection(*(set(rates[c]) for c in weights)))
    if not common:
        return []
    base = common[0]
    out: list[tuple[date, float]] = []
    for d in common:
        log_idx = sum(-(w / total) * math.log(rates[c][d] / rates[c][base]) for c, w in weights.items())
        out.append((d, 100.0 * math.exp(log_idx)))
    return out


def _last_dates(session: Session, series_ids: Sequence[str]) -> dict[str, date]:
    rows = session.execute(
        select(Observation.series_id, func.max(Observation.obs_date))
        .where(Observation.series_id.in_(series_ids))
        .group_by(Observation.series_id)
    ).all()
    return {sid: d for sid, d in rows if d is not None}


def fetch_start(last_dates: Mapping[str, date], series_ids: Sequence[str]) -> date:
    """Full history if any series is empty, else the oldest last date minus an overlap."""
    if any(sid not in last_dates for sid in series_ids):
        return HISTORY_START
    return max(HISTORY_START, min(last_dates[s] for s in series_ids) - timedelta(days=OVERLAP_DAYS))


def _fetch(
    providers: Sequence[FxProvider], currencies: Sequence[str], start: date, end: date
) -> tuple[RateTable, str, list[str]]:
    """Try providers in order; return (table, provider name, error notes)."""
    errors: list[str] = []
    for provider in providers:
        try:
            table = provider.fetch(currencies, start, end)
        except Exception as exc:  # noqa: BLE001 - fall through to the next provider
            log.warning("fx provider %s failed: %s", provider.name, exc)
            errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
            continue
        if any(table.get(c) for c in currencies):
            return table, provider.name, errors
        errors.append(f"{provider.name}: no data")
    return {}, "", errors


def _load_rates(session: Session, currencies: Sequence[str]) -> RateTable:
    by_sid = {f"FX.{c}USD": c for c in currencies}
    table: RateTable = {c: {} for c in currencies}
    rows = session.execute(
        select(Observation.series_id, Observation.obs_date, Observation.value).where(
            Observation.series_id.in_(list(by_sid))
        )
    )
    for sid, d, v in rows:
        table[by_sid[sid]][d] = v
    return table


def run(
    session: Session,
    providers: Sequence[FxProvider] | None = None,
    today: date | None = None,
) -> IngestResult:
    providers = PROVIDERS if providers is None else providers
    end = today or date.today()
    series_ids = [f"FX.{c}USD" for c in FETCH_CURRENCIES]
    start = fetch_start(_last_dates(session, series_ids), series_ids)

    table, source, errors = _fetch(providers, FETCH_CURRENCIES, start, end)
    if not table:
        raise RuntimeError("all FX providers failed: " + "; ".join(errors))

    rows = 0
    notes: list[str] = list(errors)
    latest: date | None = None
    for ccy in FETCH_CURRENCIES:
        points = sorted(table.get(ccy, {}).items())
        if not points:
            notes.append(f"{ccy}: no data from {source}")
            continue
        latest = max(latest, points[-1][0]) if latest else points[-1][0]
        basket_only = ccy in BASKET_ONLY
        rows += save_series(
            session,
            series_id=fx_series_id(ccy) if not basket_only else f"FX.{ccy}USD",
            region="FX",
            indicator=f"{ccy}USD",
            name=f"{ccy}/USD - US dollars per 1 {ccy}",
            category="fx",
            points=points,
            unit="USD",
            frequency="D",
            source=source,
            source_code=f"USD/{ccy} (inverted)",
            note=("ECB daily reference fix (not intraday). "
                  + ("Stored only as a USD index basket component." if basket_only else "")).strip(),
        )

    # Recompute the index from everything stored so the base date never drifts,
    # but only write the window we just refreshed.
    session.flush()
    index = compute_usd_index(_load_rates(session, list(USD_INDEX_WEIGHTS)))
    index_window = [(d, v) for d, v in index if d >= start]
    if index:
        weights = ", ".join(f"{c} {w:.1%}" for c, w in USD_INDEX_WEIGHTS.items())
        rows += save_series(
            session,
            series_id=FX_USD_INDEX,
            region="FX",
            indicator="USD_INDEX",
            name="US dollar basket index (DXY weights, higher = stronger USD)",
            category="fx",
            points=index_window,
            unit=f"index, {index[0][0].isoformat()}=100",
            frequency="D",
            source="computed",
            source_code="geometric basket of FX.*USD",
            note=f"Weights: {weights}. ECB daily fixes; not the official ICE DXY level."[:255],
        )
    else:
        notes.append("USD index not computed (missing basket component)")

    msg = f"{source} {start}..{latest}"
    if notes:
        msg += " | " + "; ".join(notes)
    return IngestResult(rows=rows, message=msg)
