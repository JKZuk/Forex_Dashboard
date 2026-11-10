"""Canonical indicator catalog: the shared vocabulary between ingestion and scoring.

Ingestion stores each indicator as series `<REGION>.<KEY>` (see regions.series_id)
in the unit given here. Scoring reads by the same id and must tolerate a series
being absent for a region (free data has gaps).

`bullish_when` describes the usual currency reaction to a HIGHER reading:
  "higher"  -> higher value is currency-positive (hawkish / stronger growth)
  "lower"   -> higher value is currency-negative (e.g. unemployment)
`impact` (1-3) is the market-moving weight of the release.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Indicator:
    key: str
    name: str
    category: str  # policy | inflation | employment | activity | yields | trade | growth
    unit: str
    bullish_when: str  # "higher" | "lower"
    impact: int  # 1 (low) .. 3 (high)


INDICATORS: list[Indicator] = [
    # --- central bank policy ---
    Indicator("POLICY_RATE", "Central bank policy rate", "policy", "%", "higher", 3),
    # --- inflation (year-over-year, percent) ---
    Indicator("CPI_YOY", "CPI inflation y/y", "inflation", "%", "higher", 3),
    Indicator("CORE_CPI_YOY", "Core CPI inflation y/y", "inflation", "%", "higher", 3),
    Indicator("PCE_YOY", "PCE inflation y/y (US)", "inflation", "%", "higher", 2),
    Indicator("CORE_PCE_YOY", "Core PCE inflation y/y (US)", "inflation", "%", "higher", 3),
    # --- employment ---
    Indicator("UNEMPLOYMENT", "Unemployment rate", "employment", "%", "lower", 3),
    Indicator("EMPLOYMENT_CHG", "Employment change m/m (US = NFP)", "employment", "thousands", "higher", 3),
    Indicator("JOBLESS_CLAIMS", "Initial jobless claims (US)", "employment", "thousands", "lower", 2),
    # --- activity surveys ---
    Indicator("PMI", "PMI / business survey (50 or 100 = neutral, see series note)", "activity", "index", "higher", 3),
    Indicator("CONSUMER_CONF", "Consumer confidence", "activity", "index", "higher", 1),
    Indicator("RETAIL_SALES_YOY", "Retail sales y/y", "activity", "%", "higher", 2),
    Indicator("INDUSTRIAL_PROD_YOY", "Industrial production y/y", "activity", "%", "higher", 1),
    # --- growth ---
    Indicator("GDP_YOY", "Real GDP y/y", "growth", "%", "higher", 2),
    # --- yields ---
    Indicator("YIELD_2Y", "2-year government yield (or short-rate proxy)", "yields", "%", "higher", 3),
    Indicator("YIELD_10Y", "10-year government yield", "yields", "%", "higher", 2),
    # --- trade / external ---
    Indicator("TRADE_BALANCE", "Goods trade balance (local currency or USD, see series unit)", "trade", "level", "higher", 2),
    Indicator("EXPORTS_YOY", "Exports y/y", "trade", "%", "higher", 2),
    Indicator("IMPORTS_YOY", "Imports y/y", "trade", "%", "higher", 1),
    Indicator("CURRENT_ACCOUNT_GDP", "Current account, % of GDP", "trade", "% of GDP", "higher", 2),
]

INDICATOR_BY_KEY: dict[str, Indicator] = {i.key: i for i in INDICATORS}

# Global (region = "GLOBAL") risk and commodity gauges, daily where available.
GLOBAL_SERIES: dict[str, str] = {
    "GLOBAL.VIX": "CBOE VIX (higher = risk-off)",
    "GLOBAL.SPX": "S&P 500 index",
    "GLOBAL.HY_SPREAD": "US high-yield credit spread, % (higher = risk-off)",
    "GLOBAL.OIL": "Crude oil price, USD/bbl",
    "GLOBAL.GOLD": "Gold price, USD/oz",
    "GLOBAL.COPPER": "Copper price",
    "GLOBAL.US_10Y_REAL": "US 10-year real yield (TIPS), %",
}
