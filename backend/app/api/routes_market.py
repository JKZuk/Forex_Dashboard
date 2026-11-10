"""COT positioning, calendar, news and generic series endpoints."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import regions as reg
from ..models import CalendarEvent, CotPosition, NewsItem, NewsRegionSentiment
from . import util
from .schemas import (
    CalendarOut,
    CotHistoryOut,
    CotHistoryPoint,
    CotSummaryOut,
    CotSummaryRow,
    EventOut,
    NewsFeedOut,
    NewsOut,
    NewsSentimentOut,
    SeriesOut,
    SeriesPoint,
)

router = APIRouter()

IMPACTS = ["high", "medium", "low"]
COT_LOOKBACK_DAYS = 365 * 3 + 7


# --- COT ---------------------------------------------------------------------
def _currency_order(currency: str) -> tuple[int, str]:
    codes = [r.currency for r in reg.REGIONS]
    return (codes.index(currency) if currency in codes else len(codes), currency)


@router.get("/cot", response_model=CotSummaryOut)
def cot_summary(session: Session = Depends(util.get_session)) -> CotSummaryOut:
    latest = session.execute(select(func.max(CotPosition.report_date))).scalar()
    if latest is None:
        return CotSummaryOut(has_data=False, rows=[])
    rows = session.execute(
        select(CotPosition)
        .where(CotPosition.report_date >= latest - timedelta(days=COT_LOOKBACK_DAYS))
        .order_by(CotPosition.currency, CotPosition.report_date)
    ).scalars()
    by_ccy: dict[str, list[CotPosition]] = defaultdict(list)
    for row in rows:
        by_ccy[row.currency].append(row)

    out: list[CotSummaryRow] = []
    for ccy in sorted(by_ccy, key=_currency_order):
        hist = by_ccy[ccy]
        cur = hist[-1]
        # Keep each currency's own 3-year window (its latest report may lag).
        hist = [h for h in hist if h.report_date >= cur.report_date - timedelta(days=COT_LOOKBACK_DAYS)]
        dates = [h.report_date for h in hist]

        def back(days: int) -> Optional[CotPosition]:
            i = bisect_right(dates, cur.report_date - timedelta(days=days - 3))
            if not i:
                return None
            cand = hist[i - 1]
            # Reject a stale comparison when weeks are missing.
            return cand if (cur.report_date - cand.report_date).days <= days + 10 else None

        w1, w4 = back(7), back(28)
        pct: Optional[float] = None
        extreme: Optional[str] = None
        if len(hist) >= 26:  # need at least half a year for a meaningful percentile
            values = [h.net_pct_oi for h in hist]
            below = sum(1 for v in values if v < cur.net_pct_oi)
            equal = sum(1 for v in values if v == cur.net_pct_oi)
            pct = round((below + 0.5 * equal) / len(values) * 100.0, 1)
            extreme = "long" if pct >= 90 else "short" if pct <= 10 else None
        region = reg.REGION_BY_CURRENCY.get(ccy)
        out.append(
            CotSummaryRow(
                currency=ccy,
                region=region.code if region else None,
                report_date=cur.report_date,
                noncomm_long=cur.noncomm_long,
                noncomm_short=cur.noncomm_short,
                net=cur.noncomm_net,
                open_interest=cur.open_interest,
                net_pct_oi=round(cur.net_pct_oi, 2),
                change_1w=(cur.noncomm_net - w1.noncomm_net) if w1 else None,
                change_4w=(cur.noncomm_net - w4.noncomm_net) if w4 else None,
                pct_oi_change_1w=round(cur.net_pct_oi - w1.net_pct_oi, 2) if w1 else None,
                pct_oi_change_4w=round(cur.net_pct_oi - w4.net_pct_oi, 2) if w4 else None,
                percentile_3y=pct,
                percentile_weeks=len(hist),
                extreme=extreme,
                spark=[
                    SeriesPoint(date=h.report_date, value=round(h.net_pct_oi, 2)) for h in hist[-52:]
                ],
            )
        )
    return CotSummaryOut(has_data=bool(out), report_date=latest, rows=out)


@router.get("/cot/{currency}", response_model=CotHistoryOut)
def cot_history(
    currency: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    session: Session = Depends(util.get_session),
) -> CotHistoryOut:
    ccy = currency.upper()
    if ccy in reg.REGION_BY_CODE:  # accept a region code too
        ccy = reg.REGION_BY_CODE[ccy].currency
    q = select(CotPosition).where(CotPosition.currency == ccy)
    if start:
        q = q.where(CotPosition.report_date >= start)
    if end:
        q = q.where(CotPosition.report_date <= end)
    points = [
        CotHistoryPoint(
            date=p.report_date,
            noncomm_long=p.noncomm_long,
            noncomm_short=p.noncomm_short,
            net=p.noncomm_net,
            open_interest=p.open_interest,
            net_pct_oi=round(p.net_pct_oi, 2),
        )
        for p in session.execute(q.order_by(CotPosition.report_date)).scalars()
    ]
    return CotHistoryOut(currency=ccy, points=points)


# --- calendar ----------------------------------------------------------------
def event_out(e: CalendarEvent) -> EventOut:
    return EventOut(
        id=e.event_id,
        region=e.region,
        title=e.title,
        event_time=e.event_time,
        time_known=bool(e.time_known),
        impact=(e.impact or "medium").lower(),
        actual=e.actual or "",
        forecast=e.forecast or "",
        previous=e.previous or "",
        source=e.source or "",
    )


@router.get("/calendar", response_model=CalendarOut)
def calendar(
    days: int = Query(14, ge=1, le=120, description="days ahead of now"),
    past_days: int = Query(0, ge=0, le=60, description="also include this many past days"),
    impact: Optional[str] = Query(None, description="comma separated: high,medium,low"),
    region: Optional[str] = Query(None, description="comma separated region codes"),
    limit: int = Query(500, ge=1, le=2000),
    session: Session = Depends(util.get_session),
) -> CalendarOut:
    now = datetime.utcnow()
    # Start at the beginning of the UTC day so date-only events for today are kept.
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=past_days)
    end = now + timedelta(days=days)
    q = select(CalendarEvent).where(
        CalendarEvent.event_time >= start, CalendarEvent.event_time <= end
    )
    impacts = [i.lower() for i in util.parse_csv(impact)]
    if impacts:
        bad = [i for i in impacts if i not in IMPACTS]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown impact(s): {', '.join(bad)}")
        q = q.where(CalendarEvent.impact.in_(impacts))
    region_codes = [c.upper() for c in util.parse_csv(region)]
    if region_codes:
        q = q.where(CalendarEvent.region.in_(region_codes))
    events = [
        event_out(e)
        for e in session.execute(q.order_by(CalendarEvent.event_time, CalendarEvent.region).limit(limit)).scalars()
    ]
    return CalendarOut(start=start, end=end, total=len(events), events=events)


# --- news --------------------------------------------------------------------
def _news_out(
    item: NewsItem, sentiments: dict[str, list[NewsSentimentOut]], region: Optional[str]
) -> NewsOut:
    regions = sorted(sentiments.get(item.url_hash, []), key=lambda s: -abs(s.score))
    region_score = next((s.score for s in regions if s.region == region), None) if region else None
    return NewsOut(
        id=item.url_hash,
        source=item.source,
        kind=item.kind or "news",
        title=item.title,
        summary=item.summary or "",
        url=item.url,
        published_at=item.published_at,
        sentiment=round(item.sentiment or 0.0, 3),
        engine=item.engine or "",
        region_score=region_score,
        regions=regions,
    )


def _sentiments_for(session: Session, hashes: list[str]) -> dict[str, list[NewsSentimentOut]]:
    out: dict[str, list[NewsSentimentOut]] = defaultdict(list)
    if hashes:
        rows = session.execute(
            select(NewsRegionSentiment.url_hash, NewsRegionSentiment.region, NewsRegionSentiment.score)
            .where(NewsRegionSentiment.url_hash.in_(hashes))
        )
        for h, r, s in rows:
            out[h].append(NewsSentimentOut(region=r, score=round(s, 3)))
    return out


def _news_query(region: Optional[str], kind: Optional[str], since: Optional[datetime]):
    q = select(NewsItem)
    if region:
        q = q.join(NewsRegionSentiment, NewsRegionSentiment.url_hash == NewsItem.url_hash).where(
            NewsRegionSentiment.region == region
        )
    if kind:
        q = q.where(NewsItem.kind == kind)
    if since:
        q = q.where(NewsItem.published_at >= since)
    return q


def news_for_region(session: Session, region: str, limit: int = 12) -> list[NewsOut]:
    items = list(
        session.execute(
            _news_query(region, None, None).order_by(NewsItem.published_at.desc()).limit(limit)
        ).scalars()
    )
    sentiments = _sentiments_for(session, [i.url_hash for i in items])
    return [_news_out(i, sentiments, region) for i in items]


@router.get("/news", response_model=NewsFeedOut)
def news_feed(
    region: Optional[str] = Query(None, description="region code or GLOBAL"),
    kind: Optional[str] = Query(None, description="news | central_bank"),
    days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(util.get_session),
) -> NewsFeedOut:
    region_code = region.upper() if region else None
    since = datetime.utcnow() - timedelta(days=days) if days else None
    base = _news_query(region_code, kind, since)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    items = list(
        session.execute(
            base.order_by(NewsItem.published_at.desc(), NewsItem.url_hash).limit(limit).offset(offset)
        ).scalars()
    )
    sentiments = _sentiments_for(session, [i.url_hash for i in items])
    return NewsFeedOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[_news_out(i, sentiments, region_code) for i in items],
    )


# --- generic series ----------------------------------------------------------
@router.get("/series/{series_id}", response_model=SeriesOut)
def series(
    series_id: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    session: Session = Depends(util.get_session),
) -> SeriesOut:
    """Any stored series by id. An absent series returns found=false (not a 404)."""
    return util.load_series(session, [series_id], start, end)[series_id]
