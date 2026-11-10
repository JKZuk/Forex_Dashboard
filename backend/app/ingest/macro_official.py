"""Official-statistics ingest: everything FRED cannot supply fresh or at all.

All sources are free and keyless. Each provider is one small `fetch_*` function
returning ready-to-store series; register it in PROVIDERS. One failing provider
never fails the run (see IngestResult.message).

  oecd_kei     OECD Key Economic Indicators: CPI, unemployment, employment, GDP,
               retail, industrial production, trade, current account, monthly
               10Y yields and 3M interbank rates
  oecd_cli     OECD business / consumer confidence (PMI proxy, neutral = 100)
  oecd_prices  OECD core CPI (UK, AU, NZ)
  bis          BIS central bank policy rates (daily; JP, UK, NZ, CN, CH) and Swiss CPI
  eurostat     Euro area HICP, core HICP, unemployment, extra-EA goods trade
  ecb          Euro area AAA government yield curve, 2Y and 10Y (daily)
  boc          Bank of Canada Valet: policy rate, 2Y / 10Y yields (daily), CPI-trim
  mof_japan    Japan MoF JGB yield curve, 2Y and 10Y (daily)
  rba          RBA tables F1 / F2: cash rate target, 2Y and 10Y yields (daily)
  estat_japan  Statistics Japan dashboard API: CPI and core CPI

Monthly / quarterly sources are re-fetched in full on every run (a few MB in total,
and it picks up revisions). The daily API sources are incremental.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from ..regions import series_id as make_series_id
from . import IngestResult
from . import _macro_util as mu

Rows = list[dict[str, str]]


# ----------------------------------------------------------------- generic SDMX-CSV


def parse_csv_rows(text: str, delimiter: str = ",") -> Rows:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=delimiter))


def pick(rows: Rows, time_col: str = "TIME_PERIOD", value_col: str = "OBS_VALUE", **where: str) -> mu.Points:
    """Observations of the rows whose columns equal every `where` filter."""
    return mu.clean(
        (mu.parse_period(row.get(time_col, "")), mu.parse_float(row.get(value_col)))
        for row in rows
        if all(row.get(col) == val for col, val in where.items())
    )


def _period(start: date) -> str:
    return f"{start.year}-{start.month:02d}"


# ------------------------------------------------------------------------------ OECD

OECD_URL = "https://sdmx.oecd.org/public/rest/data"
OECD_CSV = {"Accept": "application/vnd.sdmx.data+csv; charset=utf-8"}
OECD_AREA = {
    "US": "USA", "EZ": "EA20", "JP": "JPN", "UK": "GBR", "AU": "AUS",
    "CA": "CAN", "NZ": "NZL", "CN": "CHN", "CH": "CHE",
}
_NON_US = ("EZ", "JP", "UK", "AU", "CA", "NZ", "CN", "CH")
_TRADE_REGIONS = ("JP", "UK", "AU", "CA", "NZ", "CN", "CH")  # no euro-area aggregate in KEI


def _oecd_get(flow: str, key: str, start: date) -> Rows:
    resp = mu.http_get(
        f"{OECD_URL}/{flow}/{key}",
        params={"startPeriod": _period(start), "dimensionAtObservation": "AllDimensions"},
        headers=OECD_CSV,
        timeout=120,
    )
    return parse_csv_rows(resp.text)


@dataclass(frozen=True)
class KeiRule:
    """One catalog indicator read from the OECD KEI dataflow.

    `dims` is (MEASURE, UNIT_MEASURE, ACTIVITY, ADJUSTMENT, TRANSFORMATION).
    Regions listed in `monthly` use the monthly series, those in `quarterly` the
    quarterly one (the country publishes nothing more frequent).
    """

    indicator: str
    dims: tuple[str, str, str, str, str]
    monthly: tuple[str, ...] = ()
    quarterly: tuple[str, ...] = ()
    transform: str = "level"  # level | diff
    note: str = ""
    unit: str | None = None
    # Prepend the quarterly series where the monthly one starts late (Australia
    # only has a complete monthly CPI from April 2025).
    backfill_quarterly: tuple[str, ...] = ()


KEI_FLOW = "OECD.SDD.STES,DSD_KEI@DF_KEI,"
KEI_RULES: list[KeiRule] = [
    KeiRule("CPI_YOY", ("CP", "GR", "_Z", "_Z", "GY"),
            monthly=("UK", "AU", "CA", "CN"), quarterly=("NZ",), backfill_quarterly=("AU",),
            note="national CPI, all items, y/y"),
    KeiRule("UNEMPLOYMENT", ("UNEMP", "PT_LF", "_T", "Y", "_Z"),
            monthly=("JP", "UK", "AU", "CA"), quarterly=("NZ", "CH"),
            note="labour-force-survey unemployment rate, SA"),
    KeiRule("EMPLOYMENT_CHG", ("EMP", "PS", "_T", "Y", "_Z"), monthly=("JP", "AU", "CA"),
            transform="diff", unit="thousands", note="total employment SA, monthly change"),
    KeiRule("GDP_YOY", ("B1GQ_Q", "GR", "_T", "Y", "GY"), quarterly=_NON_US, note="real GDP, y/y"),
    KeiRule("RETAIL_SALES_YOY", ("TOVM", "GR", "G47", "Y", "GY"),
            monthly=("EZ", "JP", "UK", "CA", "CH"), quarterly=("NZ",),
            note="retail trade volume (ex motor vehicles), y/y"),
    KeiRule("INDUSTRIAL_PROD_YOY", ("PRVM", "GR", "BTE", "Y", "GY"),
            monthly=("EZ", "JP", "UK", "CA", "CH"), quarterly=("AU", "NZ"),
            note="industrial production ex construction, y/y"),
    KeiRule("EXPORTS_YOY", ("EX", "GR", "_T", "Y", "GY"), monthly=_TRADE_REGIONS,
            note="merchandise exports, USD value SA, y/y"),
    KeiRule("IMPORTS_YOY", ("IM", "GR", "_T", "Y", "GY"), monthly=_TRADE_REGIONS,
            note="merchandise imports, USD value SA, y/y"),
    KeiRule("CURRENT_ACCOUNT_GDP", ("CA_GDP", "PT_B1GQ", "_T", "Y", "_Z"),
            quarterly=("US",) + _NON_US, note="current account balance, % of GDP, SA"),
    KeiRule("YIELD_10Y", ("IRLT", "PA", "_Z", "_Z", "_Z"), monthly=("UK", "NZ", "CH", "CN"),
            note="long-term (10Y) government bond yield, monthly average"),
    KeiRule("YIELD_2Y", ("IR3TIB", "PA", "_Z", "_Z", "_Z"), monthly=("NZ", "CH", "CN"),
            note="proxy: 3M interbank rate, monthly average"),
]
_QUARTERLY_BACKFILL_NOTE = "; quarterly observations before the monthly series starts"
_KEI_EXPORTS = ("EX", "USD", "_T", "Y", "_Z")
_KEI_IMPORTS = ("IM", "USD", "_T", "Y", "_Z")
_KEI_MEASURES = sorted({r.dims[0] for r in KEI_RULES})


def _kei_points(rows: Rows, region: str, freq: str, dims: tuple[str, str, str, str, str]) -> mu.Points:
    measure, unit, activity, adjustment, transformation = dims
    return pick(
        rows, REF_AREA=OECD_AREA[region], FREQ=freq, MEASURE=measure, UNIT_MEASURE=unit,
        ACTIVITY=activity, ADJUSTMENT=adjustment, TRANSFORMATION=transformation,
    )


def kei_series(rows: Rows) -> list[mu.Fetched]:
    out: list[mu.Fetched] = []
    for rule in KEI_RULES:
        for freq, regions in (("M", rule.monthly), ("Q", rule.quarterly)):
            for region in regions:
                points = _kei_points(rows, region, freq, rule.dims)
                note = rule.note
                if rule.transform == "diff":
                    points = mu.diff(points)
                if freq == "M" and region in rule.backfill_quarterly:
                    points = mu.backfill(points, _kei_points(rows, region, "Q", rule.dims))
                    note += _QUARTERLY_BACKFILL_NOTE
                code = f"DF_KEI/{OECD_AREA[region]}.{freq}." + ".".join(rule.dims)
                out.append(mu.Fetched(region, rule.indicator, points, freq, "oecd", code, note, rule.unit))
    for region in _TRADE_REGIONS:
        balance = mu.subtract(_kei_points(rows, region, "M", _KEI_EXPORTS), _kei_points(rows, region, "M", _KEI_IMPORTS))
        code = f"DF_KEI/{OECD_AREA[region]}.M.EX-IM.USD._T.Y._Z"
        out.append(mu.Fetched(region, "TRADE_BALANCE", balance, "M", "oecd", code,
                              "merchandise exports minus imports, SA", "USD bn"))
    return out


def fetch_oecd_kei(start: date) -> list[mu.Fetched]:
    areas = "+".join(OECD_AREA.values())
    return kei_series(_oecd_get(KEI_FLOW, f"{areas}.M+Q.{'+'.join(_KEI_MEASURES)}....", start))


CLI_FLOW = "OECD.SDD.STES,DSD_STES@DF_CLI,"
CLI_MEASURES = {
    # measure -> (indicator, regions, note)
    "BCICP": ("PMI", ("US",) + _NON_US,
              "proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100"),
    "CCICP": ("CONSUMER_CONF", _NON_US, "OECD consumer confidence, amplitude-adjusted, neutral=100"),
}


def cli_series(rows: Rows) -> list[mu.Fetched]:
    out: list[mu.Fetched] = []
    for measure, (indicator, regions, note) in CLI_MEASURES.items():
        for region in regions:
            points = pick(rows, REF_AREA=OECD_AREA[region], MEASURE=measure)
            code = f"DF_CLI/{OECD_AREA[region]}.M.{measure}.IX._Z.AA.IX._Z.H"
            out.append(mu.Fetched(region, indicator, points, "M", "oecd", code, note))
    return out


def fetch_oecd_cli(start: date) -> list[mu.Fetched]:
    areas = "+".join(OECD_AREA.values())
    return cli_series(_oecd_get(CLI_FLOW, f"{areas}.M.{'+'.join(CLI_MEASURES)}.IX._Z.AA.IX._Z.H", start))


PRICES_FLOW = "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,"
CORE_CPI_REGIONS = {"UK": "M", "AU": "M", "NZ": "Q"}  # region -> frequency
CORE_CPI_BACKFILL_QUARTERLY = ("AU",)


def core_cpi_series(rows: Rows) -> list[mu.Fetched]:
    def points_for(region: str, freq: str) -> mu.Points:
        return pick(rows, REF_AREA=OECD_AREA[region], FREQ=freq, METHODOLOGY="N", EXPENDITURE="_TXCP01_NRG")

    out: list[mu.Fetched] = []
    for region, freq in CORE_CPI_REGIONS.items():
        points, note = points_for(region, freq), "CPI all items ex food & energy, y/y"
        if freq == "M" and region in CORE_CPI_BACKFILL_QUARTERLY:
            points = mu.backfill(points, points_for(region, "Q"))
            note += _QUARTERLY_BACKFILL_NOTE
        code = f"DF_PRICES_ALL/{OECD_AREA[region]}.{freq}.N.CPI.PA._TXCP01_NRG.N.GY"
        out.append(mu.Fetched(region, "CORE_CPI_YOY", points, freq, "oecd", code, note))
    return out


def fetch_oecd_prices(start: date) -> list[mu.Fetched]:
    areas = "+".join(OECD_AREA[r] for r in CORE_CPI_REGIONS)
    return core_cpi_series(_oecd_get(PRICES_FLOW, f"{areas}.M+Q.N.CPI.PA._TXCP01_NRG.N.GY", start))


# ------------------------------------------------------------------------------- BIS

BIS_URL = "https://stats.bis.org/api/v2/data/dataflow/BIS"
# BIS republishes with a lag of up to two weeks, so it is only used where no primary
# source is reachable (US / EZ come from FRED, CA from the BoC, AU from the RBA).
BIS_AREA = {"JP": "JP", "UK": "GB", "NZ": "NZ", "CN": "CN", "CH": "CH"}


def _bis_get(flow_key: str, start: date) -> Rows:
    resp = mu.http_get(
        f"{BIS_URL}/{flow_key}",
        params={"startPeriod": start.isoformat(), "format": "csv", "detail": "dataonly"},
        timeout=120,
    )
    return parse_csv_rows(resp.text)


def policy_rate_series(rows: Rows) -> list[mu.Fetched]:
    return [
        mu.Fetched(region, "POLICY_RATE", pick(rows, REF_AREA=area), "D", "bis",
                   f"WS_CBPOL/D.{area}", "BIS central bank policy rate, daily; published with a lag of up to ~2 weeks")
        for region, area in BIS_AREA.items()
    ]


def fetch_bis_policy(start: date) -> list[mu.Fetched]:
    return policy_rate_series(_bis_get(f"WS_CBPOL/1.0/D.{'+'.join(BIS_AREA.values())}", start))


def fetch_bis_cpi(start: date) -> list[mu.Fetched]:
    # 771 = year-on-year change. Only Switzerland: the OECD stopped updating Swiss CPI.
    rows = _bis_get("WS_LONG_CPI/1.0/M.CH.771", start)
    return [mu.Fetched("CH", "CPI_YOY", pick(rows, REF_AREA="CH"), "M", "bis",
                       "WS_LONG_CPI/M.CH.771", "national CPI, all items, y/y")]


# -------------------------------------------------------------------------- Eurostat

EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"
# Eurostat renames the aggregate whenever the euro area grows (EA20 -> EA21 in 2026).
EUROSTAT_EA = "EA21"


def _eurostat_get(key: str, start: date) -> Rows:
    resp = mu.http_get(f"{EUROSTAT_URL}/{key}", params={"format": "SDMX-CSV", "startPeriod": _period(start)})
    return parse_csv_rows(resp.text)


def eurostat_trade_series(rows: Rows) -> list[mu.Fetched]:
    code = f"ei_etea_m/M.{{}}.MIO-EUR-SA..ET-T.{EUROSTAT_EA}"
    balance = mu.scale(pick(rows, stk_flow="BAL_RT"), 0.001)
    return [
        mu.Fetched("EZ", "TRADE_BALANCE", balance, "M", "eurostat", code.format("BAL_RT"),
                   "extra-euro-area goods trade balance, SA", "EUR bn"),
        mu.Fetched("EZ", "EXPORTS_YOY", mu.yoy_pct(pick(rows, stk_flow="EXP")), "M", "eurostat",
                   code.format("EXP"), "extra-euro-area goods exports, EUR value SA, y/y"),
        mu.Fetched("EZ", "IMPORTS_YOY", mu.yoy_pct(pick(rows, stk_flow="IMP")), "M", "eurostat",
                   code.format("IMP"), "extra-euro-area goods imports, EUR value SA, y/y"),
    ]


def fetch_eurostat(start: date) -> list[mu.Fetched]:
    hicp = _eurostat_get("prc_hicp_minr/M.RCH_A.TOTAL+TOT_X_NRG_FOOD.EA", start)
    unemployment = _eurostat_get(f"une_rt_m/M.SA.TOTAL.PC_ACT.T.{EUROSTAT_EA}", start)
    trade = _eurostat_get(f"ei_etea_m/M.BAL_RT+EXP+IMP.MIO-EUR-SA..ET-T.{EUROSTAT_EA}", start)
    return [
        mu.Fetched("EZ", "CPI_YOY", pick(hicp, coicop18="TOTAL"), "M", "eurostat",
                   "prc_hicp_minr/M.RCH_A.TOTAL.EA", "HICP all items, y/y (includes flash estimate)"),
        mu.Fetched("EZ", "CORE_CPI_YOY", pick(hicp, coicop18="TOT_X_NRG_FOOD"), "M", "eurostat",
                   "prc_hicp_minr/M.RCH_A.TOT_X_NRG_FOOD.EA",
                   "HICP ex energy, food, alcohol & tobacco, y/y (includes flash estimate)"),
        mu.Fetched("EZ", "UNEMPLOYMENT", pick(unemployment), "M", "eurostat",
                   f"une_rt_m/M.SA.TOTAL.PC_ACT.T.{EUROSTAT_EA}", "harmonised unemployment rate, SA"),
        *eurostat_trade_series(trade),
    ]


# ------------------------------------------------------------------------------- ECB

ECB_URL = "https://data-api.ecb.europa.eu/service/data"
ECB_YIELDS = {"SR_2Y": "YIELD_2Y", "SR_10Y": "YIELD_10Y"}


def ecb_yield_series(rows: Rows) -> list[mu.Fetched]:
    return [
        mu.Fetched("EZ", indicator, pick(rows, DATA_TYPE_FM=tenor), "D", "ecb",
                   f"YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{tenor}", "ECB spot yield curve, AAA euro-area governments")
        for tenor, indicator in ECB_YIELDS.items()
    ]


def fetch_ecb(start: date) -> list[mu.Fetched]:
    resp = mu.http_get(
        f"{ECB_URL}/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{'+'.join(ECB_YIELDS)}",
        params={"startPeriod": start.isoformat(), "format": "csvdata"},
        timeout=120,
    )
    return ecb_yield_series(parse_csv_rows(resp.text))


# -------------------------------------------------------------------- Bank of Canada

BOC_URL = "https://www.bankofcanada.ca/valet/observations"
BOC_SERIES = {
    # valet id -> (indicator, frequency, note)
    "V39079": ("POLICY_RATE", "D", "BoC target for the overnight rate"),
    "BD.CDN.2YR.DQ.YLD": ("YIELD_2Y", "D", "Government of Canada 2Y benchmark bond yield"),
    "BD.CDN.10YR.DQ.YLD": ("YIELD_10Y", "D", "Government of Canada 10Y benchmark bond yield"),
    "CPI_TRIM": ("CORE_CPI_YOY", "M", "BoC preferred core measure CPI-trim, y/y"),
}


def boc_series(payload: dict) -> list[mu.Fetched]:
    observations = payload.get("observations", [])
    out: list[mu.Fetched] = []
    for code, (indicator, freq, note) in BOC_SERIES.items():
        points = mu.clean(
            (mu.parse_period(obs.get("d", "")), mu.parse_float(obs[code].get("v")))
            for obs in observations
            if code in obs
        )
        out.append(mu.Fetched("CA", indicator, points, freq, "boc", code, note))
    return out


def fetch_boc(start: date) -> list[mu.Fetched]:
    resp = mu.http_get(f"{BOC_URL}/{','.join(BOC_SERIES)}/json", params={"start_date": start.isoformat()})
    return boc_series(resp.json())


# ------------------------------------------------------------------------- Japan MoF

_MOF_BASE = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate"
MOF_HISTORY_URL = f"{_MOF_BASE}/historical/jgbcme_all.csv"  # everything up to last month
MOF_CURRENT_URL = f"{_MOF_BASE}/jgbcme.csv"  # the current month
MOF_TENORS = {"2Y": "YIELD_2Y", "10Y": "YIELD_10Y"}


def mof_series(text: str, start: date = mu.FETCH_START) -> list[mu.Fetched]:
    """Parse the MoF JGB curve file: a title line, a `Date,1Y,2Y,...` header, y/m/d rows."""
    lines = text.splitlines()
    header_at = next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)
    if header_at is None:
        raise ValueError("MoF JGB file: header row not found")
    columns = lines[header_at].split(",")
    points: dict[str, list[tuple[date | None, float | None]]] = {t: [] for t in MOF_TENORS}
    for line in lines[header_at + 1:]:
        cells = line.split(",")
        try:
            d = datetime.strptime(cells[0].strip(), "%Y/%m/%d").date()
        except ValueError:
            continue  # footer notes
        if d < start:
            continue
        for tenor in MOF_TENORS:
            points[tenor].append((d, mu.parse_float(cells[columns.index(tenor)])))
    return [
        mu.Fetched("JP", indicator, mu.clean(points[tenor]), "D", "mof_japan", f"jgbcme_all.csv:{tenor}",
                   f"JGB {tenor} constant-maturity yield")
        for tenor, indicator in MOF_TENORS.items()
    ]


def fetch_mof_japan(start: date) -> list[mu.Fetched]:
    history = mof_series(mu.http_get(MOF_HISTORY_URL).content.decode("ascii", errors="ignore"), start)
    current = mof_series(mu.http_get(MOF_CURRENT_URL).content.decode("ascii", errors="ignore"), start)
    for old, new in zip(history, current):
        old.points = mu.clean(old.points + new.points)
    return history


# ------------------------------------------------------------------------------- RBA

RBA_URL = "https://www.rba.gov.au/statistics/tables/csv/{table}-data.csv"
RBA_TABLES = {
    # table -> {series id: (indicator, note)}
    "f1": {"FIRMMCRTD": ("POLICY_RATE", "RBA cash rate target")},
    "f2": {
        "FCMYGBAG2D": ("YIELD_2Y", "Australian government 2Y bond yield, interpolated"),
        "FCMYGBAG10D": ("YIELD_10Y", "Australian government 10Y bond yield, interpolated"),
    },
}


def rba_series(text: str, table: str) -> list[mu.Fetched]:
    """Parse an RBA statistical-table CSV: metadata rows, a `Series ID` row, d-Mon-Y rows."""
    wanted = RBA_TABLES[table]
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    ids = next((row for row in rows if row and row[0].strip() == "Series ID"), None)
    if ids is None:
        raise ValueError(f"RBA table {table}: 'Series ID' row not found")
    points: dict[str, list[tuple[date | None, float | None]]] = {code: [] for code in wanted}
    for row in rows:
        try:
            d = datetime.strptime(row[0].strip(), "%d-%b-%Y").date()
        except (ValueError, IndexError):
            continue
        for code in wanted:
            col = ids.index(code)
            points[code].append((d, mu.parse_float(row[col]) if col < len(row) else None))
    return [
        mu.Fetched("AU", indicator, mu.clean(points[code]), "D", "rba", f"{table.upper()}:{code}", note)
        for code, (indicator, note) in wanted.items()
    ]


def fetch_rba(start: date) -> list[mu.Fetched]:
    out: list[mu.Fetched] = []
    for table in RBA_TABLES:
        resp = mu.http_get(RBA_URL.format(table=table))
        out.extend(rba_series(resp.content.decode("utf-8", errors="ignore"), table))
    return out


# ------------------------------------------------------------------ Statistics Japan

ESTAT_URL = "https://dashboard.e-stat.go.jp/api/1.0/Json/getData"
# Statistics Dashboard indicator codes, 2025-base CPI, change from a year earlier.
ESTAT_SERIES = {
    "0703010601010030000": ("CPI_YOY", "national CPI, all items, y/y"),
    "0703010601010030010": ("CORE_CPI_YOY", "CPI all items less fresh food (the BoJ's core), y/y"),
}


def estat_points(payload: dict) -> mu.Points:
    stats = payload.get("GET_STATS", {})
    if str(stats.get("RESULT", {}).get("status")) != "0":
        raise ValueError(f"e-Stat dashboard error: {stats.get('RESULT')}")
    objs = stats.get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("DATA_OBJ", [])
    out: list[tuple[date | None, float | None]] = []
    for obj in objs:
        value = obj.get("VALUE", {})
        time = str(value.get("@time", ""))  # YYYYMM00
        d = mu.parse_period(f"{time[:4]}-{time[4:6]}") if len(time) >= 6 else None
        out.append((d, mu.parse_float(value.get("$"))))
    return mu.clean(out)


def fetch_estat_japan(start: date) -> list[mu.Fetched]:
    out: list[mu.Fetched] = []
    for code, (indicator, note) in ESTAT_SERIES.items():
        resp = mu.http_get(
            ESTAT_URL,
            params={
                "Lang": "EN", "IndicatorCode": code, "Cycle": "1", "RegionalRank": "2",
                "IsSeasonalAdjustment": "1", "TimeFrom": f"{start.year}{start.month:02d}00",
            },
        )
        out.append(mu.Fetched("JP", indicator, estat_points(resp.json()), "M", "estat_japan", code, note))
    return out


# -------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Provider:
    name: str
    fetch: Callable[[date], list[mu.Fetched]]
    # Daily API sources: (region, indicator) pairs they own, used for incremental fetches.
    daily_series: tuple[tuple[str, str], ...] = ()


PROVIDERS: list[Provider] = [
    Provider("oecd_kei", fetch_oecd_kei),
    Provider("oecd_cli", fetch_oecd_cli),
    Provider("oecd_prices", fetch_oecd_prices),
    Provider("bis_policy", fetch_bis_policy, tuple((r, "POLICY_RATE") for r in BIS_AREA)),
    Provider("bis_cpi", fetch_bis_cpi),
    Provider("eurostat", fetch_eurostat),
    Provider("ecb", fetch_ecb, (("EZ", "YIELD_2Y"), ("EZ", "YIELD_10Y"))),
    Provider("boc", fetch_boc),
    Provider("mof_japan", fetch_mof_japan),
    Provider("rba", fetch_rba),
    Provider("estat_japan", fetch_estat_japan),
]


def start_date(session: Session, daily_series: Iterable[tuple[str, str]]) -> date:
    """Full history, unless every daily series of the provider already has data."""
    lasts = [mu.last_stored_date(session, make_series_id(r, i)) for r, i in daily_series]
    if not lasts or any(last is None for last in lasts):
        return mu.FETCH_START
    return max(mu.FETCH_START, min(lasts) - timedelta(days=mu.DAILY_REVISION_DAYS))


def run(session: Session) -> IngestResult:
    out = mu.RunLog()
    for provider in PROVIDERS:
        try:
            items = provider.fetch(start_date(session, provider.daily_series))
        except Exception as exc:  # noqa: BLE001 - one bad provider must not fail the run
            out.fail(provider.name, exc)
            continue
        for item in items:
            try:
                out.save(session, item)
            except Exception as exc:  # noqa: BLE001
                out.fail(item.series_id, exc)
    if out.saved == 0:
        raise RuntimeError(f"no official macro data fetched: {out.message()}")
    return IngestResult(rows=out.rows, message=out.message())
