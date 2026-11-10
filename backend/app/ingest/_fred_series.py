"""Declarative FRED mapping: (region, indicator) -> FRED id + transform.

Every id below was fetched and checked for freshness on 2026-11-10 (see
docs/sources/macro.md). Most OECD "Main Economic Indicators" mirrors on FRED
(CPALTT01*, LRHUTTTT*, BSCICP03*, ...) are stale or lag the OECD's own API by
one to two months, so non-US releases are handled by macro_official instead and
FRED is used for what it is best at: US data, the global risk gauges and a few
daily central-bank rates it mirrors without delay.

Transforms:
  level    store as published (times `scale`)
  yoy      percent change vs the same period one year earlier (from an index/level)
  diff     change vs the previous period (times `scale`)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FredSeries:
    region: str
    indicator: str
    fred_id: str
    transform: str = "level"  # level | yoy | diff
    frequency: str = "M"  # D / W / M / Q
    scale: float = 1.0
    unit: str | None = None  # None -> catalog unit
    note: str = ""


FRED_SERIES: list[FredSeries] = [
    # ------------------------------------------------------------------ United States
    FredSeries("US", "POLICY_RATE", "DFEDTARU", frequency="D",
               note="Fed funds target range, upper bound"),
    FredSeries("US", "CPI_YOY", "CPIAUCSL", "yoy", note="CPI-U all items SA, y/y from index"),
    FredSeries("US", "CORE_CPI_YOY", "CPILFESL", "yoy", note="CPI-U ex food & energy SA, y/y from index"),
    FredSeries("US", "PCE_YOY", "PCEPI", "yoy", note="PCE price index, y/y from index"),
    FredSeries("US", "CORE_PCE_YOY", "PCEPILFE", "yoy", note="PCE ex food & energy, y/y from index"),
    FredSeries("US", "UNEMPLOYMENT", "UNRATE", note="U-3 unemployment rate SA"),
    FredSeries("US", "EMPLOYMENT_CHG", "PAYEMS", "diff", unit="thousands",
               note="Nonfarm payrolls, monthly change (NFP)"),
    FredSeries("US", "JOBLESS_CLAIMS", "ICSA", frequency="W", scale=0.001,
               note="Initial claims SA, week ending Saturday"),
    FredSeries("US", "CONSUMER_CONF", "UMCSENT",
               note="University of Michigan sentiment (1966Q1=100); no fixed neutral level"),
    FredSeries("US", "RETAIL_SALES_YOY", "RSAFS", "yoy", note="Retail & food services sales, nominal, y/y"),
    FredSeries("US", "INDUSTRIAL_PROD_YOY", "INDPRO", "yoy", note="Industrial production index, y/y"),
    FredSeries("US", "GDP_YOY", "GDPC1", "yoy", frequency="Q", note="Real GDP SAAR level, y/y"),
    FredSeries("US", "YIELD_2Y", "DGS2", frequency="D", note="2Y Treasury constant maturity"),
    FredSeries("US", "YIELD_10Y", "DGS10", frequency="D", note="10Y Treasury constant maturity"),
    FredSeries("US", "TRADE_BALANCE", "BOPGTB", scale=0.001, unit="USD bn",
               note="Goods trade balance, BoP basis, SA, monthly"),
    FredSeries("US", "EXPORTS_YOY", "BOPGEXP", "yoy", note="Goods exports, BoP basis, nominal USD, y/y"),
    FredSeries("US", "IMPORTS_YOY", "BOPGIMP", "yoy", note="Goods imports, BoP basis, nominal USD, y/y"),
    # ---------------------------------------------------------------------- euro area
    FredSeries("EZ", "POLICY_RATE", "ECBDFR", frequency="D", note="ECB deposit facility rate"),
    # ----------------------------------------------------------------- United Kingdom
    # The BoE database rejects scripted clients and no other keyless source carries
    # a gilt 2Y; SONIA is the closest free daily short-rate series.
    FredSeries("UK", "YIELD_2Y", "IUDSOIA", frequency="D",
               note="proxy: SONIA overnight rate (no free 2Y gilt series)"),
    # ------------------------------------------------------------------------- global
    FredSeries("GLOBAL", "VIX", "VIXCLS", frequency="D", unit="index"),
    FredSeries("GLOBAL", "SPX", "SP500", frequency="D", unit="index",
               note="FRED only carries the last 10 years"),
    FredSeries("GLOBAL", "HY_SPREAD", "BAMLH0A0HYM2", frequency="D", unit="%",
               note="ICE BofA US HY OAS; FRED only carries the last 3 years"),
    FredSeries("GLOBAL", "OIL", "DCOILWTICO", frequency="D", unit="USD/bbl", note="WTI spot, Cushing"),
    FredSeries("GLOBAL", "COPPER", "PCOPPUSDM", frequency="M", unit="USD/tonne",
               note="IMF global copper price, monthly average"),
    FredSeries("GLOBAL", "US_10Y_REAL", "DFII10", frequency="D", unit="%", note="10Y TIPS constant maturity"),
    # GLOBAL.GOLD: no free keyless daily gold price was found (FRED dropped the LBMA
    # fixings in 2022); see docs/sources/macro.md.
]
