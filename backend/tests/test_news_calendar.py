"""Offline tests for news sentiment (lexicon, tagging, feed parsing) and calendar parsers.

No network: everything runs on inline fixtures and an in-memory SQLite database.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

from datetime import date, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import Base, upsert  # noqa: E402
from app.ingest import calendar as cal  # noqa: E402
from app.ingest import news  # noqa: E402
from app.models import CalendarEvent, NewsItem, NewsRegionSentiment  # noqa: E402
from app.sentiment import GLOBAL_REGION, LexiconEngine, TextItem, get_engine, is_macro_relevant, tag_regions  # noqa: E402

ENGINE = LexiconEngine()


def score(title: str, summary: str = "", **kw) -> dict[str, float]:
    result = ENGINE.score(TextItem(title, summary, **kw))
    out = dict(result.regions)
    if result.risk is not None:
        out[GLOBAL_REGION] = result.risk
    return out


# ------------------------------------------------------------------ directional lexicon
# (headline, {region: expected sign}); +1 = bullish for that currency, 0 = neutral.
HEADLINES: list[tuple[str, dict[str, int]]] = [
    # hawkish / dovish policy
    ("Fed signals rate cuts as inflation cools", {"US": -1}),
    ("BoE raises interest rates by 25 basis points", {"UK": 1}),
    ("RBNZ hikes OCR to 2.75%", {"NZ": 1}),
    ("SNB cuts rates to zero", {"CH": -1}),
    ("ECB delivers 25bp cut, signals more easing ahead", {"EZ": -1}),
    ("BoJ's Ueda says rate hike possible if wages keep rising", {"JP": 1}),
    ("Bank of Canada Raised Rates to Mitigate Inflation Risk, Minutes Say", {"CA": 1}),
    ("Fed's Hammack: Gradual rate hikes are the best way to get inflation to target", {"US": 1}),
    ("NZ Inflation Expectations Are Worrying, RBNZ's Conway Says", {"NZ": 1}),
    # negations and faders
    ("ECB's Lagarde: not ready to cut rates yet", {"EZ": 1}),
    ("Fed's Waller: no rush to cut rates", {"US": 1}),
    ("RBA rules out rate hike", {"AU": -1}),
    ("Traders pare bets on Fed rate cuts", {"US": 1}),
    ("Bank of Japan ends negative rates", {"JP": 1}),
    ("Bank of England policymaker refuses to rule out back-to-back rate rises", {"UK": 1}),
    ("Bank of Canada Officials Say Uncertainty Doesn’t Rule Out Hikes", {"CA": 1}),
    ("Weak jobs data and election limbo cloud December OCR hike", {"NZ": -1}),
    ("Interest rates hiked to 4% in first increase in over three years", {}),  # no region named
    ("Eurozone inflation fails to cool", {"EZ": 1}),
    ("UK avoids recession as GDP grows 0.1%", {"UK": 1}),
    # data beats / misses
    ("US inflation hotter than expected in October", {"US": 1}),
    ("UK retail sales miss forecasts", {"UK": -1}),
    ("China exports rise more than expected", {"CN": 1}),
    ("US economy adds 250,000 jobs, beating forecasts", {"US": 1}),
    ("German GDP contracts in third quarter", {"EZ": -1}),
    ("Japan's economy shrinks for second quarter", {"JP": -1}),
    ("Australian employment unexpectedly falls", {"AU": -1}),
    ("US October core CPI +0.3% vs +0.2% expected", {"US": 1}),
    ("Consumer prices rose 0.4% in October, more than expected, complicating Fed plans", {"US": 1}),
    ("Japan household spending drops more than expected in September", {"JP": -1}),
    # polarity-inverting subjects
    ("US unemployment rate jumps to 4.6%", {"US": -1}),
    ("Jobless claims fall to lowest level since mid-May", {"US": 1}),
    ("US initial jobless claims 194K vs 210K estimate", {"US": 1}),
    ("UK unemployment rose less than expected", {"UK": 1}),
    ("Canada's trade deficit widens", {"CA": -1}),
    ("China's trade surplus narrows in October", {"CN": -1}),
    # currency price action and pairs
    ("Yen slides as BoJ holds rates", {"JP": -1}),
    ("Dollar jumps against yen after hot CPI", {"US": 1, "JP": -1}),
    ("EUR/USD falls after strong payrolls", {"EZ": -1, "US": 1}),
    ("Pound falls against dollar after weak retail sales", {"UK": -1, "US": 1}),
    ("USD/CHF Rises as Strong US Jobs Data Boosts Fed Bets", {"US": 1, "CH": -1}),
    ("US Dollar vs Swiss Franc holds gains as bullish bias continues", {"US": 1, "CH": -1}),
    ("Australian dollar slips as iron ore prices drop", {"AU": -1}),
    ("The Canadian Dollar is missing out on a weaker US Dollar", {"US": -1, "CA": 0}),
    # neutral
    ("Federal Reserve issues FOMC statement", {"US": 0}),
    ("European Central Bank keeps key interest rate on hold at 2.5%", {"EZ": 0}),
    # global risk tone
    ("Stocks tumble as trade war fears escalate", {"GLOBAL": -1}),
    ("Wall Street rallies to record high", {"GLOBAL": 1}),
    ("Nasdaq drops into correction territory", {"GLOBAL": -1}),
]


@pytest.mark.parametrize("headline,expected", HEADLINES, ids=[h[0][:50] for h in HEADLINES])
def test_directional_headlines(headline: str, expected: dict[str, int]) -> None:
    got = score(headline)
    for region, sign in expected.items():
        value = got.get(region, 0.0)
        if sign > 0:
            assert value > 0.1, (headline, got)
        elif sign < 0:
            assert value < -0.1, (headline, got)
        else:
            assert abs(value) < 0.1, (headline, got)
    assert all(-1.0 <= v <= 1.0 for v in got.values())


def test_direction_differs_from_tone() -> None:
    # Positive-sounding dovish news is still bearish for the currency.
    result = ENGINE.score(TextItem("Stocks rally as Fed signals rate cuts, boosting optimism"))
    assert result.tone > 0
    assert result.regions["US"] < 0


def test_hits_explain_the_score() -> None:
    result = ENGINE.score(TextItem("US unemployment rate jumps to 4.6%"))
    assert any("inverted subject" in h for h in result.hits)


def test_headline_outweighs_summary() -> None:
    title_only = score("Bank of England raises rates")["UK"]
    with_noise = score("Bank of England raises rates", "Some economists said growth is weak and unemployment rose.")["UK"]
    assert title_only > 0 and with_noise > 0


def test_roundup_summary_is_ignored() -> None:
    got = score("European news wrap: markets await US CPI", "Eurozone investor confidence weakens as growth fades.")
    assert abs(got.get("EZ", 0.0)) < 0.1


def test_geopolitics_needs_market_context() -> None:
    assert GLOBAL_REGION not in score("Couple reunited after the war in a small village")
    assert score("Oil and stocks slide as war fears grow")[GLOBAL_REGION] < 0


# ------------------------------------------------------------------ region tagging
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Powell says the Fed is in no hurry", ["US"]),
        ("Greenback firms ahead of FOMC", ["US"]),
        ("Lagarde: ECB will stay data dependent", ["EZ"]),
        ("Sterling steadies after Bailey remarks", ["UK"]),
        ("Kiwi dips before RBNZ decision", ["NZ"]),
        ("Loonie gains as Macklem speaks", ["CA"]),
        ("Aussie rallies after RBA minutes", ["AU"]),
        ("Yuan fixing: PBOC sets midpoint", ["CN"]),
        ("Swiss franc climbs, SNB watches", ["CH"]),
        ("EUR/JPY climbs to fresh high", ["EZ", "JP"]),
        ("Australian dollar and New Zealand dollar slip", ["AU", "NZ"]),  # "dollar" alone must not tag US
        ("Hong Kong dollar peg under pressure", []),
        ("Liquidators probe multi-million-dollar sale", []),
        ("Let us know if you are fed up with this", []),  # case-sensitive short codes
    ],
)
def test_tag_regions(text: str, expected: list[str]) -> None:
    assert sorted(tag_regions(text)) == sorted(expected)


def test_summary_only_used_when_title_has_no_region() -> None:
    assert tag_regions("Inflation surprises again", "The Bank of Japan may respond.") == ["JP"]
    assert tag_regions("Fed holds", "Japan and Canada were also mentioned.") == ["US"]


def test_default_region_semantics() -> None:
    # central-bank feed: always tagged, even with a neutral title
    assert score("Opening remarks", default_region="US", force_default=True) == {"US": 0.0}
    # news feed: home region only when a directional rule fired
    assert score("Buyers ditch backyards for balconies", default_region="AU") == {}
    assert score("Consumer prices rose more than expected", default_region="US")["US"] > 0


def test_macro_relevance() -> None:
    assert is_macro_relevant("Japan inflation stays above target")
    assert not is_macro_relevant("Toyota to make and sell minitrucks in US next year")


def test_engine_fallback_when_finbert_missing(caplog: pytest.LogCaptureFixture) -> None:
    pytest.importorskip("feedparser")
    try:
        import transformers  # noqa: F401

        pytest.skip("transformers installed; fallback path not exercised")
    except ImportError:
        pass
    with caplog.at_level("WARNING", logger="sentiment"):
        engine = get_engine("finbert")
    assert engine.name == "lexicon"
    assert "requirements-finbert.txt" in caplog.text


# ------------------------------------------------------------------ feed parsing + rows
RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Fed signals &lt;b&gt;rate cuts&lt;/b&gt;</title>
  <link>https://example.com/a?utm_source=rss&amp;id=1#frag</link>
  <description><![CDATA[<p>Officials see <script>alert(1)</script>lower rates.</p><img src=x onerror=alert(2)>]]></description>
  <pubDate>Tue, 10 Nov 2026 13:30:00 GMT</pubDate></item>
<item><title>Fed signals rate cuts</title><link>https://example.com/a?id=1</link>
  <pubDate>Tue, 10 Nov 2026 13:31:00 GMT</pubDate></item>
<item><title>Ancient story about the ECB hiking</title><link>https://example.com/old</link>
  <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title>Local bakery wins award</title><link>https://example.com/bakery</link>
  <pubDate>Tue, 10 Nov 2026 12:00:00 GMT</pubDate></item>
<item><title>Yen slides - Example Publisher</title><link>https://example.com/yen</link>
  <source url="https://pub.example">Example Publisher</source></item>
<item><title>javascript link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""
NOW = datetime(2026, 11, 10, 18, 0, 0)


def test_parse_feed_sanitises_and_canonicalises() -> None:
    feed = news.Feed("Test", "https://example.com/rss")
    items = news.parse_feed(RSS, feed)
    assert [i.url for i in items][:2] == ["https://example.com/a?id=1", "https://example.com/a?id=1"]
    first = items[0]
    assert first.title == "Fed signals rate cuts"
    assert "<" not in first.summary and "alert" not in first.summary and "lower rates" in first.summary
    assert first.published == datetime(2026, 11, 10, 13, 30)
    yen = next(i for i in items if "Yen" in i.title)
    assert yen.title == "Yen slides" and yen.published is None  # publisher suffix stripped, no date
    assert all(i.url.startswith("https://") for i in items)  # javascript: link dropped


def test_clean_text_caps_length() -> None:
    text = news.clean_text("word " * 400, 100)
    assert len(text) <= 100 and text.endswith("…")


def test_build_rows_dedupes_filters_and_scores() -> None:
    items = news.parse_feed(RSS, news.Feed("Test", "https://example.com/rss"))
    known = {news.url_hash("https://example.com/yen"): datetime(2026, 11, 9, 8, 0)}
    news_rows, region_rows = news.build_rows(items, ENGINE, NOW, 60, known)
    titles = [r["title"] for r in news_rows]
    assert titles == ["Fed signals rate cuts", "Yen slides"]  # dupe, stale, off-topic all gone
    fed = news_rows[0]
    assert fed["url_hash"] == news.url_hash("https://example.com/a?id=1") and len(fed["url_hash"]) == 40
    assert fed["engine"] == "lexicon" and fed["kind"] == "news"
    by_region = {(r["url_hash"], r["region"]): r["score"] for r in region_rows}
    assert by_region[(fed["url_hash"], "US")] < 0
    yen = news_rows[1]
    assert yen["published_at"] == datetime(2026, 11, 9, 8, 0)  # dateless item keeps first-seen date
    assert by_region[(yen["url_hash"], "JP")] < 0


def test_central_bank_items_always_kept() -> None:
    feed = news.Feed("SNB", "https://example.com/snb", kind="central_bank", default_region="CH")
    item = news.RawItem(feed, "2026 Carl Menger Award", "", "https://example.com/award", NOW)
    news_rows, region_rows = news.build_rows([item], ENGINE, NOW)
    assert news_rows[0]["kind"] == "central_bank"
    assert region_rows == [{"url_hash": news_rows[0]["url_hash"], "region": "CH", "score": 0.0, "published_at": NOW}]


def test_load_feeds_from_repo_config() -> None:
    feeds, cfg = news.load_feeds()
    assert len(feeds) >= 20 and cfg["max_age_days"] == 60
    assert {f.kind for f in feeds} == {"central_bank", "news"}
    assert all(f.url.startswith("https://") for f in feeds)
    cb_regions = {f.default_region for f in feeds if f.kind == "central_bank"}
    assert {"US", "EZ", "UK", "JP", "AU", "CA", "NZ", "CH"} <= cb_regions


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_news_rows_upsert_idempotent(session: Session) -> None:
    items = news.parse_feed(RSS, news.Feed("Test", "https://example.com/rss"))
    for _ in range(2):
        news_rows, region_rows = news.build_rows(items, ENGINE, NOW)
        upsert(session, NewsItem, news_rows)
        upsert(session, NewsRegionSentiment, region_rows)
    assert len(session.execute(select(NewsItem)).all()) == 2
    assert len(session.execute(select(NewsRegionSentiment)).all()) == 2


# ------------------------------------------------------------------ calendar parsers
FF_JSON = [
    {"title": "CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.6%"},
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "impact": "High", "forecast": "0.2%", "previous": "0.3%"},
    {"title": "GDP m/m", "country": "GBP", "date": "2026-11-12T02:00:00-05:00", "impact": "High", "forecast": "0.0%", "previous": "0.1%"},
    {"title": "Leading Indicators", "country": "JPY", "date": "2026-11-09T00:00:00-05:00", "impact": "Low", "forecast": "116.7%", "previous": "118.0%"},
    {"title": "Bank Holiday", "country": "CAD", "date": "2026-11-11T00:00:00-05:00", "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "G7 Meetings", "country": "All", "date": "2026-11-09T04:45:00-05:00", "impact": "Low", "forecast": "", "previous": ""},
    {"title": "FOMC Member Bowman Speaks", "country": "USD", "date": "2026-11-10T15:30:00-05:00", "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "FOMC Member Bowman Speaks", "country": "USD", "date": "2026-11-10T18:30:00-05:00", "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "Broken", "country": "USD", "date": "not-a-date", "impact": "High"},
]
WINDOW = (datetime(2026, 10, 27), datetime(2026, 12, 22))


def _myfx_item(title: str, when: str, impact: str, prev: str, cons: str, actual: str) -> str:
    return f"""<item><title>{title}</title><link>https://www.myfxbook.com/x</link><pubDate>{when}</pubDate>
<description><![CDATA[<table><tr><th>Time left</th><th>Impact</th><th>Previous</th><th>Consensus</th><th>Actual</th></tr>
<tr><td>-100 seconds</td><td><span class="sprite sprite-common sprite-{impact}-impact"></span></td>
<td>  {prev} </td><td> {cons} </td><td> {actual} </td></tr></table>]]></description></item>"""


MYFX_RSS = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>Calendar Events</title>'
    + _myfx_item("United States Inflation Rate MoM", "Tue, 10 Nov 2026 13:30 GMT", "high", "0.6%", "0.3%", "0.4%")
    + _myfx_item("United States Core Inflation Rate MoM", "Tue, 10 Nov 2026 13:30 GMT", "high", "0.3%", "0.2%", "0.3%")
    + _myfx_item("United States CPI", "Tue, 10 Nov 2026 13:30 GMT", "high", "336.94", "337.02", "337.51")
    + _myfx_item("Nepal Laxmi Puja", "Tue, 10 Nov 2026 00:00 GMT", "no", "", "", "")
    + _myfx_item("United Kingdom GDP MoM", "Thu, 12 Nov 2026 07:00 GMT", "high", "0.1%", "0.0%", "")
    + "</channel></rss>"
)


def test_parse_forexfactory() -> None:
    events = cal.parse_forexfactory(FF_JSON)
    assert len(events) == 6  # holiday, "All" and the broken row are dropped
    cpi = events[0]
    assert (cpi.region, cpi.title, cpi.impact) == ("US", "CPI m/m", "high")
    assert cpi.when == datetime(2026, 11, 10, 13, 30) and cpi.time_known  # converted to UTC
    assert (cpi.forecast, cpi.previous, cpi.actual) == ("0.3%", "0.6%", "")


def test_build_rows_ids_impact_floor_and_same_day_duplicates() -> None:
    rows = cal.build_rows(cal.parse_forexfactory(FF_JSON), *WINDOW, "medium")
    assert {r["impact"] for r in rows} == {"high", "medium"}  # the low JPY event is filtered out
    assert len({r["event_id"] for r in rows}) == len(rows) == 5  # both Bowman speeches survive
    cpi = next(r for r in rows if r["title"] == "CPI m/m")
    assert cpi["event_id"] == cal.event_id("forexfactory", "US", "CPI m/m", date(2026, 11, 10))
    assert len(cpi["event_id"]) == 40
    assert cal.build_rows(cal.parse_forexfactory(FF_JSON), datetime(2027, 1, 1), datetime(2027, 2, 1)) == []


def test_parse_myfxbook_and_match_actuals() -> None:
    actuals = cal.parse_myfxbook(MYFX_RSS)
    assert [a.region for a in actuals] == ["US", "US", "US", "UK"]  # Nepal dropped
    assert (actuals[0].previous, actuals[0].forecast, actuals[0].actual) == ("0.6%", "0.3%", "0.4%")
    rows = cal.build_rows(cal.parse_forexfactory(FF_JSON), *WINDOW)
    filled = cal.match_actuals(rows, actuals)
    by_title = {r["title"]: filled.get(r["event_id"]) for r in rows}
    assert by_title["CPI m/m"] == "0.4%" and by_title["Core CPI m/m"] == "0.3%"
    assert by_title["GDP m/m"] is None  # not released yet
    assert by_title["FOMC Member Bowman Speaks"] is None  # nothing to match on


def test_actual_survives_rerun_and_fills_in_later(session: Session) -> None:
    rows = cal.build_rows(cal.parse_forexfactory(FF_JSON), *WINDOW)
    assert cal._save(session, rows) == 5
    cpi_id = cal.event_id("forexfactory", "US", "CPI m/m", date(2026, 11, 10))
    assert session.get(CalendarEvent, cpi_id).actual == ""
    # later the source publishes the actual -> the upsert fills it in
    later = [dict(r, actual="0.4%") if r["event_id"] == cpi_id else dict(r) for r in rows]
    cal._save(session, later)
    session.expire_all()
    assert session.get(CalendarEvent, cpi_id).actual == "0.4%"
    # a re-run from a source without actuals must not blank it
    cal._save(session, [dict(r) for r in rows])
    session.expire_all()
    assert session.get(CalendarEvent, cpi_id).actual == "0.4%"
    assert len(session.execute(select(CalendarEvent)).all()) == 5  # idempotent


def test_parse_central_banks() -> None:
    banks = [
        {"bank": "Fed", "region": "US", "title": "FOMC rate decision", "time": "14:00", "tz": "America/New_York",
         "dates": [date(2026, 12, 9), "2027-06-09"], "unverified_dates": ["2027-12-08"]},
        {"bank": "BoJ", "region": "JP", "title": "BoJ rate decision", "dates": ["2026-12-18", "garbage"]},
        {"bank": "Nowhere", "region": "XX", "dates": ["2026-12-01"]},
    ]
    events = cal.parse_central_banks(banks)
    assert len(events) == 4
    dec, jun, unverified, boj = events
    assert dec.when == datetime(2026, 12, 9, 19, 0) and dec.time_known and dec.impact == "high"  # EST
    assert jun.when == datetime(2027, 6, 9, 18, 0)  # EDT
    assert unverified.title.endswith("(unverified date)")
    assert boj.when == datetime(2026, 12, 18) and not boj.time_known


def test_repo_calendar_yaml_is_complete() -> None:
    cfg = cal.load_config()
    banks = {b["region"]: b for b in cfg["central_banks"]}
    assert set(banks) == {"US", "EZ", "UK", "JP", "AU", "CA", "NZ", "CH"}
    assert all(str(b["verified_from"]).startswith("https://") for b in banks.values())
    events = cal.parse_central_banks(cfg["central_banks"])
    for region in ("US", "EZ", "UK", "JP", "AU", "CA", "CH"):
        assert sum(1 for e in events if e.region == region and e.when.year == 2027) >= 4, region
    assert all(e.when.weekday() < 5 for e in events)  # no meeting on a weekend


def test_parse_bea() -> None:
    data = {
        "Gross Domestic Product": {"release_dates": ["2026-11-25T13:30:00+00:00", "bad"]},
        "Personal Income and Outlays": {"release_dates": ["2026-11-25T13:30:00+00:00"]},
        "Marine Economy Satellite Account": {"release_dates": ["2026-11-26T13:30:00+00:00"]},
        "file_last_updated": "2026-11-10",
    }
    events = cal.parse_bea(data)
    assert [(e.title.split(" ")[0], e.impact, e.when) for e in events] == [
        ("GDP", "high", datetime(2026, 11, 25, 13, 30)),
        ("PCE", "high", datetime(2026, 11, 25, 13, 30)),
    ]


def test_parse_census() -> None:
    page = """<table><tr height="20"><td height="20"><a href="/retail">Advance Monthly Sales for Retail and Food Services</a></td>
      <td sorttable_customkey="202701150830">January 15, 2027</td><td>8:30 AM</td><td>December 2026</td>
      <td class="hiden">A202701150830</td></tr>
      <tr><td><a href="/econ/bfs/index.html">Business Formation Statistics</a></td><td>January 13, 2027</td><td>10:00 AM</td><td>December 2026</td></tr></table>"""
    events = cal.parse_census(page)
    assert len(events) == 1
    assert events[0].title == "Retail sales (advance) - December 2026" and events[0].impact == "high"
    assert events[0].when == datetime(2027, 1, 15, 13, 30)  # 08:30 EST


def test_parse_eurostat() -> None:
    ics = "\r\n".join([
        "BEGIN:VCALENDAR",
        "BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261201", "SUMMARY:Flash estimate inflation euro area",
        "X-CATEGORY:Data release\\,Euro indicators release", "END:VEVENT",
        "BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261202", "SUMMARY:Unemployment", "END:VEVENT",
        "BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261203", "SUMMARY:Asylum applications", "END:VEVENT",
        "BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261207", "SUMMARY:GDP main aggregates and employment - update", "END:VEVENT",
        "END:VCALENDAR",
    ])
    events = cal.parse_eurostat(ics)
    assert [(e.title, e.impact, e.when, e.time_known) for e in events] == [
        ("Euro area flash HICP inflation", "high", datetime(2026, 12, 1), False),
        ("Euro area unemployment rate", "medium", datetime(2026, 12, 2), False),
    ]


def test_parse_ons() -> None:
    def rel(title: str, when: str, **extra) -> dict:
        return {"description": {"title": title, "release_date": when, "cancelled": False, **extra}}

    data = {"releases": [
        rel("Consumer price inflation, UK: October 2026", "2026-11-18T07:00:00.000Z"),
        rel("Consumer price inflation, UK: October 2026 time series", "2026-11-18T07:00:00.000Z"),
        rel("UK Labour Market: November 2026", "2026-11-17T07:00:00.000Z"),
        rel("Retail sales, Great Britain: October 2026", "2026-11-20T07:00:00.000Z"),
        rel("GDP monthly estimate, UK: October 2026", "2026-12-11T07:00:00.000Z", cancelled=True),
        rel("Deaths registered weekly in England and Wales", "2026-11-11T09:30:00.000Z"),
    ]}
    events = cal.parse_ons(data)
    assert [(e.title, e.impact) for e in events] == [
        ("CPI inflation - October 2026", "high"),
        ("Labour market (unemployment, wages) - November 2026", "high"),
        ("Retail sales - October 2026", "medium"),
    ]
    assert events[0].when == datetime(2026, 11, 18, 7, 0) and events[0].region == "UK"


def test_parse_fred() -> None:
    data = {"release_dates": [
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-12-04"},
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-12-10"},
        {"release_id": 99, "release_name": "H.15 Selected Interest Rates", "date": "2026-12-10"},
        {"release_id": 53, "release_name": "Gross Domestic Product", "date": "junk"},
    ]}
    events = cal.parse_fred(data)
    assert [(e.title.split(" (")[0], e.impact, e.when) for e in events] == [
        ("Employment Situation", "high", datetime(2026, 12, 4, 13, 30)),
        ("CPI inflation", "high", datetime(2026, 12, 10, 13, 30)),
    ]


def test_nfp_rule_is_labelled_approximate() -> None:
    events = cal.nfp_rule(date(2026, 10, 27), date(2026, 12, 22))
    assert [e.when for e in events] == [datetime(2026, 11, 6, 13, 30), datetime(2026, 12, 4, 13, 30)]
    assert all("approx" in e.title for e in events)


def test_week_key_and_supersede(session: Session) -> None:
    assert cal.week_key(date(2026, 11, 8)) == date(2026, 11, 8)  # Sunday starts the FF week
    assert cal.week_key(date(2026, 11, 14)) == date(2026, 11, 8)
    assert cal.week_key(date(2026, 11, 15)) == date(2026, 11, 15)
    ff = cal.build_rows(cal.parse_forexfactory(FF_JSON), *WINDOW)
    schedule = cal.build_rows(
        [cal.Event("ons", "UK", "GDP monthly estimate", datetime(2026, 11, 12, 7, 0), True, "high"),
         cal.Event("ons", "UK", "CPI inflation", datetime(2026, 11, 18, 7, 0), True, "high")],
        *WINDOW,
    )
    cal._save(session, ff + schedule)
    weeks = cal._ff_weeks(session)
    assert weeks == {date(2026, 11, 8)}
    assert cal._prune_superseded(session, weeks) == 1  # the ONS row inside the FF week goes
    left = {e.title for e in session.execute(select(CalendarEvent)).scalars() if e.source == "ons"}
    assert left == {"CPI inflation"}


def test_drop_stale_future_rows(session: Session) -> None:
    now = datetime(2026, 11, 10, 18, 0)
    old = cal.build_rows([cal.Event("bea", "US", "GDP", now + timedelta(days=10)),
                          cal.Event("bea", "US", "GDP", now - timedelta(days=5))], *WINDOW)
    cal._save(session, old)
    new = cal.build_rows([cal.Event("bea", "US", "GDP", now + timedelta(days=11))], *WINDOW)  # rescheduled
    cal._save(session, new)
    cal._drop_stale_future(session, "bea", new, now)
    times = sorted(e.event_time for e in session.execute(select(CalendarEvent)).scalars())
    assert times == [now - timedelta(days=5), now + timedelta(days=11)]


def test_norm_value() -> None:
    assert cal._norm_value("+0.30%") == cal._norm_value("0.3 %") == "0.3%"
    assert cal._norm_value("194K") == "194k" and cal._norm_value("") == ""


def test_forexfactory_cache_enforces_rate_limit(tmp_path) -> None:
    cfg = cal.load_config()
    cache = tmp_path / "ff.json"
    calls: list[int] = []

    def fetch() -> list[dict]:
        calls.append(1)
        return FF_JSON

    def boom() -> list[dict]:
        raise RuntimeError("429 Too Many Requests")

    now = datetime.utcnow()
    with pytest.raises(RuntimeError):  # no cache yet and the fetch fails -> source fails
        cal.load_forexfactory(cfg, now, cache, boom)
    data, note = cal.load_forexfactory(cfg, now, cache, fetch)
    assert data == FF_JSON and note == "forexfactory: fetched" and cache.exists()
    data, note = cal.load_forexfactory(cfg, now + timedelta(minutes=5), cache, fetch)
    assert len(calls) == 1 and "not re-fetched" in note  # within the hour: no request at all
    data, note = cal.load_forexfactory(cfg, now + timedelta(hours=2), cache, boom)
    assert data == FF_JSON and "fetch failed" in note  # stale cache beats nothing
