"""FX rate providers.

A provider returns daily rates normalised to **USD value of one unit of the
currency** (EUR -> ~1.1, JPY -> ~0.0065), so "up" always means that currency
strengthened against the dollar.

To add a provider (yfinance, Twelve Data, a paid feed, ...): implement the
`FxProvider` protocol and add an instance to `PROVIDERS` (tried in order; the
first one that returns data wins).
"""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Mapping, Protocol, Sequence

from . import _fx_http

# currency -> {date -> USD per one unit of currency}
RateTable = dict[str, dict[date, float]]


class FxProvider(Protocol):
    name: str

    def fetch(self, currencies: Sequence[str], start: date, end: date) -> RateTable:
        """Daily rates for `currencies` (never USD) between start and end, inclusive."""
        ...


def _clip(table: RateTable, start: date, end: date) -> RateTable:
    return {c: {d: v for d, v in pts.items() if start <= d <= end} for c, pts in table.items()}


# --------------------------------------------------------------------------- Frankfurter
def parse_frankfurter(payload: Mapping[str, Any]) -> RateTable:
    """Parse a Frankfurter v1 time-series response requested with base=USD.

    The API quotes units of currency per 1 USD; we invert to USD per unit.
    """
    if str(payload.get("base", "")).upper() != "USD":
        raise ValueError(f"expected base=USD, got {payload.get('base')!r}")
    amount = float(payload.get("amount", 1.0) or 1.0)
    out: RateTable = {}
    for day, quotes in (payload.get("rates") or {}).items():
        d = date.fromisoformat(day)
        for ccy, per_usd in quotes.items():
            if per_usd is None or float(per_usd) <= 0:
                continue
            out.setdefault(ccy.upper(), {})[d] = amount / float(per_usd)
    return out


class FrankfurterProvider:
    """ECB reference rates via frankfurter.dev (free, keyless, whole range in one call)."""

    name = "frankfurter"
    base_url = "https://api.frankfurter.dev/v1"

    def fetch(self, currencies: Sequence[str], start: date, end: date) -> RateTable:
        url = f"{self.base_url}/{start.isoformat()}..{end.isoformat()}"
        resp = _fx_http.get(url, params={"base": "USD", "symbols": ",".join(currencies)})
        # The API snaps the start back to the previous business day; clip it off.
        return _clip(parse_frankfurter(resp.json()), start, end)


# --------------------------------------------------------------------------- ECB direct
def parse_ecb_csv(text: str) -> RateTable:
    """Parse ECB Data Portal `csvdata` for EXR/D.<ccys>.EUR.SP00.A.

    ECB quotes units of currency per 1 EUR. Convert through the USD row:
    EURUSD = usd_per_eur; XXXUSD = usd_per_eur / xxx_per_eur.
    Dates where the USD fix is missing are dropped.
    """
    per_eur: dict[str, dict[date, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get("OBS_VALUE") or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value <= 0:
            continue
        per_eur.setdefault(row["CURRENCY"].upper(), {})[date.fromisoformat(row["TIME_PERIOD"])] = value
    usd = per_eur.pop("USD", {})
    out: RateTable = {"EUR": dict(usd)} if usd else {}
    for ccy, pts in per_eur.items():
        out[ccy] = {d: usd[d] / v for d, v in pts.items() if d in usd}
    return out


class EcbProvider:
    """Fallback: the same ECB reference rates straight from the ECB Data Portal."""

    name = "ecb"
    base_url = "https://data-api.ecb.europa.eu/service/data/EXR"

    def fetch(self, currencies: Sequence[str], start: date, end: date) -> RateTable:
        keys = ["USD"] + [c for c in currencies if c not in ("USD", "EUR")]
        url = f"{self.base_url}/D.{'+'.join(keys)}.EUR.SP00.A"
        resp = _fx_http.get(
            url,
            params={"startPeriod": start.isoformat(), "endPeriod": end.isoformat(), "format": "csvdata"},
            accept="text/csv",
        )
        table = _clip(parse_ecb_csv(resp.text), start, end)
        return {c: pts for c, pts in table.items() if c in currencies}


PROVIDERS: list[FxProvider] = [FrankfurterProvider(), EcbProvider()]
