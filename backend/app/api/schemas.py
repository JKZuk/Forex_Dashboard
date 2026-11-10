"""Pydantic response models for the REST API (documented in docs/API.md)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Optional

from pydantic import BaseModel, Field, PlainSerializer


def _iso_utc(value: datetime) -> str:
    """The database stores naive UTC datetimes; always emit an explicit `Z`."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


UtcDateTime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]


# --- regions -----------------------------------------------------------------
class RegionOut(BaseModel):
    code: str
    name: str
    currency: str
    group: str  # major | commodity | influence | haven
    core: bool
    trade_sensitive: bool
    scored: bool
    fx_series_id: str


# --- scores ------------------------------------------------------------------
class HorizonScore(BaseModel):
    horizon: str
    score: float
    label: str
    coverage: float
    no_data: bool = False  # label "No Data": nothing behind the score, do not show it as Neutral
    as_of: date
    change_1w: Optional[float] = None  # points vs the score about a week earlier
    previous_as_of: Optional[date] = None


class PriceSnapshot(BaseModel):
    series_id: str
    name: str  # e.g. "EUR/USD" or "USD index"
    date: date
    value: float
    change_1d_pct: Optional[float] = None
    change_1w_pct: Optional[float] = None
    change_1m_pct: Optional[float] = None


class OverviewRegion(BaseModel):
    region: RegionOut
    as_of: Optional[date] = None
    scores: dict[str, HorizonScore]  # keyed by horizon; missing key = no score
    headline: Optional[str] = None
    price: Optional[PriceSnapshot] = None


class OverviewOut(BaseModel):
    has_data: bool
    as_of: Optional[date] = None  # most recent score date across regions
    horizons: list[str]
    regions: list[OverviewRegion]


class ComponentOut(BaseModel):
    component: str
    label: str
    raw_display: str
    signal: Optional[float] = None  # None = no data
    weight: float
    contribution: float
    detail: str
    has_data: bool


class HorizonPanel(BaseModel):
    horizon: str
    score: Optional[HorizonScore] = None
    components: list[ComponentOut]


class ConclusionOut(BaseModel):
    as_of: date
    headline: str
    text: str


class SeriesPoint(BaseModel):
    date: date
    value: float


class IndicatorRow(BaseModel):
    series_id: str
    indicator: str
    name: str
    category: str
    unit: str
    frequency: str
    source: str
    note: str
    bullish_when: Optional[str] = None  # higher | lower, from the catalog
    impact: Optional[int] = None
    latest_date: date
    latest_value: float
    previous_date: Optional[date] = None
    previous_value: Optional[float] = None
    change: Optional[float] = None
    spark: list[SeriesPoint] = Field(default_factory=list)  # recent points, oldest first


class SeriesOut(BaseModel):
    series_id: str
    found: bool
    region: Optional[str] = None
    indicator: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    unit: str = ""
    frequency: str = ""
    source: str = ""
    note: str = ""
    last_updated: Optional[UtcDateTime] = None
    points: list[SeriesPoint] = Field(default_factory=list)


class NewsSentimentOut(BaseModel):
    region: str
    score: float


class NewsOut(BaseModel):
    id: str
    source: str
    kind: str
    title: str
    summary: str
    url: str
    published_at: UtcDateTime
    sentiment: float
    engine: str
    region_score: Optional[float] = None  # score for the requested region, if any
    regions: list[NewsSentimentOut] = Field(default_factory=list)


class NewsFeedOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[NewsOut]


class EventOut(BaseModel):
    id: str
    region: str
    title: str
    event_time: UtcDateTime
    time_known: bool
    impact: str
    actual: str
    forecast: str
    previous: str
    source: str


class CalendarOut(BaseModel):
    start: UtcDateTime
    end: UtcDateTime
    total: int
    events: list[EventOut]


class RegionDetailOut(BaseModel):
    region: RegionOut
    has_data: bool
    as_of: Optional[date] = None
    horizons: list[HorizonPanel]
    conclusion: Optional[ConclusionOut] = None
    price: Optional[PriceSnapshot] = None
    indicators: list[IndicatorRow]
    trade: list[SeriesOut]  # trade balance / exports / imports / current account
    news: list[NewsOut]
    events: list[EventOut]


class ScoreHistoryPoint(BaseModel):
    date: date
    score: float
    coverage: float


class ScoreHistoryOut(BaseModel):
    region: str
    start: Optional[date] = None
    end: Optional[date] = None
    series: dict[str, list[ScoreHistoryPoint]]  # keyed by horizon


class PriceHistoryOut(BaseModel):
    region: str
    currency: str
    series_id: str
    name: str
    found: bool
    points: list[SeriesPoint]


# --- heatmap -----------------------------------------------------------------
class HeatmapCurrency(BaseModel):
    region: str
    currency: str
    name: str
    group: str
    scored: bool
    as_of: Optional[date] = None
    scores: dict[str, Optional[float]]
    labels: dict[str, Optional[str]]


class PairIdea(BaseModel):
    pair: str  # conventional market name, e.g. "EUR/JPY"
    direction: str  # long | short (of the pair as named)
    strong: str  # currency with the higher score
    weak: str
    differential: float  # strong score - weak score, points (always >= 0)


class HeatmapOut(BaseModel):
    has_data: bool
    as_of: Optional[date] = None
    horizons: list[str]
    currencies: list[HeatmapCurrency]
    pairs: dict[str, list[PairIdea]]  # per horizon, ranked by differential desc


# --- COT ---------------------------------------------------------------------
class CotSummaryRow(BaseModel):
    currency: str
    region: Optional[str] = None
    report_date: date
    noncomm_long: float
    noncomm_short: float
    net: float
    open_interest: float
    net_pct_oi: float
    change_1w: Optional[float] = None  # contracts
    change_4w: Optional[float] = None
    pct_oi_change_1w: Optional[float] = None  # percentage points
    pct_oi_change_4w: Optional[float] = None
    percentile_3y: Optional[float] = None  # 0..100, of net_pct_oi
    percentile_weeks: int = 0  # sample size behind the percentile
    extreme: Optional[str] = None  # "long" | "short" | None
    spark: list[SeriesPoint] = Field(default_factory=list)  # net_pct_oi, last 52 weeks


class CotSummaryOut(BaseModel):
    has_data: bool
    report_date: Optional[date] = None
    rows: list[CotSummaryRow]


class CotHistoryPoint(BaseModel):
    date: date
    noncomm_long: float
    noncomm_short: float
    net: float
    open_interest: float
    net_pct_oi: float


class CotHistoryOut(BaseModel):
    currency: str
    points: list[CotHistoryPoint]


# --- global gauges -----------------------------------------------------------
class GaugeOut(BaseModel):
    series_id: str
    name: str
    unit: str
    date: date
    value: float
    change_1d_pct: Optional[float] = None
    change_1w_pct: Optional[float] = None
    change_1m_pct: Optional[float] = None


# --- refresh / health --------------------------------------------------------
class RefreshRequest(BaseModel):
    sources: Optional[list[str]] = None


class RefreshStartOut(BaseModel):
    started: bool
    running: bool
    sources: list[str]
    message: str


class RefreshJobOut(BaseModel):
    job: str
    status: str  # running | ok | error | never
    started_at: Optional[UtcDateTime] = None
    finished_at: Optional[UtcDateTime] = None
    rows: int = 0
    message: str = ""
    last_success_at: Optional[UtcDateTime] = None


class RefreshStatusOut(BaseModel):
    running: bool
    server_time: UtcDateTime
    last_success_at: Optional[UtcDateTime] = None  # last fully successful refresh
    last_finished_at: Optional[UtcDateTime] = None  # last time any job finished
    sources: list[str]
    jobs: list[RefreshJobOut]
    auto_refresh_minutes: int


class HealthOut(BaseModel):
    status: str  # ok | degraded
    database: str  # ok | error
    dialect: str
    has_data: bool
    server_time: UtcDateTime
    frontend_built: bool
