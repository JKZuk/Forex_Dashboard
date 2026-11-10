"""Database schema. All tables use natural primary keys so `db.upsert` works.

Only portable column types are used (runs on MySQL 8/9 and SQLite).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class SeriesMeta(Base):
    """One row per time series stored in `observations`."""

    __tablename__ = "series"

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. US.CPI_YOY
    region: Mapped[str] = mapped_column(String(8), index=True)  # region code, GLOBAL or FX
    indicator: Mapped[str] = mapped_column(String(48))  # catalog key, e.g. CPI_YOY
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(32))
    unit: Mapped[str] = mapped_column(String(32), default="")
    frequency: Mapped[str] = mapped_column(String(8), default="")  # D / W / M / Q / A
    source: Mapped[str] = mapped_column(String(32), default="")  # fred, frankfurter, ...
    source_code: Mapped[str] = mapped_column(String(160), default="")  # id at the source
    note: Mapped[str] = mapped_column(String(255), default="")  # e.g. "proxy for 2Y yield"
    last_updated: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Observation(Base):
    """Generic time-series store: macro data, yields, FX rates, risk gauges."""

    __tablename__ = "observations"

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class CotPosition(Base):
    """Weekly CFTC Commitments of Traders, non-commercial (speculative) positions."""

    __tablename__ = "cot_positions"

    currency: Mapped[str] = mapped_column(String(8), primary_key=True)  # EUR, JPY, USD (DXY)...
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    noncomm_long: Mapped[float] = mapped_column(Float)
    noncomm_short: Mapped[float] = mapped_column(Float)
    noncomm_net: Mapped[float] = mapped_column(Float)
    open_interest: Mapped[float] = mapped_column(Float)
    net_pct_oi: Mapped[float] = mapped_column(Float)  # net / open interest * 100


class NewsItem(Base):
    __tablename__ = "news_items"

    url_hash: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1 of url
    source: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="news")  # news | central_bank
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1024))
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC, naive
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)  # generic tone, -1..+1
    engine: Mapped[str] = mapped_column(String(32), default="lexicon")


class NewsRegionSentiment(Base):
    """Per-region reading of a news item: +1 = bullish for that region's currency."""

    __tablename__ = "news_region_sentiment"

    url_hash: Mapped[str] = mapped_column(String(40), primary_key=True)
    region: Mapped[str] = mapped_column(String(8), primary_key=True)
    score: Mapped[float] = mapped_column(Float)  # -1..+1, currency-directional
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # denormalised


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1(source|region|title|date)
    region: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(256))
    event_time: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC, naive
    time_known: Mapped[int] = mapped_column(Integer, default=1)  # 0 = date only
    impact: Mapped[str] = mapped_column(String(8), default="medium")  # high | medium | low
    actual: Mapped[str] = mapped_column(String(32), default="")
    forecast: Mapped[str] = mapped_column(String(32), default="")
    previous: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(32), default="")


class Score(Base):
    __tablename__ = "scores"

    region: Mapped[str] = mapped_column(String(8), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    horizon: Mapped[str] = mapped_column(String(8), primary_key=True)  # short | medium | long
    score: Mapped[float] = mapped_column(Float)  # -100..+100
    label: Mapped[str] = mapped_column(String(24))  # Strong Bullish .. Strong Bearish
    coverage: Mapped[float] = mapped_column(Float, default=1.0)  # share of weight with data, 0..1


class ScoreComponent(Base):
    """Transparent breakdown: one row per driver of a score."""

    __tablename__ = "score_components"

    region: Mapped[str] = mapped_column(String(8), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    horizon: Mapped[str] = mapped_column(String(8), primary_key=True)
    component: Mapped[str] = mapped_column(String(48), primary_key=True)  # e.g. yield_differential
    label: Mapped[str] = mapped_column(String(128))  # human-readable name
    raw_display: Mapped[str] = mapped_column(String(96), default="")  # e.g. "+1.35 pp vs G10 avg"
    signal: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1..+1, NULL = no data
    weight: Mapped[float] = mapped_column(Float)  # configured weight
    contribution: Mapped[float] = mapped_column(Float, default=0.0)  # points added to the score
    detail: Mapped[str] = mapped_column(String(512), default="")


class Conclusion(Base):
    __tablename__ = "conclusions"

    region: Mapped[str] = mapped_column(String(8), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    headline: Mapped[str] = mapped_column(String(256))
    text: Mapped[str] = mapped_column(Text)


class RefreshRun(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(48), index=True)  # ingest source name or "scoring"
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # running | ok | error
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
