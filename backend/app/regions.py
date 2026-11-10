"""Region registry. Add a region here and the rest of the system picks it up."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    code: str  # primary key used everywhere
    name: str
    currency: str
    group: str  # "major" | "commodity" | "influence" | "haven"
    core: bool  # core regions are built and shown first
    trade_sensitive: bool  # trade balance carries extra weight (JP + commodity bloc)
    scored: bool = True  # False = shown as an influence only


REGIONS: list[Region] = [
    Region("US", "United States", "USD", "major", True, False),
    Region("EZ", "Eurozone", "EUR", "major", True, False),
    Region("JP", "Japan", "JPY", "major", True, True),
    Region("UK", "United Kingdom", "GBP", "major", True, False),
    Region("AU", "Australia", "AUD", "commodity", True, True),
    Region("CA", "Canada", "CAD", "commodity", True, True),
    Region("NZ", "New Zealand", "NZD", "commodity", True, True),
    Region("CN", "China", "CNY", "influence", False, True),
    Region("CH", "Switzerland", "CHF", "haven", False, False),
]

REGION_BY_CODE: dict[str, Region] = {r.code: r for r in REGIONS}
REGION_BY_CURRENCY: dict[str, Region] = {r.currency: r for r in REGIONS}
REGION_CODES: list[str] = [r.code for r in REGIONS]


def series_id(region: str, indicator: str) -> str:
    """Canonical series id, e.g. series_id("US", "CPI_YOY") -> "US.CPI_YOY"."""
    return f"{region}.{indicator}"


def fx_series_id(currency: str) -> str:
    """Daily USD value of one unit of `currency`, e.g. "FX.EURUSD".

    For USD itself use FX_USD_INDEX (a basket index, higher = stronger dollar).
    """
    return FX_USD_INDEX if currency == "USD" else f"FX.{currency}USD"


FX_USD_INDEX = "FX.USD_INDEX"
