"""Regions, overview, region drill-down, history and heatmap endpoints."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import catalog
from .. import regions as reg
from ..models import (
    CalendarEvent,
    Observation,
    Score,
    ScoreComponent,
    SeriesMeta,
)
from . import util
from .routes_market import event_out, news_for_region
from .schemas import (
    ComponentOut,
    ConclusionOut,
    GaugeOut,
    HeatmapCurrency,
    HeatmapOut,
    HorizonPanel,
    IndicatorRow,
    OverviewOut,
    OverviewRegion,
    PairIdea,
    PriceHistoryOut,
    RegionDetailOut,
    RegionOut,
    ScoreHistoryOut,
    ScoreHistoryPoint,
    SeriesPoint,
)

router = APIRouter()

TRADE_KEYS = ["TRADE_BALANCE", "EXPORTS_YOY", "IMPORTS_YOY", "CURRENT_ACCOUNT_GDP"]

# Conventional base/quote priority used to name pairs the way the market does.
PAIR_PRIORITY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "CNY", "JPY"]


def _pair_rank(currency: str) -> int:
    return PAIR_PRIORITY.index(currency) if currency in PAIR_PRIORITY else len(PAIR_PRIORITY)


@router.get("/regions", response_model=list[RegionOut])
def list_regions() -> list[RegionOut]:
    return [util.region_out(r) for r in reg.REGIONS]


@router.get("/overview", response_model=OverviewOut)
def overview(session: Session = Depends(util.get_session)) -> OverviewOut:
    scores = util.latest_scores(session)
    headlines = util.latest_headlines(session)
    prices = util.price_snapshots(session, reg.REGIONS)
    items: list[OverviewRegion] = []
    for r in reg.REGIONS:
        region_scores = scores.get(r.code, {})
        as_of = max((s.as_of for s in region_scores.values()), default=None)
        concl = headlines.get(r.code)
        items.append(
            OverviewRegion(
                region=util.region_out(r),
                as_of=as_of,
                scores=region_scores,
                headline=concl.headline if concl else None,
                price=prices.get(r.code),
            )
        )
    dates = [i.as_of for i in items if i.as_of]
    return OverviewOut(
        has_data=bool(dates),
        as_of=max(dates) if dates else None,
        horizons=util.HORIZONS,
        regions=items,
    )


@router.get("/regions/{code}", response_model=RegionDetailOut)
def region_detail(code: str, session: Session = Depends(util.get_session)) -> RegionDetailOut:
    r = util.get_region(code)
    scores = util.latest_scores(session, [r.code]).get(r.code, {})
    as_of = max((s.as_of for s in scores.values()), default=None)

    # Components at the latest date that has any (normally == the score date).
    comp_date = session.execute(
        select(func.max(ScoreComponent.as_of)).where(ScoreComponent.region == r.code)
    ).scalar()
    comps: dict[str, list[ComponentOut]] = defaultdict(list)
    if comp_date is not None:
        rows = session.execute(
            select(ScoreComponent).where(
                ScoreComponent.region == r.code, ScoreComponent.as_of == comp_date
            )
        ).scalars()
        for c in rows:
            comps[c.horizon].append(
                ComponentOut(
                    component=c.component,
                    label=c.label,
                    raw_display=c.raw_display or "",
                    signal=c.signal,
                    weight=c.weight,
                    contribution=round(c.contribution or 0.0, 2),
                    detail=c.detail or "",
                    has_data=c.signal is not None,
                )
            )
    panels = []
    for h in util.HORIZONS:
        # Drivers with data first, largest absolute contribution on top.
        items = sorted(
            comps.get(h, []), key=lambda c: (not c.has_data, -abs(c.contribution), -c.weight)
        )
        panels.append(HorizonPanel(horizon=h, score=scores.get(h), components=items))

    concl = util.latest_headlines(session).get(r.code)

    # Key indicators: every series stored for the region, catalog order.
    metas = list(
        session.execute(select(SeriesMeta).where(SeriesMeta.region == r.code)).scalars()
    )
    recent = util.last_n_per_series(session, [m.series_id for m in metas], 24)
    order = {ind.key: i for i, ind in enumerate(catalog.INDICATORS)}
    indicators: list[IndicatorRow] = []
    for m in sorted(metas, key=lambda m: (order.get(m.indicator, 999), m.series_id)):
        points = recent.get(m.series_id)
        if not points:
            continue
        ind = catalog.INDICATOR_BY_KEY.get(m.indicator)
        prev = points[-2] if len(points) > 1 else None
        indicators.append(
            IndicatorRow(
                series_id=m.series_id,
                indicator=m.indicator,
                name=(ind.name if ind else None) or m.name,
                category=m.category or (ind.category if ind else ""),
                unit=m.unit or (ind.unit if ind else ""),
                frequency=m.frequency or "",
                source=m.source or "",
                note=m.note or "",
                bullish_when=ind.bullish_when if ind else None,
                impact=ind.impact if ind else None,
                latest_date=points[-1][0],
                latest_value=points[-1][1],
                previous_date=prev[0] if prev else None,
                previous_value=prev[1] if prev else None,
                change=(points[-1][1] - prev[1]) if prev else None,
                spark=[SeriesPoint(date=d, value=v) for d, v in points],
            )
        )

    trade_ids = [reg.series_id(r.code, k) for k in TRADE_KEYS]
    trade_map = util.load_series(session, trade_ids, start=date.today() - timedelta(days=366 * 6))
    trade = [trade_map[sid] for sid in trade_ids if trade_map[sid].found and trade_map[sid].points]

    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = session.execute(
        select(CalendarEvent)
        .where(
            CalendarEvent.region == r.code,
            CalendarEvent.event_time >= day_start,
            CalendarEvent.event_time <= now + timedelta(days=45),
        )
        .order_by(CalendarEvent.event_time)
        .limit(12)
    ).scalars()

    has_data = bool(scores or indicators or comps)
    return RegionDetailOut(
        region=util.region_out(r),
        has_data=has_data,
        as_of=as_of,
        horizons=panels,
        conclusion=ConclusionOut(as_of=concl.as_of, headline=concl.headline, text=concl.text)
        if concl
        else None,
        price=util.price_snapshots(session, [r]).get(r.code),
        indicators=indicators,
        trade=trade,
        news=news_for_region(session, r.code, limit=12),
        events=[event_out(e) for e in events],
    )


@router.get("/history/scores", response_model=ScoreHistoryOut)
def score_history(
    region: str,
    horizons: Optional[str] = Query(None, description="comma separated: short,medium,long"),
    start: Optional[date] = None,
    end: Optional[date] = None,
    session: Session = Depends(util.get_session),
) -> ScoreHistoryOut:
    r = util.get_region(region)
    wanted = util.parse_horizons(horizons)
    # "No Data" rows (score 0, coverage 0) are gaps, not neutral readings: leave them out.
    q = select(Score.horizon, Score.as_of, Score.score, Score.coverage).where(
        Score.region == r.code,
        Score.horizon.in_(wanted),
        Score.label != util.NO_DATA_LABEL,
        Score.coverage > 0,
    )
    if start:
        q = q.where(Score.as_of >= start)
    if end:
        q = q.where(Score.as_of <= end)
    series: dict[str, list[ScoreHistoryPoint]] = {h: [] for h in wanted}
    for h, d, s, cov in session.execute(q.order_by(Score.as_of)):
        series[h].append(ScoreHistoryPoint(date=d, score=round(s, 1), coverage=round(cov, 3)))
    return ScoreHistoryOut(region=r.code, start=start, end=end, series=series)


@router.get("/history/price", response_model=PriceHistoryOut)
def price_history(
    region: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    session: Session = Depends(util.get_session),
) -> PriceHistoryOut:
    r = util.get_region(region)
    sid = reg.fx_series_id(r.currency)
    q = select(Observation.obs_date, Observation.value).where(Observation.series_id == sid)
    if start:
        q = q.where(Observation.obs_date >= start)
    if end:
        q = q.where(Observation.obs_date <= end)
    points = [SeriesPoint(date=d, value=v) for d, v in session.execute(q.order_by(Observation.obs_date))]
    return PriceHistoryOut(
        region=r.code,
        currency=r.currency,
        series_id=sid,
        name=util.fx_name(r.currency),
        found=bool(points),
        points=points,
    )


@router.get("/heatmap", response_model=HeatmapOut)
def heatmap(session: Session = Depends(util.get_session)) -> HeatmapOut:
    scores = util.latest_scores(session)
    currencies: list[HeatmapCurrency] = []
    for r in reg.REGIONS:
        rs = scores.get(r.code, {})
        currencies.append(
            HeatmapCurrency(
                region=r.code,
                currency=r.currency,
                name=r.name,
                group=r.group,
                scored=r.scored,
                as_of=max((s.as_of for s in rs.values()), default=None),
                # no-data scores are null here so they never enter the pair ranking
                scores={h: (rs[h].score if h in rs and not rs[h].no_data else None) for h in util.HORIZONS},
                labels={h: (rs[h].label if h in rs else None) for h in util.HORIZONS},
            )
        )

    pairs: dict[str, list[PairIdea]] = {}
    for h in util.HORIZONS:
        scored = [(c.currency, c.scores[h]) for c in currencies if c.scored and c.scores[h] is not None]
        ideas: list[PairIdea] = []
        for i, (ca, sa) in enumerate(scored):
            for cb, sb in scored[i + 1 :]:
                base, quote = sorted((ca, cb), key=_pair_rank)
                strong, weak = (ca, cb) if sa >= sb else (cb, ca)
                ideas.append(
                    PairIdea(
                        pair=f"{base}/{quote}",
                        direction="long" if strong == base else "short",
                        strong=strong,
                        weak=weak,
                        differential=round(abs(sa - sb), 1),
                    )
                )
        ideas.sort(key=lambda p: -p.differential)
        pairs[h] = ideas

    dates = [c.as_of for c in currencies if c.as_of]
    return HeatmapOut(
        has_data=bool(dates),
        as_of=max(dates) if dates else None,
        horizons=util.HORIZONS,
        currencies=currencies,
        pairs=pairs,
    )


@router.get("/global", response_model=list[GaugeOut])
def global_gauges(session: Session = Depends(util.get_session)) -> list[GaugeOut]:
    """Latest reading of each GLOBAL.* risk / commodity gauge that has data."""
    metas = {
        m.series_id: m
        for m in session.execute(select(SeriesMeta).where(SeriesMeta.region == "GLOBAL")).scalars()
    }
    ids = list(dict.fromkeys(list(catalog.GLOBAL_SERIES) + sorted(metas)))
    window = util.recent_window(session, ids)
    out: list[GaugeOut] = []
    for sid in ids:
        points = window.get(sid)
        if not points:
            continue
        ch = util.changes(points)
        meta = metas.get(sid)
        out.append(
            GaugeOut(
                series_id=sid,
                name=catalog.GLOBAL_SERIES.get(sid) or (meta.name if meta else sid),
                unit=(meta.unit if meta else "") or "",
                date=points[-1][0],
                value=points[-1][1],
                change_1d_pct=ch["d1"],
                change_1w_pct=ch["w1"],
                change_1m_pct=ch["m1"],
            )
        )
    return out
