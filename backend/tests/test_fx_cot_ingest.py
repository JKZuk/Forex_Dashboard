"""Offline tests for FX price and COT ingestion (no network)."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.ingest import _fx_providers, cot, fx_prices
from app.models import CotPosition, Observation, SeriesMeta
from app.regions import FX_USD_INDEX

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ----------------------------------------------------------------- provider parsers
def test_parse_frankfurter_inverts_to_usd_per_unit():
    payload = {
        "amount": 1.0,
        "base": "USD",
        "rates": {
            "2024-01-02": {"EUR": 0.8, "JPY": 160.0},
            "2024-01-03": {"EUR": 0.5, "JPY": None},
        },
    }
    table = _fx_providers.parse_frankfurter(payload)
    assert table["EUR"] == {D1: pytest.approx(1.25), D2: pytest.approx(2.0)}
    assert table["JPY"] == {D1: pytest.approx(0.00625)}


def test_parse_frankfurter_rejects_wrong_base():
    with pytest.raises(ValueError):
        _fx_providers.parse_frankfurter({"base": "EUR", "rates": {}})


def test_parse_ecb_csv_crosses_through_usd():
    text = (
        "KEY,FREQ,CURRENCY,CURRENCY_DENOM,TIME_PERIOD,OBS_VALUE\n"
        "EXR.D.USD.EUR.SP00.A,D,USD,EUR,2024-01-02,1.10\n"
        "EXR.D.USD.EUR.SP00.A,D,USD,EUR,2024-01-03,1.20\n"
        "EXR.D.JPY.EUR.SP00.A,D,JPY,EUR,2024-01-02,165.0\n"
        "EXR.D.JPY.EUR.SP00.A,D,JPY,EUR,2024-01-03,\n"
        "EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,2024-01-04,0.85\n"  # no USD fix that day
    )
    table = _fx_providers.parse_ecb_csv(text)
    assert table["EUR"] == {D1: pytest.approx(1.10), D2: pytest.approx(1.20)}
    assert table["JPY"] == {D1: pytest.approx(1.10 / 165.0)}
    assert table["GBP"] == {}


# ----------------------------------------------------------------- basket math
def test_usd_index_base_100_and_direction():
    rates = {"EUR": {D1: 1.0, D2: 0.9, D3: 1.1}, "JPY": {D1: 0.01, D2: 0.01, D3: 0.01}}
    idx = dict(fx_prices.compute_usd_index(rates, {"EUR": 0.5, "JPY": 0.5}))
    assert idx[D1] == pytest.approx(100.0)
    assert idx[D2] > 100.0  # EUR fell vs USD -> dollar index up
    assert idx[D3] < 100.0
    assert idx[D2] == pytest.approx(100.0 * 0.9**-0.5)


def test_usd_index_normalises_weights_and_uses_common_dates():
    rates = {"EUR": {D1: 1.0, D2: 0.5, D3: 0.5}, "JPY": {D2: 0.01, D3: 0.02}}
    idx = fx_prices.compute_usd_index(rates, {"EUR": 3.0, "JPY": 1.0})
    assert [d for d, _ in idx] == [D2, D3]  # base = first date all components exist
    assert idx[0][1] == pytest.approx(100.0)
    assert idx[1][1] == pytest.approx(100.0 * 2.0**-0.25)


def test_usd_index_missing_component_gives_nothing():
    assert fx_prices.compute_usd_index({"EUR": {D1: 1.0}}, {"EUR": 0.5, "JPY": 0.5}) == []


def test_dxy_weights_sum_to_one():
    assert sum(fx_prices.USD_INDEX_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(fx_prices.CURRENCIES) == {"EUR", "JPY", "GBP", "AUD", "CAD", "NZD", "CNY", "CHF"}


# ----------------------------------------------------------------- fx run (fake provider)
class FakeProvider:
    name = "fake"

    def __init__(self, days):
        self.days = days
        self.calls: list[tuple[date, date]] = []

    def fetch(self, currencies, start, end):
        self.calls.append((start, end))
        return {
            c: {d: 1.0 + 0.01 * i + 0.1 * j for j, d in enumerate(self.days) if start <= d <= end}
            for i, c in enumerate(currencies)
        }


class BrokenProvider:
    name = "broken"

    def fetch(self, currencies, start, end):
        raise ConnectionError("down")


def test_fx_run_stores_series_incrementally_and_idempotently(db):
    provider = FakeProvider([D1, D2, D3])
    result = fx_prices.run(db, providers=[BrokenProvider(), provider], today=D3)
    assert provider.calls[0][0] == fx_prices.HISTORY_START
    assert "broken" in result.message and "fake" in result.message
    n_series = len(fx_prices.FETCH_CURRENCIES) + 1  # + USD index
    assert result.rows == 3 * n_series

    eur = db.scalars(
        select(Observation.value).where(Observation.series_id == "FX.EURUSD").order_by(Observation.obs_date)
    ).all()
    assert eur == pytest.approx([1.0, 1.1, 1.2])
    idx = db.scalars(
        select(Observation.value).where(Observation.series_id == FX_USD_INDEX).order_by(Observation.obs_date)
    ).all()
    assert idx[0] == pytest.approx(100.0)
    assert idx[2] < idx[1] < 100.0  # every currency rose vs USD
    meta = db.get(SeriesMeta, "FX.JPYUSD")
    assert (meta.region, meta.category, meta.frequency) == ("FX", "fx", "D")

    # second run: starts from the last stored date minus the overlap, adds nothing
    fx_prices.run(db, providers=[provider], today=D3)
    assert provider.calls[1][0] == D3 - fx_prices.timedelta(days=fx_prices.OVERLAP_DAYS)
    assert db.scalar(select(func.count()).select_from(Observation)) == 3 * n_series
    idx2 = db.scalars(
        select(Observation.value).where(Observation.series_id == FX_USD_INDEX).order_by(Observation.obs_date)
    ).all()
    assert idx2 == pytest.approx(idx)


def test_fx_run_raises_when_all_providers_fail(db):
    with pytest.raises(RuntimeError):
        fx_prices.run(db, providers=[BrokenProvider()], today=D3)


# ----------------------------------------------------------------- COT
def _rec(code, day, long_, short, oi):
    return {
        "cftc_contract_market_code": code,
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "noncomm_positions_long_all": str(long_),
        "noncomm_positions_short_all": str(short),
        "open_interest_all": str(oi),
    }


COT_RECORDS = [
    _rec("099741", "2026-11-03", 229383, 387174, 876122),  # EUR, net short
    _rec("097741", "2026-11-03", 227198, 124877, 380641),  # JPY, net long
    _rec("098662", "2026-11-03", 33054, 18864, 49091),  # dollar index -> USD
    _rec("999999", "2026-11-03", 1, 2, 3),  # unknown contract
    _rec("090741", "2026-11-03", 10, 5, 0),  # zero open interest
    {"cftc_contract_market_code": "092741", "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000"},
]


def test_cot_contract_codes():
    assert cot.CONTRACTS == {
        "099741": "EUR", "097741": "JPY", "096742": "GBP", "232741": "AUD",
        "090741": "CAD", "112741": "NZD", "092741": "CHF", "098662": "USD",
    }


def test_cot_parse_rows_signs_and_filters():
    rows = {r["currency"]: r for r in cot.parse_rows(COT_RECORDS)}
    assert set(rows) == {"EUR", "JPY", "USD"}
    eur = rows["EUR"]
    assert eur["report_date"] == date(2026, 11, 3)
    assert eur["noncomm_net"] == -157791  # specs short EUR vs USD -> negative
    assert eur["net_pct_oi"] == pytest.approx(-157791 / 876122 * 100)
    assert rows["JPY"]["noncomm_net"] > 0 and rows["JPY"]["net_pct_oi"] > 0
    assert rows["USD"]["noncomm_net"] == 14190  # long the dollar index = long USD


def test_cot_fetch_start():
    ccys = ["EUR", "JPY"]
    assert cot.fetch_start({}, ccys) == cot.HISTORY_START
    assert cot.fetch_start({"EUR": date(2026, 11, 3)}, ccys) == cot.HISTORY_START
    both = {"EUR": date(2026, 11, 3), "JPY": date(2026, 10, 27)}
    assert cot.fetch_start(both, ccys) == date(2026, 10, 27) - cot.timedelta(days=cot.OVERLAP_DAYS)


def test_cot_run_upserts_idempotently(db, monkeypatch):
    starts: list[date] = []

    def fake_fetch(start, codes=()):
        starts.append(start)
        return COT_RECORDS

    monkeypatch.setattr(cot, "fetch_records", fake_fetch)
    first = cot.run(db, today=date(2026, 11, 10))
    assert first.rows == 3 and starts == [cot.HISTORY_START]
    assert "no data for" in first.message and "STALE" not in first.message
    second = cot.run(db, today=date(2026, 12, 10))
    assert "STALE" in second.message
    assert db.scalar(select(func.count()).select_from(CotPosition)) == 3
    row = db.get(CotPosition, ("USD", date(2026, 11, 3)))
    assert row.net_pct_oi == pytest.approx(14190 / 49091 * 100)
