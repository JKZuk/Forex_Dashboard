"""API tests: FastAPI TestClient on a temporary SQLite database, no network.

The engine in app.db is swapped for a temp-file SQLite engine per test module, so
these tests never touch the configured (MySQL) database. The TestClient is not
used as a context manager, so the app lifespan (init_db / scheduler) never runs.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db, pipeline, regions as reg  # noqa: E402
from app.ingest import SOURCES  # noqa: E402
from app.models import CotPosition, RefreshRun, Score  # noqa: E402

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo", BACKEND / "scripts" / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_client(tmp_path_factory, name: str, seed: bool):
    from fastapi.testclient import TestClient

    from app import models  # noqa: F401
    from app.main import app

    path = tmp_path_factory.mktemp(name) / "test.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True)
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    if seed:
        session = factory()
        _load_seed_module().seed(session)
        session.commit()
        session.close()
    return TestClient(app), engine, factory


@pytest.fixture(scope="module")
def _engines(tmp_path_factory):
    saved = (db._engine, db._SessionLocal)
    seeded = _make_client(tmp_path_factory, "seeded", True)
    empty = _make_client(tmp_path_factory, "empty", False)
    yield {"seeded": seeded, "empty": empty}
    seeded[1].dispose()
    empty[1].dispose()
    db._engine, db._SessionLocal = saved


@pytest.fixture()
def client(_engines):
    c, engine, factory = _engines["seeded"]
    db._engine, db._SessionLocal = engine, factory
    return c


@pytest.fixture()
def empty_client(_engines):
    c, engine, factory = _engines["empty"]
    db._engine, db._SessionLocal = engine, factory
    return c


def get(c, path: str, status: int = 200):
    r = c.get(path)
    assert r.status_code == status, f"{path}: {r.status_code} {r.text[:300]}"
    return r.json()


# --- seeded database ---------------------------------------------------------
def test_health(client):
    body = get(client, "/api/health")
    assert body["status"] == "ok" and body["database"] == "ok" and body["has_data"] is True
    assert body["dialect"] == "sqlite"
    assert ISO_UTC.match(body["server_time"])


def test_regions_come_from_registry(client):
    body = get(client, "/api/regions")
    assert [r["code"] for r in body] == reg.REGION_CODES
    by_code = {r["code"]: r for r in body}
    assert by_code["US"]["fx_series_id"] == "FX.USD_INDEX"
    assert by_code["EZ"]["fx_series_id"] == "FX.EURUSD"
    assert by_code["CN"]["group"] == "influence" and by_code["CH"]["group"] == "haven"
    assert {r["code"] for r in body if r["group"] == "commodity"} == {"AU", "CA", "NZ"}


def test_overview(client):
    body = get(client, "/api/overview")
    assert body["has_data"] is True
    assert body["horizons"] == ["short", "medium", "long"]
    assert [r["region"]["code"] for r in body["regions"]] == reg.REGION_CODES
    for item in body["regions"]:
        assert set(item["scores"]) == {"short", "medium", "long"}
        for s in item["scores"].values():
            assert -100 <= s["score"] <= 100
            assert 0 <= s["coverage"] <= 1
            assert s["label"]
            assert s["change_1w"] is not None
            # the comparison row is about a week old
            gap = date.fromisoformat(s["as_of"]) - date.fromisoformat(s["previous_as_of"])
            assert 4 <= gap.days <= 14
        assert item["headline"]
        price = item["price"]
        assert price["value"] > 0
        for key in ("change_1d_pct", "change_1w_pct", "change_1m_pct"):
            assert isinstance(price[key], float)


def test_overview_week_change_matches_database(client):
    session = db.SessionLocal()
    rows = sorted(
        (s for s in session.query(Score).filter_by(region="JP", horizon="short")), key=lambda s: s.as_of
    )
    session.close()
    latest = rows[-1]
    target = latest.as_of - timedelta(days=7)
    candidates = [r for r in rows[:-1] if 4 <= (latest.as_of - r.as_of).days <= 14]
    prev = min(candidates, key=lambda r: abs((r.as_of - target).days))
    body = get(client, "/api/overview")
    jp = next(r for r in body["regions"] if r["region"]["code"] == "JP")["scores"]["short"]
    assert jp["score"] == pytest.approx(latest.score, abs=0.05)
    assert jp["change_1w"] == pytest.approx(latest.score - prev.score, abs=0.11)


def test_region_detail(client):
    body = get(client, "/api/regions/JP")
    assert body["region"]["code"] == "JP" and body["has_data"] is True
    assert [p["horizon"] for p in body["horizons"]] == ["short", "medium", "long"]
    for panel in body["horizons"]:
        comps = panel["components"]
        assert comps, "every horizon has drivers in the demo seed"
        with_data = [c for c in comps if c["has_data"]]
        # sorted by |contribution| desc, no-data rows last
        mags = [abs(c["contribution"]) for c in with_data]
        assert mags == sorted(mags, reverse=True)
        assert all(not c["has_data"] for c in comps[len(with_data):])
        # contributions add up to the score
        assert sum(c["contribution"] for c in comps) == pytest.approx(panel["score"]["score"], abs=0.6)
    assert body["conclusion"]["headline"] and body["conclusion"]["text"]
    keys = [i["indicator"] for i in body["indicators"]]
    assert "CPI_YOY" in keys and "TRADE_BALANCE" in keys
    row = next(i for i in body["indicators"] if i["indicator"] == "CPI_YOY")
    assert row["previous_value"] is not None and row["latest_date"] > row["previous_date"]
    assert row["change"] == pytest.approx(row["latest_value"] - row["previous_value"])
    assert row["bullish_when"] == "higher" and len(row["spark"]) > 2
    assert {t["indicator"] for t in body["trade"]} >= {"TRADE_BALANCE", "EXPORTS_YOY", "IMPORTS_YOY"}
    assert all(len(t["points"]) > 12 for t in body["trade"])
    assert body["news"] and all(n["region_score"] is not None for n in body["news"])
    assert all(ISO_UTC.match(n["published_at"]) for n in body["news"])
    assert all(e["region"] == "JP" for e in body["events"])


def test_region_detail_no_data_components_and_lookup(client):
    body = get(client, "/api/regions/cn")  # case-insensitive
    missing = [c for p in body["horizons"] for c in p["components"] if not c["has_data"]]
    assert missing and all(c["signal"] is None and c["contribution"] == 0 for c in missing)
    assert get(client, "/api/regions/EUR")["region"]["code"] == "EZ"  # currency code accepted
    get(client, "/api/regions/XX", status=404)


def test_score_history(client):
    body = get(client, "/api/history/scores?region=EZ")
    assert set(body["series"]) == {"short", "medium", "long"}
    points = body["series"]["short"]
    assert len(points) >= 100  # ~2 years of weekly history
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)

    start = (date.today() - timedelta(days=90)).isoformat()
    body = get(client, f"/api/history/scores?region=EZ&horizons=long&start={start}")
    assert list(body["series"]) == ["long"]
    assert 10 <= len(body["series"]["long"]) <= 15
    assert all(p["date"] >= start for p in body["series"]["long"])
    get(client, "/api/history/scores?region=EZ&horizons=weekly", status=400)
    get(client, "/api/history/scores?region=ZZ", status=404)


def test_price_history(client):
    start = (date.today() - timedelta(days=30)).isoformat()
    body = get(client, f"/api/history/price?region=JP&start={start}")
    assert body["series_id"] == "FX.JPYUSD" and body["found"] is True
    assert 15 <= len(body["points"]) <= 23
    assert get(client, "/api/history/price?region=US")["series_id"] == "FX.USD_INDEX"


def test_heatmap(client):
    body = get(client, "/api/heatmap")
    assert [c["currency"] for c in body["currencies"]] == [r.currency for r in reg.REGIONS]
    scores = {c["currency"]: c["scores"] for c in body["currencies"]}
    for horizon in body["horizons"]:
        ideas = body["pairs"][horizon]
        assert len(ideas) == 9 * 8 // 2
        diffs = [i["differential"] for i in ideas]
        assert diffs == sorted(diffs, reverse=True) and min(diffs) >= 0
        for idea in ideas:
            base, quote = idea["pair"].split("/")
            assert {base, quote} == {idea["strong"], idea["weak"]}
            assert idea["direction"] == ("long" if idea["strong"] == base else "short")
            assert idea["differential"] == pytest.approx(
                scores[idea["strong"]][horizon] - scores[idea["weak"]][horizon], abs=0.11
            )
    names = {i["pair"] for i in body["pairs"]["short"]}
    assert {"EUR/USD", "USD/JPY", "AUD/NZD", "USD/CAD", "GBP/JPY"} <= names


def test_cot_summary(client):
    body = get(client, "/api/cot")
    assert body["has_data"] is True
    rows = {r["currency"]: r for r in body["rows"]}
    assert "CNY" not in rows and {"USD", "EUR", "JPY", "GBP"} <= set(rows)

    session = db.SessionLocal()
    hist = sorted(session.query(CotPosition).filter_by(currency="JPY"), key=lambda p: p.report_date)
    session.close()
    cur = hist[-1]
    window = [h for h in hist if h.report_date >= cur.report_date - timedelta(days=365 * 3 + 7)]
    expected_pct = sum(1 for h in window if h.net_pct_oi < cur.net_pct_oi) / len(window) * 100
    jpy = rows["JPY"]
    assert jpy["net"] == cur.noncomm_net
    assert jpy["change_1w"] == pytest.approx(cur.noncomm_net - hist[-2].noncomm_net)
    assert jpy["change_4w"] == pytest.approx(cur.noncomm_net - hist[-5].noncomm_net)
    assert jpy["percentile_3y"] == pytest.approx(expected_pct, abs=1.0)
    assert jpy["percentile_weeks"] == len(window)
    for row in rows.values():
        pct = row["percentile_3y"]
        expected = "long" if pct >= 90 else "short" if pct <= 10 else None
        assert row["extreme"] == expected
        assert len(row["spark"]) == 52


def test_cot_history(client):
    body = get(client, "/api/cot/eur")
    assert body["currency"] == "EUR" and len(body["points"]) > 150
    assert get(client, "/api/cot/EZ")["currency"] == "EUR"  # region code accepted
    start = (date.today() - timedelta(days=60)).isoformat()
    assert 7 <= len(get(client, f"/api/cot/EUR?start={start}")["points"]) <= 10
    assert get(client, "/api/cot/XXX")["points"] == []


def test_calendar_filters(client):
    body = get(client, "/api/calendar?days=21")
    assert body["total"] == len(body["events"]) > 0
    times = [e["event_time"] for e in body["events"]]
    assert times == sorted(times) and all(ISO_UTC.match(t) for t in times)
    assert ISO_UTC.match(body["start"]) and ISO_UTC.match(body["end"])
    today_start = datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")
    assert min(times) >= today_start

    high = get(client, "/api/calendar?days=21&impact=high")
    assert high["events"] and all(e["impact"] == "high" for e in high["events"])
    regional = get(client, "/api/calendar?days=28&region=US,jp&impact=high,medium")
    assert all(e["region"] in ("US", "JP") and e["impact"] != "low" for e in regional["events"])
    past = get(client, "/api/calendar?days=1&past_days=3")
    assert any(e["actual"] for e in past["events"])
    assert any(not e["time_known"] for e in get(client, "/api/calendar?days=28")["events"])
    get(client, "/api/calendar?impact=huge", status=400)


def test_news_feed(client):
    body = get(client, "/api/news?limit=10")
    assert body["total"] == 70 and len(body["items"]) == 10
    stamps = [n["published_at"] for n in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert all(n["regions"] for n in body["items"])
    page2 = get(client, "/api/news?limit=10&offset=10")
    assert not {n["id"] for n in body["items"]} & {n["id"] for n in page2["items"]}

    au = get(client, "/api/news?region=AU&limit=200")
    assert au["total"] == len(au["items"]) > 0
    for n in au["items"]:
        assert n["region_score"] == next(s["score"] for s in n["regions"] if s["region"] == "AU")
    glob = get(client, "/api/news?region=GLOBAL")
    assert glob["items"] and all(any(s["region"] == "GLOBAL" for s in n["regions"]) for n in glob["items"])
    cb = get(client, "/api/news?kind=central_bank")
    assert cb["items"] and all(n["kind"] == "central_bank" for n in cb["items"])


def test_series_and_global(client):
    body = get(client, "/api/series/JP.TRADE_BALANCE")
    assert body["found"] is True and body["unit"] == "JPY bn" and len(body["points"]) > 24
    assert ISO_UTC.match(body["last_updated"])
    missing = get(client, "/api/series/XX.NOT_A_SERIES")
    assert missing["found"] is False and missing["points"] == []
    gauges = get(client, "/api/global")
    assert {g["series_id"] for g in gauges} >= {"GLOBAL.VIX", "GLOBAL.OIL", "GLOBAL.GOLD"}


def test_refresh_status(client):
    body = get(client, "/api/refresh/status")
    assert body["running"] is False
    assert body["sources"] == SOURCES
    assert [j["job"] for j in body["jobs"]] == SOURCES + ["scoring"]
    jobs = {j["job"]: j for j in body["jobs"]}
    # latest run per job: the seed's news job fails after an earlier success
    assert jobs["news"]["status"] == "error" and "demo failure" in jobs["news"]["message"]
    assert jobs["news"]["last_success_at"] < jobs["news"]["started_at"]
    assert jobs["scoring"]["status"] == "ok"
    assert ISO_UTC.match(body["last_success_at"]) and ISO_UTC.match(body["server_time"])
    assert body["last_success_at"] == max(j["last_success_at"] for j in body["jobs"])


def test_refresh_status_flags_interrupted_and_external_runs(client):
    session = db.SessionLocal()
    stale = RefreshRun(job="cot", started_at=datetime.utcnow() - timedelta(hours=3), status="running")
    session.add(stale)
    session.commit()
    try:
        body = get(client, "/api/refresh/status")
        cot = next(j for j in body["jobs"] if j["job"] == "cot")
        assert cot["status"] == "interrupted" and body["running"] is False

        stale.started_at = datetime.utcnow() - timedelta(seconds=20)  # e.g. CLI refresh elsewhere
        session.merge(stale)
        session.commit()
        body = get(client, "/api/refresh/status")
        assert body["running"] is True
        assert next(j for j in body["jobs"] if j["job"] == "cot")["status"] == "running"
    finally:
        session.delete(session.merge(stale))
        session.commit()
        session.close()


def test_post_refresh(client, monkeypatch):
    calls: list = []

    def fake_background(sources=None):
        calls.append(sources)
        return len(calls) == 1  # second call: already running

    monkeypatch.setattr(pipeline, "refresh_in_background", fake_background)
    monkeypatch.setattr(pipeline, "is_running", lambda: True)

    r = client.post("/api/refresh")
    assert r.status_code == 202
    assert r.json() == {
        "started": True, "running": True, "sources": SOURCES, "message": "Refresh started.",
    }
    r = client.post("/api/refresh", json={"sources": ["fx_prices", "cot"]})
    assert r.status_code == 202
    assert r.json()["started"] is False and r.json()["running"] is True
    assert r.json()["sources"] == ["fx_prices", "cot"]
    assert calls == [None, ["fx_prices", "cot"]]

    r = client.post("/api/refresh", json={"sources": ["nope"]})
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    assert len(calls) == 2  # nothing started for an invalid source


def test_unknown_api_path_is_json_404(client):
    body = get(client, "/api/does-not-exist", status=404)
    assert "detail" in body


def test_no_data_label_is_not_neutral(client):
    """Scoring writes label "No Data" (score 0, coverage 0): must not read as Neutral."""
    session = db.SessionLocal()
    row = session.query(Score).filter_by(region="NZ", horizon="long").order_by(Score.as_of.desc()).first()
    saved = (row.score, row.label, row.coverage)
    row.score, row.label, row.coverage = 0.0, "No Data", 0.0
    session.commit()
    try:
        nz = next(r for r in get(client, "/api/overview")["regions"] if r["region"]["code"] == "NZ")
        assert nz["scores"]["long"]["no_data"] is True and nz["scores"]["long"]["change_1w"] is None
        assert nz["scores"]["short"]["no_data"] is False
        heat = get(client, "/api/heatmap")
        cur = next(c for c in heat["currencies"] if c["currency"] == "NZD")
        assert cur["scores"]["long"] is None and cur["labels"]["long"] == "No Data"
        assert all("NZD" not in i["pair"] for i in heat["pairs"]["long"])
        assert any("NZD" in i["pair"] for i in heat["pairs"]["short"])
        hist = get(client, "/api/history/scores?region=NZ&horizons=long")["series"]["long"]
        assert hist[-1]["date"] < row.as_of.isoformat()
        panel = get(client, "/api/regions/NZ")["horizons"][2]
        assert panel["score"]["no_data"] is True
    finally:
        row.score, row.label, row.coverage = saved
        session.commit()
        session.close()


# --- empty database: every endpoint answers with a clean empty shape ----------
def test_empty_database(empty_client):
    c = empty_client
    assert get(c, "/api/health")["has_data"] is False
    assert len(get(c, "/api/regions")) == len(reg.REGIONS)

    overview = get(c, "/api/overview")
    assert overview["has_data"] is False and overview["as_of"] is None
    assert len(overview["regions"]) == len(reg.REGIONS)
    assert all(r["scores"] == {} and r["price"] is None and r["headline"] is None for r in overview["regions"])

    detail = get(c, "/api/regions/AU")
    assert detail["has_data"] is False and detail["conclusion"] is None and detail["price"] is None
    assert detail["indicators"] == [] and detail["trade"] == [] and detail["news"] == [] and detail["events"] == []
    assert all(p["score"] is None and p["components"] == [] for p in detail["horizons"])

    assert all(v == [] for v in get(c, "/api/history/scores?region=AU")["series"].values())
    price = get(c, "/api/history/price?region=AU")
    assert price["found"] is False and price["points"] == []

    heat = get(c, "/api/heatmap")
    assert heat["has_data"] is False and all(v == [] for v in heat["pairs"].values())
    assert all(set(cur["scores"].values()) == {None} for cur in heat["currencies"])

    assert get(c, "/api/cot") == {"has_data": False, "report_date": None, "rows": []}
    assert get(c, "/api/cot/EUR")["points"] == []
    assert get(c, "/api/calendar")["events"] == []
    assert get(c, "/api/news") == {"total": 0, "limit": 50, "offset": 0, "items": []}
    assert get(c, "/api/global") == []

    status = get(c, "/api/refresh/status")
    assert status["running"] is False and status["last_success_at"] is None
    assert [j["status"] for j in status["jobs"]] == ["never"] * (len(SOURCES) + 1)
