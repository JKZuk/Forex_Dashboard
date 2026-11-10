"""Scoring engine tests. No network, no MySQL: a synthetic SQLite database is
seeded once per session and copied for each test."""
from __future__ import annotations

import copy
import importlib.util
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import scoring  # noqa: E402
from app.db import Base, upsert  # noqa: E402
from app.models import (  # noqa: E402
    CalendarEvent, Conclusion, NewsItem, NewsRegionSentiment, Observation, Score, ScoreComponent, SeriesMeta,
)
from app.regions import REGION_BY_CODE, REGIONS  # noqa: E402
from app.scoring.components.positioning import cot_positioning  # noqa: E402
from app.scoring.config import label_for, load_config  # noqa: E402
from app.scoring.data import TS, Context, DataStore, parse_number  # noqa: E402
from app.scoring.engine import score_region  # noqa: E402
from app.scoring.signals import blend, squash  # noqa: E402

END = date(2026, 6, 30)  # fixed "today" of the synthetic world
N_SCORED = sum(1 for r in REGIONS if r.scored)
LABELS = {"Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish", "No Data"}


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_synthetic", BACKEND / "scripts" / "seed_synthetic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def seeded_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("scoring") / "seed.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _load_seed_module().seed(session, end=END, years=7, seed=7)
        session.commit()
    engine.dispose()
    return path


@pytest.fixture()
def session(seeded_path: Path, tmp_path: Path):
    path = tmp_path / "test.db"
    shutil.copy(seeded_path, path)
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture()
def empty_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def live_scores(session: Session, cfg: dict | None = None, as_of: date = END):
    """Score every region as a LIVE run on `as_of` (no publication lag)."""
    cfg = cfg or load_config()
    ctx = Context(DataStore.load(session, cfg), cfg, as_of, today=as_of)
    return {r.code: score_region(ctx, r) for r in REGIONS if r.scored}


def comp(result, region: str, horizon: str, name: str):
    return next(c for c in result[region][horizon].components if c.component == name)


# ------------------------------------------------------------ basic contract
def test_run_writes_scores_components_conclusions(session):
    n = scoring.run(session, END)
    assert n == N_SCORED * 3
    scores = session.execute(select(Score).where(Score.as_of == END)).scalars().all()
    assert len(scores) == n
    comps = session.execute(select(ScoreComponent).where(ScoreComponent.as_of == END)).scalars().all()
    assert not [c for c in comps if c.detail.startswith("Component error")]
    for s in scores:
        assert -100.0 <= s.score <= 100.0
        assert 0.0 <= s.coverage <= 1.0
        assert s.label in LABELS
        mine = [c for c in comps if (c.region, c.horizon) == (s.region, s.horizon)]
        assert mine, f"no components for {s.region}/{s.horizon}"
        assert sum(c.contribution for c in mine) == pytest.approx(s.score, abs=1e-6)
        for c in mine:
            assert c.label and c.weight > 0
            if c.signal is None:
                assert c.contribution == 0.0
            else:
                assert -1.0 <= c.signal <= 1.0
                assert c.raw_display and c.detail
    conclusions = session.execute(select(Conclusion).where(Conclusion.as_of == END)).scalars().all()
    assert len(conclusions) == N_SCORED
    for c in conclusions:
        assert c.headline.startswith(REGION_BY_CODE[c.region].currency + ":")
        assert len(c.text) > 60 and "None" not in c.text and "nan" not in c.text.lower()


def test_run_is_idempotent(session):
    scoring.run(session, END)
    first = {(s.region, s.horizon): s.score for s in session.execute(select(Score)).scalars()}
    n_comp = session.scalar(select(func.count()).select_from(ScoreComponent))
    scoring.run(session, END)
    second = {(s.region, s.horizon): s.score for s in session.execute(select(Score)).scalars()}
    assert first == second
    assert session.scalar(select(func.count()).select_from(ScoreComponent)) == n_comp


def test_signals_are_mostly_non_trivial(session):
    """The fixture has data for most things: scores should not all collapse to 0."""
    result = live_scores(session)
    values = [hs.score for r in result.values() for hs in r.values()]
    assert np.std(values) > 5
    assert sum(1 for r in result.values() for hs in r.values() if hs.coverage > 0.7) >= 0.8 * len(values)


# -------------------------------------------------------------- missing data
def test_empty_database_does_not_crash(empty_session):
    n = scoring.run(empty_session, END)
    assert n == N_SCORED * 3
    for s in empty_session.execute(select(Score)).scalars():
        assert s.score == 0.0 and s.coverage == 0.0 and s.label == "No Data"
    comps = empty_session.execute(select(ScoreComponent)).scalars().all()
    assert comps and all(c.signal is None and c.contribution == 0.0 for c in comps)
    for c in empty_session.execute(select(Conclusion)).scalars():
        assert "not enough data" in c.text


def test_missing_series_degrade_gracefully(session):
    before = live_scores(session)
    session.execute(delete(Observation).where(Observation.series_id.like("NZ.%")))
    session.execute(delete(Observation).where(Observation.series_id.like("GLOBAL.%")))
    session.execute(delete(NewsRegionSentiment))
    session.execute(delete(CalendarEvent))
    session.commit()
    after = live_scores(session)
    nz = after["NZ"]
    # long: only the FX-based trend/valuation component is left
    with_data = [c.component for c in nz["long"].components if c.signal is not None]
    assert with_data == ["trend_valuation"]
    total_w = sum(c.weight for c in nz["long"].components)
    assert nz["long"].coverage == pytest.approx(0.15 / total_w, abs=1e-3)
    assert nz["long"].coverage < before["NZ"]["long"].coverage
    for hs in nz.values():
        assert sum(c.contribution for c in hs.components) == pytest.approx(hs.score, abs=1e-6)
        assert -100 <= hs.score <= 100
    # short: no news, no risk gauges, no macro -> only momentum
    assert [c.component for c in nz["short"].components if c.signal is not None] == ["price_momentum"]
    assert nz["short"].coverage == pytest.approx(0.25, abs=1e-3)
    # weights renormalised: the only live component carries the whole score,
    # scaled down because coverage (25%) is below coverage_shrink_below (50%)
    mom = comp(after, "NZ", "short", "price_momentum")
    assert nz["short"].score == pytest.approx(100 * mom.signal * 0.25 / 0.5, abs=0.01)
    cfg = copy.deepcopy(load_config())
    cfg["general"]["coverage_shrink_below"] = 0
    plain = live_scores(session, cfg)["NZ"]["short"]
    assert plain.score == pytest.approx(100 * mom.signal, abs=0.01)
    # written conclusion flags thin coverage
    scoring.run(session, END)
    text = session.execute(select(Conclusion.text).where(Conclusion.region == "NZ", Conclusion.as_of == END)).scalar_one()
    assert "coverage is thin" in text


def test_stale_series_is_treated_as_missing(session):
    cutoff = END - timedelta(days=400)
    session.execute(delete(Observation).where(Observation.series_id == "UK.CURRENT_ACCOUNT_GDP", Observation.obs_date > cutoff))
    session.commit()
    assert comp(live_scores(session), "UK", "long", "current_account").signal is None


# ------------------------------------------------------------------ direction
def test_higher_2y_yield_vs_peers_raises_medium_score(session):
    before = live_scores(session)
    session.execute(update(Observation).where(Observation.series_id == "UK.YIELD_2Y", Observation.obs_date > END - timedelta(days=75))
                    .values(value=Observation.value + 1.5))
    session.commit()
    after = live_scores(session)
    assert comp(after, "UK", "medium", "yield_differential").signal > comp(before, "UK", "medium", "yield_differential").signal + 0.2
    assert after["UK"]["medium"].score > before["UK"]["medium"].score + 3
    # relative: a peer loses a little
    assert comp(after, "EZ", "medium", "yield_differential").signal < comp(before, "EZ", "medium", "yield_differential").signal


def test_rising_unemployment_lowers_short_score(session):
    before = live_scores(session)
    last = session.execute(select(func.max(Observation.obs_date)).where(Observation.series_id == "US.UNEMPLOYMENT")).scalar_one()
    session.execute(update(Observation).where(Observation.series_id == "US.UNEMPLOYMENT", Observation.obs_date == last)
                    .values(value=Observation.value + 0.8))
    session.commit()
    after = live_scores(session)
    assert comp(after, "US", "short", "data_surprise").signal < comp(before, "US", "short", "data_surprise").signal - 0.05
    assert after["US"]["short"].score < before["US"]["short"].score


def test_hotter_cpi_raises_short_score(session):
    before = live_scores(session)
    last = session.execute(select(func.max(Observation.obs_date)).where(Observation.series_id == "EZ.CPI_YOY")).scalar_one()
    session.execute(update(Observation).where(Observation.series_id == "EZ.CPI_YOY", Observation.obs_date == last)
                    .values(value=Observation.value + 1.0))
    session.commit()
    assert live_scores(session)["EZ"]["short"].score > before["EZ"]["short"].score


def test_risk_off_helps_jpy_and_hurts_aud(session):
    before = live_scores(session)
    recent = Observation.obs_date > END - timedelta(days=8)
    session.execute(update(Observation).where(Observation.series_id == "GLOBAL.VIX", recent).values(value=Observation.value * 2.2))
    session.execute(update(Observation).where(Observation.series_id == "GLOBAL.SPX", recent).values(value=Observation.value * 0.88))
    session.execute(update(Observation).where(Observation.series_id == "GLOBAL.HY_SPREAD", recent).values(value=Observation.value + 2.0))
    session.execute(update(NewsRegionSentiment).where(NewsRegionSentiment.region == "GLOBAL").values(score=-0.8))
    session.commit()
    after = live_scores(session)
    for haven in ("JP", "CH", "US"):
        assert comp(after, haven, "short", "risk_sentiment").signal > 0.2
        assert after[haven]["short"].score > before[haven]["short"].score
    for risky in ("AU", "NZ", "CA"):
        assert comp(after, risky, "short", "risk_sentiment").signal < -0.2
        assert after[risky]["short"].score < before[risky]["short"].score
    assert comp(after, "JP", "short", "risk_sentiment").signal > comp(after, "US", "short", "risk_sentiment").signal


def test_currency_rally_raises_momentum(session):
    before = live_scores(session)
    session.execute(update(Observation).where(Observation.series_id == "FX.GBPUSD", Observation.obs_date > END - timedelta(days=20))
                    .values(value=Observation.value * 1.05))
    session.commit()
    after = live_scores(session)
    assert comp(after, "UK", "short", "price_momentum").signal > comp(before, "UK", "short", "price_momentum").signal
    # basket-relative: everyone else, including USD, loses a little
    assert comp(after, "US", "short", "price_momentum").signal < comp(before, "US", "short", "price_momentum").signal


def test_central_bank_news_weighs_more(session):
    session.execute(delete(NewsRegionSentiment).where(NewsRegionSentiment.region == "CA"))
    when = datetime(END.year, END.month, END.day, 9, 0)
    upsert(session, NewsItem, [
        {"url_hash": "cb", "source": "t", "kind": "central_bank", "title": "hawkish", "summary": "", "url": "u1",
         "published_at": when, "sentiment": 0.6, "engine": "t"},
        {"url_hash": "nw", "source": "t", "kind": "news", "title": "gloomy", "summary": "", "url": "u2",
         "published_at": when, "sentiment": -0.6, "engine": "t"},
    ])
    upsert(session, NewsRegionSentiment, [
        {"url_hash": "cb", "region": "CA", "score": 0.6, "published_at": when},
        {"url_hash": "nw", "region": "CA", "score": -0.6, "published_at": when},
    ])
    session.commit()
    assert comp(live_scores(session), "CA", "short", "news_sentiment").signal > 0.05


def test_trade_sensitive_regions_get_higher_trade_weight(session):
    result = live_scores(session)
    for horizon in ("medium", "long"):
        assert comp(result, "JP", horizon, "trade_balance").weight == pytest.approx(0.175)
        assert comp(result, "US", horizon, "trade_balance").weight == pytest.approx(0.10)
    names = lambda code: {c.component for c in result[code]["medium"].components}  # noqa: E731
    assert "commodity_china" in names("AU") and "commodity_china" in names("CA")
    assert "commodity_china" not in names("US") and "commodity_china" not in names("UK")


def test_china_activity_feeds_aud(session):
    before = live_scores(session)
    session.execute(update(Observation).where(Observation.series_id.in_(["CN.PMI", "CN.INDUSTRIAL_PROD_YOY", "CN.RETAIL_SALES_YOY", "CN.IMPORTS_YOY"]),
                                              Observation.obs_date > END - timedelta(days=130))
                    .values(value=Observation.value + 6.0))
    session.commit()
    after = live_scores(session)
    assert comp(after, "AU", "medium", "commodity_china").signal > comp(before, "AU", "medium", "commodity_china").signal + 0.1
    assert comp(after, "CA", "medium", "commodity_china").signal == comp(before, "CA", "medium", "commodity_china").signal


def test_cot_trend_and_contrarian_extreme():
    cfg = load_config()
    params = cfg["horizons"]["medium"]["components"]["cot_positioning"]
    region = REGION_BY_CODE["EZ"]
    weeks = 160
    days = np.array([END.toordinal() - 7 * (weeks - 1 - i) for i in range(weeks)], dtype=np.int64)

    def signal(values: np.ndarray) -> float:
        store = DataStore()
        store.cot["EUR"] = TS("COT.EUR", days, values.astype(float), "W", 3)
        return cot_positioning(Context(store, cfg, END, today=END), region, params).signal

    rng = np.random.default_rng(1)
    base = rng.normal(0, 5, weeks)
    mid_rising = base.copy()
    mid_rising[-14:] = np.linspace(-6, 3, 14)       # building longs, not extreme
    assert signal(mid_rising) > 0.2
    mid_falling = base.copy()
    mid_falling[-14:] = np.linspace(6, -2, 14)
    assert signal(mid_falling) < -0.2
    crowded = base.copy()
    crowded[-14:] = 30.0                             # flat at a 3-year extreme long
    assert signal(crowded) < -0.2                    # pure contrarian tilt
    washed_out = base.copy()
    washed_out[-14:] = -30.0
    assert signal(washed_out) > 0.2
    chasing = base.copy()
    chasing[-14:] = np.linspace(5, 30, 14)           # rising INTO an extreme: trend is damped
    still_room = base.copy()
    still_room[-14:] = np.linspace(-22, 3, 14)       # same rise, mid-range
    assert signal(chasing) < signal(still_room)


# -------------------------------------------------------------- point in time
def test_future_observations_do_not_change_past_scores(session):
    past = END - timedelta(days=210)
    scoring.run(session, past)
    snap = {(s.region, s.horizon): (s.score, s.coverage) for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    # rewrite everything after `past`, including recent news
    session.execute(update(Observation).where(Observation.obs_date > past).values(value=Observation.value * 1.7 + 3))
    session.execute(update(NewsRegionSentiment).values(score=1.0))
    session.commit()
    scoring.run(session, past)
    again = {(s.region, s.horizon): (s.score, s.coverage) for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    assert snap == again


def test_publication_lag_hides_unpublished_monthly_data(session):
    past = END - timedelta(days=210)
    scoring.run(session, past)
    snap = {(s.region, s.horizon): s.score for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    # a monthly reference period that started < 40 days before `past` is not yet published
    session.execute(update(Observation).where(Observation.series_id.in_(["US.CPI_YOY", "US.UNEMPLOYMENT", "US.TRADE_BALANCE"]),
                                              Observation.obs_date > past - timedelta(days=39), Observation.obs_date <= past)
                    .values(value=Observation.value + 5))
    session.commit()
    scoring.run(session, past)
    again = {(s.region, s.horizon): s.score for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    assert snap == again
    # ...whereas an already published observation does matter
    session.execute(update(Observation).where(Observation.series_id == "US.UNEMPLOYMENT",
                                              Observation.obs_date > past - timedelta(days=75), Observation.obs_date <= past - timedelta(days=41))
                    .values(value=Observation.value + 5))
    session.commit()
    scoring.run(session, past)
    final = {(s.region, s.horizon): s.score for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    assert final[("US", "short")] < snap[("US", "short")]


def test_historical_dates_without_news_lose_coverage(session):
    past = END - timedelta(days=210)
    scoring.run(session, past)
    row = session.execute(select(ScoreComponent).where(ScoreComponent.as_of == past, ScoreComponent.region == "US",
                                                       ScoreComponent.horizon == "short", ScoreComponent.component == "news_sentiment")).scalar_one()
    assert row.signal is None
    score = session.execute(select(Score).where(Score.as_of == past, Score.region == "US", Score.horizon == "short")).scalar_one()
    assert score.coverage == pytest.approx(0.8)


# ------------------------------------------------------------------- backfill
def test_backfill_row_counts(session):
    start = END - timedelta(days=70)
    n = scoring.backfill(session, start, END, step_days=7)
    assert n == 11 * N_SCORED * 3
    assert session.scalar(select(func.count()).select_from(Score)) == n
    assert session.scalar(select(func.count(func.distinct(Score.as_of)))) == 11
    assert session.scalar(select(func.count(func.distinct(ScoreComponent.as_of)))) == 11
    assert session.scalar(select(func.count()).select_from(Conclusion)) == 11 * N_SCORED
    # re-running does not duplicate
    assert scoring.backfill(session, start, END, step_days=7) == n
    assert session.scalar(select(func.count()).select_from(Score)) == n
    # an end date off the weekly grid is still scored
    assert scoring.backfill(session, start, END - timedelta(days=3), step_days=7) == 11 * N_SCORED * 3


def test_backfill_store_detail_latest(session):
    cfg = copy.deepcopy(load_config())
    cfg["backfill"]["store_detail"] = "latest"
    n = scoring.backfill(session, END - timedelta(days=28), END, config=cfg)
    assert n == 5 * N_SCORED * 3
    assert session.scalar(select(func.count(func.distinct(ScoreComponent.as_of)))) == 1
    assert session.scalar(select(func.count()).select_from(Conclusion)) == N_SCORED


def test_backfill_matches_run(session):
    past = END - timedelta(days=14)
    scoring.backfill(session, END - timedelta(days=28), END)
    a = {(s.region, s.horizon): s.score for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    scoring.run(session, past)
    b = {(s.region, s.horizon): s.score for s in session.execute(select(Score).where(Score.as_of == past)).scalars()}
    assert a == b and len(a) == N_SCORED * 3


# --------------------------------------------------------------------- config
def test_yaml_weight_change_changes_result(session):
    base_cfg = load_config()
    before = live_scores(session, base_cfg)
    cfg = copy.deepcopy(base_cfg)
    cfg["horizons"]["short"]["components"]["price_momentum"]["weight"] = 2.0
    after = live_scores(session, cfg)
    changed = [r for r in before if abs(before[r]["short"].score - after[r]["short"].score) > 0.5]
    assert len(changed) >= N_SCORED - 2
    us = after["US"]["short"]
    mom = comp(after, "US", "short", "price_momentum")
    assert abs(mom.contribution) > abs(comp(before, "US", "short", "price_momentum").contribution) or mom.signal == 0
    assert sum(c.contribution for c in us.components) == pytest.approx(us.score, abs=1e-6)
    # weight 0 removes the component entirely
    cfg["horizons"]["short"]["components"]["price_momentum"]["weight"] = 0
    off = live_scores(session, cfg)
    assert "price_momentum" not in {c.component for c in off["US"]["short"].components}
    assert before["US"]["medium"].score == after["US"]["medium"].score  # other horizons untouched


def test_risk_beta_is_configurable(session):
    cfg = copy.deepcopy(load_config())
    base = comp(live_scores(session, cfg), "AU", "short", "risk_sentiment").signal
    cfg["risk_beta"]["AUD"] = -1.0
    assert comp(live_scores(session, cfg), "AU", "short", "risk_sentiment").signal == pytest.approx(-base, abs=1e-3)


def test_yaml_file_override(session, tmp_path, monkeypatch):
    import yaml

    cfg = copy.deepcopy(load_config())
    cfg["horizons"]["long"]["components"] = {"trend_valuation": cfg["horizons"]["long"]["components"]["trend_valuation"]}
    path = tmp_path / "scoring.yaml"
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("SCORING_CONFIG", str(path))
    scoring.run(session, END)
    names = set(session.execute(select(ScoreComponent.component).where(ScoreComponent.horizon == "long")).scalars())
    assert names == {"trend_valuation"}


def test_labels():
    labels = load_config()["labels"]
    assert [label_for(x, labels) for x in (80, 50, 49.9, 20, 19.9, 0, -19.9, -20, -49.9, -50, -90)] == [
        "Strong Bullish", "Strong Bullish", "Bullish", "Bullish", "Neutral", "Neutral", "Neutral",
        "Bearish", "Bearish", "Strong Bearish", "Strong Bearish"]
    assert label_for(30, {**labels, "bullish": 40}) == "Neutral"


# -------------------------------------------------------------------- helpers
def test_signal_helpers():
    assert squash(0, 1) == 0 and -1 <= squash(-50, 1) <= -0.999 and squash(1e9, 1) <= 1.0
    assert squash(1, 1) == pytest.approx(0.7616, abs=1e-4)
    assert blend([(None, 1.0), (0.5, 1.0), (-0.5, 3.0)]) == pytest.approx(-0.25)
    assert blend([(None, 1.0)]) is None
    assert parse_number("250K") == 250_000 and parse_number("3.2%") == 3.2 and parse_number("-0.1") == -0.1
    assert parse_number("1,234.5") == 1234.5 and parse_number("") is None and parse_number("n/a") is None
    assert parse_number("<0.1%") == 0.1 and parse_number("1.2M") == 1_200_000


def test_conclusion_mentions_divergence(session):
    from app.scoring.conclusions import build_conclusion
    from app.scoring.engine import HorizonScore

    cfg = load_config()
    result = {
        "short": HorizonScore("short", 35.0, "Bullish", 1.0, []),
        "medium": HorizonScore("medium", -5.0, "Neutral", 1.0, []),
        "long": HorizonScore("long", -42.0, "Bearish", 0.4, []),
    }
    headline, text = build_conclusion(REGION_BY_CODE["JP"], result, cfg)
    assert headline == "JPY: bullish short-term, neutral medium-term, bearish long-term"
    assert "bounce inside a longer-term downtrend" in text
    assert "coverage is thin" in text and "long-term (40% of inputs)" in text


def test_daily_policy_rate_counts_only_actual_moves():
    """A daily step series (e.g. the Fed funds target) must not flood the
    surprise average with hundreds of zero 'releases'."""
    from app.scoring.components.surprise import data_surprise

    cfg = load_config()
    params = cfg["horizons"]["short"]["components"]["data_surprise"]
    days = np.arange(END.toordinal() - 500, END.toordinal() + 1, dtype=np.int64)
    values = np.full(len(days), 4.5)
    store = DataStore()
    store.series["US.POLICY_RATE"] = TS("US.POLICY_RATE", days, values.copy(), "D", 0)
    flat = data_surprise(Context(store, cfg, END, today=END), REGION_BY_CODE["US"], params)
    assert flat.signal is None  # nothing happened: no information, not a zero reading
    values[-5:] = 4.25  # a 25bp cut five days ago
    store = DataStore()
    store.series["US.POLICY_RATE"] = TS("US.POLICY_RATE", days, values, "D", 0)
    cut = data_surprise(Context(store, cfg, END, today=END), REGION_BY_CODE["US"], params)
    assert cut.signal < -0.3 and "from 1 releases" in cut.raw


def test_monthly_commodity_series_still_gives_momentum(session):
    """Copper is monthly in the free data; the commodity component must cope."""
    rows = session.execute(select(Observation.obs_date, Observation.value).where(Observation.series_id == "GLOBAL.COPPER")).all()
    session.execute(delete(Observation).where(Observation.series_id == "GLOBAL.COPPER"))
    monthly = {}
    for d, v in rows:
        monthly.setdefault(date(d.year, d.month, 1), v)
    upsert(session, Observation, [{"series_id": "GLOBAL.COPPER", "obs_date": d, "value": v} for d, v in monthly.items()])
    session.execute(update(SeriesMeta).where(SeriesMeta.series_id == "GLOBAL.COPPER").values(frequency="M"))
    session.commit()
    c = comp(live_scores(session), "AU", "medium", "commodity_china")
    assert c.signal is not None and "copper" in c.raw_display
