"""CFTC Commitments of Traders: speculative positioning in currency futures.

Legacy futures-only report, non-commercial longs/shorts and open interest, from
the CFTC Public Reporting (Socrata) API. Contracts are matched by CFTC contract
market code. All contracts are quoted currency-vs-USD, so a positive net means
speculators are long that currency against the dollar; for the ICE dollar index
(stored as "USD") positive = long USD. See docs/sources/market.md.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import upsert
from ..models import CotPosition
from . import IngestResult, _fx_http

log = logging.getLogger("ingest.cot")

# Legacy report, futures only.
DATASET_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
HISTORY_START = date(2015, 1, 1)
OVERLAP_DAYS = 28  # re-fetch recent weeks to pick up CFTC corrections
PAGE_SIZE = 10000
STALE_AFTER_DAYS = 14

# CFTC contract market code -> currency. Verified against live data.
CONTRACTS: dict[str, str] = {
    "099741": "EUR",  # EURO FX - CME
    "097741": "JPY",  # JAPANESE YEN - CME
    "096742": "GBP",  # BRITISH POUND - CME
    "232741": "AUD",  # AUSTRALIAN DOLLAR - CME
    "090741": "CAD",  # CANADIAN DOLLAR - CME
    "112741": "NZD",  # NZ DOLLAR - CME
    "092741": "CHF",  # SWISS FRANC - CME
    "098662": "USD",  # USD INDEX - ICE FUTURES U.S.
}

_FIELDS = (
    "cftc_contract_market_code",
    "report_date_as_yyyy_mm_dd",
    "noncomm_positions_long_all",
    "noncomm_positions_short_all",
    "open_interest_all",
)


def parse_rows(
    records: Iterable[Mapping[str, Any]], contracts: Mapping[str, str] = CONTRACTS
) -> list[dict]:
    """Socrata records -> CotPosition row dicts. Unknown codes and bad rows are skipped."""
    out: dict[tuple[str, date], dict] = {}
    for rec in records:
        currency = contracts.get(str(rec.get("cftc_contract_market_code", "")).strip())
        if currency is None:
            continue
        try:
            report_date = date.fromisoformat(str(rec["report_date_as_yyyy_mm_dd"])[:10])
            long_ = float(rec["noncomm_positions_long_all"])
            short = float(rec["noncomm_positions_short_all"])
            oi = float(rec["open_interest_all"])
        except (KeyError, TypeError, ValueError):
            continue
        if oi <= 0:
            continue
        net = long_ - short
        out[(currency, report_date)] = {
            "currency": currency,
            "report_date": report_date,
            "noncomm_long": long_,
            "noncomm_short": short,
            "noncomm_net": net,
            "open_interest": oi,
            "net_pct_oi": net / oi * 100.0,
        }
    return [out[k] for k in sorted(out)]


def fetch_start(last_dates: Mapping[str, date], currencies: Sequence[str]) -> date:
    """Full history if any currency is empty, else the oldest last date minus an overlap."""
    if any(c not in last_dates for c in currencies):
        return HISTORY_START
    return max(HISTORY_START, min(last_dates[c] for c in currencies) - timedelta(days=OVERLAP_DAYS))


def fetch_records(start: date, codes: Sequence[str] = tuple(CONTRACTS)) -> list[dict]:
    """All legacy futures-only rows for `codes` with report date >= start (paged)."""
    code_list = ",".join(f"'{c}'" for c in codes)
    where = (
        f"cftc_contract_market_code in ({code_list}) "
        f"AND report_date_as_yyyy_mm_dd >= '{start.isoformat()}T00:00:00'"
    )
    records: list[dict] = []
    offset = 0
    while True:
        page = _fx_http.get(
            DATASET_URL,
            params={
                "$select": ",".join(_FIELDS),
                "$where": where,
                "$order": "report_date_as_yyyy_mm_dd,cftc_contract_market_code",
                "$limit": PAGE_SIZE,
                "$offset": offset,
            },
        ).json()
        if not isinstance(page, list):
            raise ValueError(f"unexpected CFTC response: {str(page)[:200]}")
        records.extend(page)
        if len(page) < PAGE_SIZE:
            return records
        offset += PAGE_SIZE


def _last_dates(session: Session) -> dict[str, date]:
    rows = session.execute(
        select(CotPosition.currency, func.max(CotPosition.report_date)).group_by(CotPosition.currency)
    ).all()
    return {c: d for c, d in rows if d is not None}


def run(session: Session, today: date | None = None) -> IngestResult:
    today = today or date.today()
    currencies = list(CONTRACTS.values())
    start = fetch_start(_last_dates(session), currencies)

    rows = parse_rows(fetch_records(start))
    if not rows and start == HISTORY_START:
        raise RuntimeError("CFTC returned no COT rows")
    n = upsert(session, CotPosition, rows)

    session.flush()
    last = _last_dates(session)
    notes: list[str] = []
    missing = [c for c in currencies if c not in last]
    if missing:
        notes.append("no data for " + ",".join(missing))
    if last:
        latest = max(last.values())
        lagging = [c for c, d in last.items() if d < latest]
        if lagging:
            notes.append("behind latest report: " + ",".join(sorted(lagging)))
        if (today - latest).days > STALE_AFTER_DAYS:
            notes.append(f"STALE: latest report is {(today - latest).days} days old")
        msg = f"cftc legacy futures-only {start}..{latest}"
    else:
        msg = "cftc: no rows stored"
    if notes:
        msg += " | " + "; ".join(notes)
    return IngestResult(rows=n, message=msg)
