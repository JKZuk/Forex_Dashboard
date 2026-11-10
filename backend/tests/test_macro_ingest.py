"""Macro ingest tests: parsers, transforms and the run() loops. No network access."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.catalog import GLOBAL_SERIES, INDICATOR_BY_KEY
from app.db import Base
from app.ingest import _macro_util as mu
from app.ingest import macro_fred, macro_official
from app.ingest._fred_series import FRED_SERIES, FredSeries
from app.models import Observation, SeriesMeta
from app.regions import REGION_CODES


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(mu.time, "sleep", lambda _s: None)


class FakeResponse:
    def __init__(self, text: str = "", payload: dict | None = None, status: int = 200):
        self.text, self._payload, self.status_code = text, payload, status
        self.content = text.encode("utf-8")
        self.url = "https://example.test/x?y=1"

    def json(self) -> dict:
        return self._payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise mu.requests.HTTPError(f"HTTP {self.status_code}")


def monthly(start_year: int, values: list[float]) -> mu.Points:
    return [(date(start_year + i // 12, i % 12 + 1, 1), v) for i, v in enumerate(values)]


# ------------------------------------------------------------------------- utilities


def test_parse_period():
    assert mu.parse_period("2026-09-14") == date(2026, 9, 14)
    assert mu.parse_period("2026-09") == date(2026, 9, 1)
    assert mu.parse_period("2026-Q3") == date(2026, 7, 1)
    assert mu.parse_period("2026") == date(2026, 1, 1)
    assert mu.parse_period("n/a") is None


def test_parse_float():
    assert mu.parse_float("1,234.5") == 1234.5
    assert mu.parse_float(" 2.1 ") == 2.1
    for missing in ("", ".", "-", "NA", None, float("nan")):
        assert mu.parse_float(missing) is None


def test_yoy_pct():
    points = monthly(2024, [100.0] * 12 + [103.0] * 12)
    out = mu.yoy_pct(points)
    assert [d for d, _ in out] == [d for d, _ in points[12:]]
    assert all(v == pytest.approx(3.0) for _, v in out)


def test_yoy_pct_interpolates_a_single_missing_base_month():
    points = monthly(2025, [100.0 + i for i in range(24)])
    del points[9]  # October 2025 was never published
    out = dict(mu.yoy_pct(points))
    base = 108.0 * (110.0 / 108.0) ** (30 / 61)  # log-linear between September and November
    assert out[date(2026, 10, 1)] == pytest.approx((121.0 / base - 1) * 100)
    assert date(2025, 10, 1) not in out


def test_yoy_pct_does_not_bridge_long_gaps():
    points = monthly(2025, [100.0 + i for i in range(24)])
    del points[8:11]  # three months missing
    assert date(2026, 10, 1) not in dict(mu.yoy_pct(points))


def test_diff_skips_gaps():
    points = [(date(2026, 1, 1), 10.0), (date(2026, 2, 1), 13.0), (date(2026, 6, 1), 20.0), (date(2026, 7, 1), 19.0)]
    assert mu.diff(points) == [(date(2026, 2, 1), 3.0), (date(2026, 7, 1), -1.0)]


def test_subtract_and_backfill():
    a = [(date(2026, 1, 1), 5.0), (date(2026, 2, 1), 6.0)]
    b = [(date(2026, 2, 1), 2.0)]
    assert mu.subtract(a, b) == [(date(2026, 2, 1), 4.0)]
    older = [(date(2025, 10, 1), 1.0), (date(2026, 1, 1), 9.0)]
    assert mu.backfill(a, older) == [(date(2025, 10, 1), 1.0)] + a
    assert mu.backfill([], older) == older


def test_is_stale():
    today = date(2026, 11, 10)
    assert not mu.is_stale("M", date(2026, 9, 1), today)
    assert mu.is_stale("M", date(2025, 6, 1), today)
    assert mu.is_stale("D", date(2026, 10, 1), today)
    assert not mu.is_stale("Q", date(2026, 4, 1), today)


def test_http_get_retries_then_succeeds(monkeypatch):
    calls = []

    class FakeSession:
        def get(self, url, **_kw):
            calls.append(url)
            return FakeResponse(status=503) if len(calls) < 3 else FakeResponse("ok")

    monkeypatch.setattr(mu, "_http", lambda: FakeSession())
    assert mu.http_get("https://example.test/x").text == "ok"
    assert len(calls) == 3


def test_http_get_raises_after_retries(monkeypatch):
    class FakeSession:
        def get(self, url, **_kw):
            raise mu.requests.ConnectionError("down")

    monkeypatch.setattr(mu, "_http", lambda: FakeSession())
    with pytest.raises(mu.requests.ConnectionError):
        mu.http_get("https://example.test/x", retries=2)


# ------------------------------------------------------------------------------ FRED


def test_fred_mapping_is_consistent():
    ids = [(s.region, s.indicator) for s in FRED_SERIES]
    assert len(ids) == len(set(ids))
    for spec in FRED_SERIES:
        assert spec.transform in ("level", "yoy", "diff")
        assert spec.frequency in ("D", "W", "M", "Q")
        if spec.region == "GLOBAL":
            assert f"GLOBAL.{spec.indicator}" in GLOBAL_SERIES
        else:
            assert spec.region in REGION_CODES
            assert spec.indicator in INDICATOR_BY_KEY


def test_parse_fred_csv():
    text = "observation_date,DGS10\n2026-11-04,4.80\n2026-11-05,.\n2026-11-06,4.86\n2026-11-09,\n"
    assert macro_fred.parse_fred_csv(text) == [(date(2026, 11, 4), 4.80), (date(2026, 11, 6), 4.86)]


def test_parse_fred_csv_rejects_html():
    with pytest.raises(ValueError):
        macro_fred.parse_fred_csv("<!DOCTYPE html>\n<html>Not found</html>")


def test_parse_fred_json():
    payload = {"observations": [{"date": "2026-09-01", "value": "4.2"}, {"date": "2026-10-01", "value": "."}]}
    assert macro_fred.parse_fred_json(payload) == [(date(2026, 9, 1), 4.2)]
    with pytest.raises(ValueError, match="bad key"):
        macro_fred.parse_fred_json({"error_code": 400, "error_message": "bad key"})


def test_fred_fetch_uses_api_only_with_a_key(monkeypatch):
    seen = []

    def fake_get(url, params=None, **_kw):
        seen.append((url, dict(params)))
        if url == macro_fred.API_URL:
            return FakeResponse(payload={"observations": [{"date": "2026-10-01", "value": "4.1"}]})
        return FakeResponse("observation_date,UNRATE\n2026-10-01,4.1\n")

    monkeypatch.setattr(mu, "http_get", fake_get)
    expected = [(date(2026, 10, 1), 4.1)]
    assert macro_fred.fetch_observations("UNRATE", date(2009, 1, 1)) == expected
    assert macro_fred.fetch_observations("UNRATE", date(2009, 1, 1), api_key="k") == expected
    (csv_url, csv_params), (api_url, api_params) = seen
    assert csv_url == macro_fred.CSV_URL and csv_params == {"id": "UNRATE", "cosd": "2009-01-01"}
    assert api_url == macro_fred.API_URL
    assert api_params == {
        "series_id": "UNRATE", "api_key": "k", "file_type": "json", "observation_start": "2009-01-01",
    }


def test_fred_transforms():
    raw = monthly(2025, [150_000.0 + 100 * i for i in range(14)])
    nfp = FredSeries("US", "EMPLOYMENT_CHG", "PAYEMS", "diff")
    assert macro_fred.apply_transform(nfp, raw)[0] == (date(2025, 2, 1), 100.0)
    claims = FredSeries("US", "JOBLESS_CLAIMS", "ICSA", frequency="W", scale=0.001)
    assert macro_fred.apply_transform(claims, [(date(2026, 10, 31), 189_000.0)]) == [(date(2026, 10, 31), 189.0)]
    cpi = FredSeries("US", "CPI_YOY", "CPIAUCSL", "yoy")
    assert macro_fred.apply_transform(cpi, monthly(2025, [100.0] * 12 + [102.5]))[0][1] == pytest.approx(2.5)


def test_fred_fetch_start_is_incremental_only_for_daily_levels():
    daily = FredSeries("US", "YIELD_10Y", "DGS10", frequency="D")
    assert macro_fred.fetch_start(daily, None) == mu.FETCH_START
    assert macro_fred.fetch_start(daily, date(2026, 11, 6)) == date(2026, 9, 22)
    assert macro_fred.fetch_start(FredSeries("US", "CPI_YOY", "CPIAUCSL", "yoy"), date(2026, 9, 1)) == mu.FETCH_START


def test_fred_run_stores_series_and_survives_failures(session, monkeypatch):
    specs = [
        FredSeries("US", "UNEMPLOYMENT", "UNRATE"),
        FredSeries("US", "CPI_YOY", "CPIAUCSL", "yoy"),
        FredSeries("US", "YIELD_10Y", "BROKEN", frequency="D"),
        FredSeries("GLOBAL", "VIX", "VIXCLS", frequency="D", unit="index"),
    ]
    today = date.today()
    recent = [date(today.year - 2 + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1, 1) for i in range(1, 25)]

    def fake_fetch(fred_id, start, api_key=""):
        if fred_id == "BROKEN":
            raise mu.requests.ConnectionError("boom")
        if fred_id == "VIXCLS":
            return [(today, 19.9)]
        return [(d, 100.0 + i) for i, d in enumerate(recent)]

    monkeypatch.setattr(macro_fred, "FRED_SERIES", specs)
    monkeypatch.setattr(macro_fred, "fetch_observations", fake_fetch)
    result = macro_fred.run(session)
    session.commit()

    assert result.message.startswith("3 series saved")
    assert "US.YIELD_10Y (BROKEN)" in result.message and "stale" not in result.message
    meta = {m.series_id: m for m in session.scalars(select(SeriesMeta))}
    assert set(meta) == {"US.UNEMPLOYMENT", "US.CPI_YOY", "GLOBAL.VIX"}
    assert meta["US.CPI_YOY"].unit == "%" and meta["US.CPI_YOY"].category == "inflation"
    assert meta["US.CPI_YOY"].source == "fred" and meta["US.CPI_YOY"].source_code == "CPIAUCSL"
    assert meta["GLOBAL.VIX"].region == "GLOBAL" and meta["GLOBAL.VIX"].name == GLOBAL_SERIES["GLOBAL.VIX"]
    n_cpi = len(session.scalars(select(Observation).where(Observation.series_id == "US.CPI_YOY")).all())
    assert n_cpi == 12  # 24 months of index -> 12 months of y/y

    assert macro_fred.run(session).rows == result.rows  # idempotent upsert


def test_fred_run_raises_when_everything_fails(session, monkeypatch):
    def fail(*_a, **_kw):
        raise mu.requests.ConnectionError("offline")

    monkeypatch.setattr(macro_fred, "fetch_observations", fail)
    with pytest.raises(RuntimeError):
        macro_fred.run(session)


def test_history_is_trimmed_to_2010(session):
    item = mu.Fetched("US", "UNEMPLOYMENT", [(date(2009, 12, 1), 9.9), (date(2010, 1, 1), 9.8)], "M", "fred", "UNRATE")
    assert mu.store(session, item) == 1


# ----------------------------------------------------------------------------- OECD

KEI_HEADER = "DATAFLOW,REF_AREA,FREQ,MEASURE,UNIT_MEASURE,ACTIVITY,ADJUSTMENT,TRANSFORMATION,TIME_PERIOD,OBS_VALUE,OBS_STATUS"


def kei_csv(*rows: str) -> macro_official.Rows:
    return macro_official.parse_csv_rows("\n".join([KEI_HEADER, *("DF_KEI," + r + ",A" for r in rows)]))


def by_id(items: list[mu.Fetched]) -> dict[str, mu.Fetched]:
    return {i.series_id: i for i in items}


def test_kei_series_levels_frequencies_and_trade_balance():
    rows = kei_csv(
        "CAN,M,CP,GR,_Z,_Z,GY,2026-09,3.09",
        "CAN,M,CP,GR,_Z,_Z,G1,2026-09,0.12",  # m/m growth must not be picked up
        "CAN,Q,CP,GR,_Z,_Z,GY,2026-Q3,3.05",  # Canada is monthly
        "NZL,Q,CP,GR,_Z,_Z,GY,2026-Q3,3.71",
        "CAN,M,EMP,PS,_T,Y,_Z,2026-09,21104.8",
        "CAN,M,EMP,PS,_T,Y,_Z,2026-10,21100.1",
        "JPN,M,EX,USD,_T,Y,_Z,2026-09,65.0",
        "JPN,M,IM,USD,_T,Y,_Z,2026-09,69.5",
        "JPN,M,IM,USD,_T,Y,_Z,2026-08,70.0",
        "USA,Q,CA_GDP,PT_B1GQ,_T,Y,_Z,2026-Q2,-3.02",
        "CHE,M,IR3TIB,PA,_Z,_Z,_Z,2026-10,-0.04",
    )
    out = by_id(macro_official.kei_series(rows))
    assert out["CA.CPI_YOY"].points == [(date(2026, 9, 1), 3.09)]
    assert out["NZ.CPI_YOY"].points == [(date(2026, 7, 1), 3.71)] and out["NZ.CPI_YOY"].frequency == "Q"
    assert out["CA.EMPLOYMENT_CHG"].points == [(date(2026, 10, 1), pytest.approx(-4.7))]
    assert out["CA.EMPLOYMENT_CHG"].unit == "thousands"
    assert out["JP.TRADE_BALANCE"].points == [(date(2026, 9, 1), pytest.approx(-4.5))]
    assert out["JP.TRADE_BALANCE"].unit == "USD bn"
    assert out["US.CURRENT_ACCOUNT_GDP"].points == [(date(2026, 4, 1), -3.02)]
    assert out["CH.YIELD_2Y"].note.startswith("proxy: 3M interbank")
    assert "US.CPI_YOY" not in out  # the US comes from FRED


def test_kei_series_backfills_australian_cpi_with_quarterly_history():
    rows = kei_csv(
        "AUS,Q,CP,GR,_Z,_Z,GY,2024-Q4,2.4",
        "AUS,Q,CP,GR,_Z,_Z,GY,2025-Q2,2.1",
        "AUS,M,CP,GR,_Z,_Z,GY,2025-04,2.3",
        "AUS,M,CP,GR,_Z,_Z,GY,2025-05,2.2",
    )
    cpi = by_id(macro_official.kei_series(rows))["AU.CPI_YOY"]
    assert cpi.points == [(date(2024, 10, 1), 2.4), (date(2025, 4, 1), 2.3), (date(2025, 5, 1), 2.2)]
    assert "quarterly" in cpi.note


def test_kei_rules_cover_only_catalog_indicators():
    for rule in macro_official.KEI_RULES:
        assert rule.indicator in INDICATOR_BY_KEY
        assert set(rule.monthly + rule.quarterly) <= set(REGION_CODES)
        assert not set(rule.monthly) & set(rule.quarterly)


def test_cli_series_marks_the_neutral_level():
    text = (
        "DATAFLOW,REF_AREA,FREQ,MEASURE,TIME_PERIOD,OBS_VALUE\n"
        "DF_CLI,GBR,M,BCICP,2026-10,101.66\nDF_CLI,GBR,M,CCICP,2026-10,98.9\nDF_CLI,USA,M,CCICP,2026-10,96.0\n"
    )
    out = by_id(macro_official.cli_series(macro_official.parse_csv_rows(text)))
    assert out["UK.PMI"].points == [(date(2026, 10, 1), 101.66)]
    assert "neutral=100" in out["UK.PMI"].note and out["UK.PMI"].note.startswith("proxy")
    assert "neutral=100" in out["UK.CONSUMER_CONF"].note
    assert "US.CONSUMER_CONF" not in out  # FRED supplies the Michigan survey


def test_core_cpi_series():
    text = (
        "REF_AREA,FREQ,METHODOLOGY,EXPENDITURE,TIME_PERIOD,OBS_VALUE\n"
        "GBR,M,N,_TXCP01_NRG,2026-09,3.1\nGBR,M,HICP,_TXCP01_NRG,2026-09,9.9\n"
        "NZL,Q,N,_TXCP01_NRG,2026-Q3,2.45\nAUS,Q,N,_TXCP01_NRG,2024-Q4,3.3\nAUS,M,N,_TXCP01_NRG,2025-04,2.8\n"
    )
    out = by_id(macro_official.core_cpi_series(macro_official.parse_csv_rows(text)))
    assert out["UK.CORE_CPI_YOY"].points == [(date(2026, 9, 1), 3.1)]
    assert out["NZ.CORE_CPI_YOY"].points == [(date(2026, 7, 1), 2.45)]
    assert out["AU.CORE_CPI_YOY"].points == [(date(2024, 10, 1), 3.3), (date(2025, 4, 1), 2.8)]


# ---------------------------------------------------------------- other providers


def test_policy_rate_series():
    text = "FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\nD,GB,2026-11-02,3.75\nD,JP,2026-11-03,1.25\nD,XM,2026-11-03,2.5\n"
    out = by_id(macro_official.policy_rate_series(macro_official.parse_csv_rows(text)))
    assert out["UK.POLICY_RATE"].points == [(date(2026, 11, 2), 3.75)]
    assert out["JP.POLICY_RATE"].frequency == "D"
    assert "EZ.POLICY_RATE" not in out


def test_eurostat_trade_series():
    lines = ["freq,stk_flow,unit,partner,indic,geo,TIME_PERIOD,OBS_VALUE"]
    for period, exp, imp in (("2025-08", 240000.0, 230000.0), ("2026-08", 257201.0, 264027.6)):
        lines += [
            f"M,EXP,MIO-EUR-SA,EXT_EA21,ET-T,EA21,{period},{exp}",
            f"M,IMP,MIO-EUR-SA,EXT_EA21,ET-T,EA21,{period},{imp}",
            f"M,BAL_RT,MIO-EUR-SA,EXT_EA21,ET-T,EA21,{period},{exp - imp}",
        ]
    out = by_id(macro_official.eurostat_trade_series(macro_official.parse_csv_rows("\n".join(lines))))
    assert out["EZ.TRADE_BALANCE"].points[-1] == (date(2026, 8, 1), pytest.approx(-6.8266))
    assert out["EZ.TRADE_BALANCE"].unit == "EUR bn"
    assert out["EZ.EXPORTS_YOY"].points == [(date(2026, 8, 1), pytest.approx(7.167, abs=1e-3))]
    assert out["EZ.IMPORTS_YOY"].points == [(date(2026, 8, 1), pytest.approx(14.795, abs=1e-3))]


def test_ecb_yield_series():
    text = (
        "KEY,FREQ,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE\n"
        "YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y,B,SR_2Y,2026-11-09,2.9391\n"
        "YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,SR_10Y,2026-11-09,3.2720\n"
    )
    out = by_id(macro_official.ecb_yield_series(macro_official.parse_csv_rows(text)))
    assert out["EZ.YIELD_2Y"].points == [(date(2026, 11, 9), 2.9391)]
    assert out["EZ.YIELD_10Y"].points == [(date(2026, 11, 9), 3.2720)]


def test_boc_series():
    payload = {
        "observations": [
            {"d": "2026-09-01", "CPI_TRIM": {"v": "1.8"}},
            {"d": "2026-11-06", "BD.CDN.2YR.DQ.YLD": {"v": "3.12"}, "BD.CDN.10YR.DQ.YLD": {"v": "3.65"}, "V39079": {"v": "2.50"}},
            {"d": "2026-11-09", "BD.CDN.2YR.DQ.YLD": {"v": "3.13"}, "BD.CDN.10YR.DQ.YLD": {"v": ""}},
        ]
    }
    out = by_id(macro_official.boc_series(payload))
    assert out["CA.YIELD_2Y"].points == [(date(2026, 11, 6), 3.12), (date(2026, 11, 9), 3.13)]
    assert out["CA.YIELD_10Y"].points == [(date(2026, 11, 6), 3.65)]
    assert out["CA.CORE_CPI_YOY"].points == [(date(2026, 9, 1), 1.8)] and out["CA.CORE_CPI_YOY"].frequency == "M"
    assert out["CA.POLICY_RATE"].points == [(date(2026, 11, 6), 2.5)]


MOF_CSV = (
    "Interest Rate,,,,,,,,,,,,,,,(Unit : %)\r\n"
    "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y\r\n"
    "1974/9/24,10.327,9.362,8.83,8.515,8.348,8.29,8.24,8.121,8.127,-,-,-,-,-,-\r\n"
    "2026/11/6,1.6,1.78,1.87,2.0,2.1,2.2,2.3,2.4,2.5,2.73,3.2,3.6,3.9,3.9,3.9\r\n"
    "2026/11/9,1.595,1.778,1.871,2.011,2.115,2.198,2.302,2.446,2.584,2.724,3.289,3.604,3.939,3.911,3.941\r\n"
    ",,,,,,,,,,,,,,,\r\n"
    '"  If you cannot download the latest csv data, please clear the cache.",,,,,,,,,,,,,,,\r\n'
)


def test_mof_series():
    out = by_id(macro_official.mof_series(MOF_CSV))
    assert out["JP.YIELD_2Y"].points == [(date(2026, 11, 6), 1.78), (date(2026, 11, 9), 1.778)]
    assert out["JP.YIELD_10Y"].points[-1] == (date(2026, 11, 9), 2.724)
    with pytest.raises(ValueError):
        macro_official.mof_series("<html>moved</html>")


def test_fetch_mof_merges_history_and_current_month(monkeypatch):
    current = MOF_CSV.replace("2026/11/9,", "2026/11/10,")

    def fake_get(url, **_kw):
        return FakeResponse(current if url == macro_official.MOF_CURRENT_URL else MOF_CSV)

    monkeypatch.setattr(mu, "http_get", fake_get)
    two_year = by_id(macro_official.fetch_mof_japan(mu.FETCH_START))["JP.YIELD_2Y"]
    assert [d for d, _ in two_year.points] == [date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)]


def test_rba_series():
    f2 = (
        "﻿F2 CAPITAL MARKET YIELDS\n"
        "Title,Australian Government 2 year bond,Australian Government 10 year bond\n"
        "Units,Per cent per annum,Per cent per annum\n\n"
        "Series ID,FCMYGBAG2D,FCMYGBAG10D\n"
        "20-May-2013,,3.229\n03-Nov-2026,4.828,5.086\n04-Nov-2026,4.826,5.082\n\n\n"
    )
    out = by_id(macro_official.rba_series(f2, "f2"))
    assert out["AU.YIELD_2Y"].points == [(date(2026, 11, 3), 4.828), (date(2026, 11, 4), 4.826)]
    assert out["AU.YIELD_10Y"].points[0] == (date(2013, 5, 20), 3.229)
    f1 = "Title,Cash Rate Target,Change\nSeries ID,FIRMMCRTD,FIRMMCCRT\n09-Nov-2026,4.85,\n10-Nov-2026,,\n"
    assert by_id(macro_official.rba_series(f1, "f1"))["AU.POLICY_RATE"].points == [(date(2026, 11, 9), 4.85)]
    with pytest.raises(ValueError):
        macro_official.rba_series("no header here\n", "f2")


def test_estat_points():
    payload = {
        "GET_STATS": {
            "RESULT": {"status": "0"},
            "STATISTICAL_DATA": {"DATA_INF": {"DATA_OBJ": [
                {"VALUE": {"@time": "20260800", "$": "2.3"}},
                {"VALUE": {"@time": "20260900", "$": "2.1"}},
                {"VALUE": {"@time": "20261000", "$": "-"}},
            ]}},
        }
    }
    assert macro_official.estat_points(payload) == [(date(2026, 8, 1), 2.3), (date(2026, 9, 1), 2.1)]
    with pytest.raises(ValueError):
        macro_official.estat_points({"GET_STATS": {"RESULT": {"status": "100", "errorMsg": "bad"}}})


# -------------------------------------------------------------------------- run loop


def test_official_start_date_is_incremental_once_all_daily_series_exist(session):
    daily = (("EZ", "YIELD_2Y"), ("EZ", "YIELD_10Y"))
    assert macro_official.start_date(session, ()) == mu.FETCH_START
    assert macro_official.start_date(session, daily) == mu.FETCH_START
    mu.store(session, mu.Fetched("EZ", "YIELD_2Y", [(date(2026, 11, 9), 2.9)], "D", "ecb", "x"))
    assert macro_official.start_date(session, daily) == mu.FETCH_START
    mu.store(session, mu.Fetched("EZ", "YIELD_10Y", [(date(2026, 11, 6), 3.3)], "D", "ecb", "y"))
    assert macro_official.start_date(session, daily) == date(2026, 9, 22)


def test_official_run_isolates_provider_failures(session, monkeypatch):
    today = date.today()

    def good(_start):
        return [
            mu.Fetched("EZ", "YIELD_10Y", [(today, 3.27)], "D", "ecb", "YC/SR_10Y", "note"),
            mu.Fetched("EZ", "YIELD_2Y", [], "D", "ecb", "YC/SR_2Y"),
            mu.Fetched("CH", "CPI_YOY", [(date(2015, 1, 1), -0.5)], "M", "bis", "WS_LONG_CPI/M.CH.771"),
        ]

    def bad(_start):
        raise mu.requests.Timeout("slow")

    providers = [macro_official.Provider("good", good), macro_official.Provider("bad", bad)]
    monkeypatch.setattr(macro_official, "PROVIDERS", providers)
    result = macro_official.run(session)
    session.commit()

    assert result.rows == 2 and result.message.startswith("2 series saved")
    assert "bad: Timeout" in result.message
    assert "EZ.YIELD_2Y: no data" in result.message
    assert "stale: CH.CPI_YOY@2015-01-01" in result.message
    meta = session.get(SeriesMeta, "EZ.YIELD_10Y")
    assert (meta.source, meta.unit, meta.category, meta.frequency) == ("ecb", "%", "yields", "D")


def test_official_run_raises_when_everything_fails(session, monkeypatch):
    def bad(_start):
        raise mu.requests.Timeout("slow")

    monkeypatch.setattr(macro_official, "PROVIDERS", [macro_official.Provider("bad", bad)])
    with pytest.raises(RuntimeError):
        macro_official.run(session)


def test_no_series_is_written_by_two_sources():
    official: set[tuple[str, str]] = set()
    for rule in macro_official.KEI_RULES:
        official |= {(r, rule.indicator) for r in rule.monthly + rule.quarterly}
    official |= {(r, "TRADE_BALANCE") for r in macro_official._TRADE_REGIONS}
    for indicator, regions, _note in macro_official.CLI_MEASURES.values():
        official |= {(r, indicator) for r in regions}
    official |= {(r, "CORE_CPI_YOY") for r in macro_official.CORE_CPI_REGIONS}
    official |= {(r, "POLICY_RATE") for r in macro_official.BIS_AREA}
    fred = {(s.region, s.indicator) for s in FRED_SERIES}
    assert not official & fred
