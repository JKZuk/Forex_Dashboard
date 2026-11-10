"""Economic calendar ingestion (free, keyless sources combined).

There is no perfect free calendar API, so several partial ones are merged:

* forexfactory   weekly JSON export: current week, all our currencies, impact,
                 forecast, previous. Main source for the week. Rate-limited
                 (HTTP 429): at most one request per run and per hour, enforced
                 with a small cache file (backend/.cache/).
* myfxbook       public calendar RSS: today's ACTUAL values, merged into the
                 ForexFactory rows (matched on region + time + previous/forecast).
* cb_schedule    central-bank decision dates from backend/config/calendar.yaml.
* bea / census / eurostat / ons   official release schedules (US GDP + PCE +
                 trade, US retail sales, euro-area HICP/GDP/unemployment, UK
                 CPI/labour/GDP/retail) for the weeks ForexFactory does not cover.
* fred           optional, only with FRED_API_KEY: exact US release dates.
* rule           APPROXIMATE US payrolls dates (first Friday), labelled as such.

Weeks covered by ForexFactory supersede the schedule sources (no duplicates).
Idempotent; values already stored (actual/forecast/previous) are never blanked.

NOTE: this module is named like the stdlib ``calendar``. Inside this package
always use absolute imports; nothing here needs the stdlib module.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, CONFIG_DIR, settings
from app.db import upsert
from app.ingest import IngestResult
from app.models import CalendarEvent
from app.regions import REGION_BY_CURRENCY, REGION_CODES

log = logging.getLogger("ingest.calendar")

CALENDAR_FILE = CONFIG_DIR / "calendar.yaml"
FF_CACHE = BACKEND_DIR / ".cache" / "forexfactory_thisweek.json"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
MYFXBOOK_URL = "https://www.myfxbook.com/rss/forex-economic-calendar-events"
BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"
CENSUS_URL = "https://www.census.gov/economic-indicators/calendar-listview.html"
EUROSTAT_URL = "https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=0&category=0"
ONS_URL = "https://api.beta.ons.gov.uk/v1/search/releases"
FRED_URL = "https://api.stlouisfed.org/fred/releases/dates"

SRC_FF, SRC_CB, SRC_BEA, SRC_CENSUS = "forexfactory", "cb_schedule", "bea", "census"
SRC_EUROSTAT, SRC_ONS, SRC_FRED, SRC_RULE = "eurostat", "ons", "fred", "rule"
SCHEDULE_SOURCES = [SRC_CB, SRC_BEA, SRC_CENSUS, SRC_EUROSTAT, SRC_ONS, SRC_FRED, SRC_RULE]

IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}
DEFAULTS: dict[str, Any] = {
    "lookback_days": 14,
    "lookahead_days": 42,
    "min_impact": "medium",
    "timeout_seconds": 25,
    "user_agent": "fx-dashboard/1.0 (personal local dashboard; calendar reader)",
    "forexfactory_min_interval_minutes": 60,
    "sources": {},
}
US_EASTERN = ZoneInfo("America/New_York")


@dataclass
class Event:
    source: str
    region: str
    title: str
    when: datetime  # UTC, naive. Midnight when only the date is known.
    time_known: bool = True
    impact: str = "medium"
    actual: str = ""
    forecast: str = ""
    previous: str = ""


@dataclass
class Actual:
    """A released value from myfxbook, to be merged into an existing event."""

    region: str
    title: str
    when: datetime
    impact: str
    previous: str
    forecast: str
    actual: str


# ------------------------------------------------------------------ helpers
def event_id(source: str, region: str, title: str, day: date, extra: str = "") -> str:
    key = f"{source}|{region}|{title}|{day.isoformat()}" + (f"|{extra}" if extra else "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def to_utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def local_to_utc(day: date, hhmm: str, tz: str | ZoneInfo) -> datetime:
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    hour, minute = (int(x) for x in hhmm.split(":")[:2])
    return to_utc_naive(datetime.combine(day, time(hour, minute), tzinfo=zone))


def _clean(value: Any, limit: int = 32) -> str:
    text = html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def week_key(day: date) -> date:
    """Sunday that starts the ForexFactory week containing ``day``."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def load_config(path: Path = CALENDAR_FILE) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    cfg = {**DEFAULTS, **(data.get("settings") or {})}
    cfg["central_banks"] = data.get("central_banks") or []
    return cfg


# ------------------------------------------------------------------ parsers (pure)
def parse_forexfactory(data: Iterable[dict[str, Any]]) -> list[Event]:
    """ForexFactory weekly export -> events for our regions (holidays dropped)."""
    events: list[Event] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        region = REGION_BY_CURRENCY.get(str(row.get("country", "")).upper())
        impact = str(row.get("impact", "")).lower()
        title = _clean(row.get("title"), 256)
        if region is None or impact not in IMPACT_RANK or not title:
            continue
        try:
            when = datetime.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        events.append(
            Event(
                SRC_FF, region.code, title, to_utc_naive(when), when.tzinfo is not None, impact,
                actual=_clean(row.get("actual")), forecast=_clean(row.get("forecast")),
                previous=_clean(row.get("previous")),
            )
        )
    return events


_MYFX_COUNTRIES = {
    "United States": "US", "Euro Area": "EZ", "European Union": "EZ", "Germany": "EZ", "France": "EZ",
    "Italy": "EZ", "Spain": "EZ", "Japan": "JP", "United Kingdom": "UK", "Australia": "AU", "Canada": "CA",
    "New Zealand": "NZ", "China": "CN", "Switzerland": "CH",
}
_TD_RE = re.compile(r"(?is)<td[^>]*>(.*?)</td>")


def parse_myfxbook(content: bytes | str) -> list[Actual]:
    """myfxbook calendar RSS -> released values (columns: time left, impact, previous, consensus, actual)."""
    out: list[Actual] = []
    for entry in feedparser.parse(content).entries:
        title = _clean(entry.get("title"), 256)
        country = next((c for c in _MYFX_COUNTRIES if title.startswith(c + " ")), None)
        stamp = entry.get("published_parsed")
        if not country or not stamp:
            continue
        summary = str(entry.get("summary") or "")
        cells = [_clean(c) for c in _TD_RE.findall(summary)]
        if len(cells) < 5:
            continue
        impact = re.search(r"sprite-(high|medium|low)-impact", summary)
        out.append(
            Actual(
                _MYFX_COUNTRIES[country], title[len(country) + 1 :], datetime(*stamp[:6]),
                impact.group(1) if impact else "low", previous=cells[2], forecast=cells[3], actual=cells[4],
            )
        )
    return out


def _norm_value(value: str) -> str:
    """'+0.30%' and '0.3 %' compare equal; '' stays ''."""
    text = value.strip().lower().replace(",", "").replace(" ", "").lstrip("+")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(.*)", text)
    if not m:
        return text
    return f"{float(m.group(1)):g}{m.group(2)}"


def match_actuals(events: list[dict[str, Any]], actuals: list[Actual], tolerance_minutes: int = 5) -> dict[str, str]:
    """Pair released values with stored events. Returns {event_id: actual}.

    Titles differ between providers ("CPI m/m" vs "Inflation Rate MoM"), so the
    match is on region + release time + identical previous value (and forecast
    when both have one). Ambiguous or unmatched values are skipped, never guessed.
    """
    result: dict[str, str] = {}
    tol = timedelta(minutes=tolerance_minutes)
    for ev in events:
        if ev.get("actual") or not ev.get("previous") or not ev.get("time_known"):
            continue
        candidates = [
            a for a in actuals
            if a.actual and a.region == ev["region"] and abs(a.when - ev["event_time"]) <= tol
            and _norm_value(a.previous) == _norm_value(ev["previous"])
            and (not a.forecast or not ev.get("forecast") or _norm_value(a.forecast) == _norm_value(ev["forecast"]))
        ]
        if len({_norm_value(c.actual) for c in candidates}) == 1:
            result[ev["event_id"]] = candidates[0].actual[:32]
    return result


def parse_central_banks(banks: Iterable[dict[str, Any]]) -> list[Event]:
    events: list[Event] = []
    for bank in banks or []:
        region = bank.get("region")
        if region not in REGION_CODES:
            continue
        base_title = str(bank.get("title") or f"{bank.get('bank', region)} rate decision")
        for key, suffix in (("dates", ""), ("unverified_dates", " (unverified date)")):
            for raw in bank.get(key) or []:
                try:
                    day = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
                except ValueError:
                    log.warning("calendar.yaml: bad date %r for %s", raw, bank.get("bank"))
                    continue
                if bank.get("time") and bank.get("tz"):
                    when, known = local_to_utc(day, str(bank["time"]), str(bank["tz"])), True
                else:
                    when, known = datetime.combine(day, time(0, 0)), False
                events.append(Event(SRC_CB, region, base_title + suffix, when, known, "high"))
    return events


_BEA_RELEASES: list[tuple[str, str, str]] = [  # (exact BEA key, title, impact)
    ("Gross Domestic Product", "GDP (BEA release)", "high"),
    ("Personal Income and Outlays", "PCE inflation / Personal Income and Outlays", "high"),
    ("U.S. International Trade in Goods and Services", "Trade balance (goods and services)", "medium"),
]


def parse_bea(data: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for key, title, impact in _BEA_RELEASES:
        for raw in (data.get(key) or {}).get("release_dates") or []:
            try:
                when = datetime.fromisoformat(str(raw))
            except ValueError:
                continue
            events.append(Event(SRC_BEA, "US", title, to_utc_naive(when), True, impact))
    return events


_CENSUS_RELEASES: list[tuple[str, str, str]] = [  # (name prefix, title, impact)
    ("Advance Monthly Sales for Retail", "Retail sales (advance)", "high"),
    ("Advance Report on Durable Goods", "Durable goods orders (advance)", "medium"),
    ("New Residential Construction", "Housing starts / building permits", "medium"),
]
_TR_RE = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")


def parse_census(page: str) -> list[Event]:
    """Census Bureau economic indicator calendar (HTML table, US Eastern times)."""
    events: list[Event] = []
    for row in _TR_RE.findall(page or ""):
        cells = [_clean(c, 200) for c in _TD_RE.findall(row)]
        if len(cells) < 3:
            continue
        match = next(((t, i) for p, t, i in _CENSUS_RELEASES if cells[0].startswith(p)), None)
        if not match:
            continue
        try:
            local = datetime.strptime(f"{cells[1]} {cells[2]}", "%B %d, %Y %I:%M %p")
        except ValueError:
            continue
        period = f" - {cells[3]}" if len(cells) > 3 and cells[3] else ""
        events.append(Event(SRC_CENSUS, "US", match[0] + period, to_utc_naive(local.replace(tzinfo=US_EASTERN)), True, match[1]))
    return events


_EUROSTAT_RELEASES: list[tuple[str, str, str]] = [  # (regex on SUMMARY, title, impact)
    (r"^Flash estimate inflation euro area", "Euro area flash HICP inflation", "high"),
    (r"^Inflation \(HICP\)$", "Euro area HICP inflation (final)", "medium"),
    (r"^Preliminary flash estimate GDP", "Euro area GDP (preliminary flash)", "high"),
    (r"^Flash estimate GDP and employment", "Euro area GDP and employment (flash)", "medium"),
    (r"^GDP main aggregates and employment$", "Euro area GDP (main aggregates)", "medium"),
    (r"^Unemployment$", "Euro area unemployment rate", "medium"),
]


def parse_eurostat(ics: str) -> list[Event]:
    """Eurostat release calendar (iCalendar, dates only: releases are normally at 11:00 CET)."""
    events: list[Event] = []
    text = re.sub(r"\r?\n[ \t]", "", ics or "")  # unfold continuation lines
    for block in re.findall(r"(?s)BEGIN:VEVENT(.*?)END:VEVENT", text):
        summary = re.search(r"(?m)^SUMMARY[^:\n]*:(.*)$", block)
        start = re.search(r"(?m)^DTSTART[^:\n]*:(\d{8})", block)
        if not summary or not start:
            continue
        name = summary.group(1).strip().replace("\\,", ",")
        match = next(((t, i) for rx, t, i in _EUROSTAT_RELEASES if re.search(rx, name)), None)
        if not match:
            continue
        day = datetime.strptime(start.group(1), "%Y%m%d")
        events.append(Event(SRC_EUROSTAT, "EZ", match[0], day, False, match[1]))
    return events


_ONS_RELEASES: list[tuple[str, str, str]] = [  # (regex on title, title, impact)
    (r"^Consumer price inflation, UK: (?P<p>.+)$", "CPI inflation", "high"),
    (r"^(?:UK Labour Market|Labour market overview, UK): (?P<p>.+)$", "Labour market (unemployment, wages)", "high"),
    (r"^GDP monthly estimate, UK: (?P<p>.+)$", "GDP monthly estimate", "high"),
    (r"^GDP first quarterly estimate, UK: (?P<p>.+)$", "GDP first quarterly estimate", "high"),
    (r"^Retail sales, Great Britain: (?P<p>.+)$", "Retail sales", "medium"),
]
ONS_QUERIES = ["Consumer price inflation", "Labour market", "GDP", "Retail sales"]


def parse_ons(data: dict[str, Any]) -> list[Event]:
    """ONS release-calendar search API response."""
    events: list[Event] = []
    for release in (data or {}).get("releases") or []:
        desc = release.get("description") or {}
        title = str(desc.get("title") or "")
        if desc.get("cancelled") or "time series" in title.lower():
            continue
        for rx, label, impact in _ONS_RELEASES:
            m = re.match(rx, title)
            if not m:
                continue
            try:
                when = datetime.fromisoformat(str(desc.get("release_date")).replace("Z", "+00:00"))
            except ValueError:
                break
            events.append(Event(SRC_ONS, "UK", f"{label} - {m.group('p')}"[:256], to_utc_naive(when), True, impact))
            break
    return events


_FRED_RELEASES: list[tuple[str, str, str, str]] = [  # (regex on release name, title, impact, ET time)
    (r"^Employment Situation$", "Employment Situation (Non-Farm Payrolls, unemployment)", "high", "08:30"),
    (r"^Consumer Price Index$", "CPI inflation", "high", "08:30"),
    (r"^Personal Income and Outlays$", "PCE inflation / Personal Income and Outlays", "high", "08:30"),
    (r"^Gross Domestic Product$", "GDP", "high", "08:30"),
    (r"^Advance Monthly Sales for Retail and Food Services$", "Retail sales (advance)", "high", "08:30"),
    (r"^Producer Price Index$", "PPI inflation", "medium", "08:30"),
    (r"^Job Openings and Labor Turnover Survey$", "JOLTS job openings", "medium", "10:00"),
    (r"^Unemployment Insurance Weekly Claims Report$", "Initial jobless claims", "medium", "08:30"),
    (r"^U\.S\. International Trade in Goods and Services$", "Trade balance (goods and services)", "medium", "08:30"),
    (r"^G\.17 Industrial Production and Capacity Utilization$", "Industrial production", "medium", "09:15"),
]


def parse_fred(data: dict[str, Any]) -> list[Event]:
    """FRED ``releases/dates`` response -> the big US releases at their usual Eastern time."""
    events: list[Event] = []
    for row in (data or {}).get("release_dates") or []:
        name = str(row.get("release_name") or "")
        match = next(((t, i, hm) for rx, t, i, hm in _FRED_RELEASES if re.search(rx, name)), None)
        if not match:
            continue
        try:
            day = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        events.append(Event(SRC_FRED, "US", match[0], local_to_utc(day, match[2], US_EASTERN), True, match[1]))
    return events


def nfp_rule(start: date, end: date) -> list[Event]:
    """APPROXIMATE US payrolls dates: first Friday of each month, 08:30 Eastern.

    The BLS moves the release around holidays (and after shutdowns), so the title
    says so. With a FRED key the exact schedule replaces this rule.
    """
    events: list[Event] = []
    month = date(start.year, start.month, 1)
    while month <= end:
        first_friday = month + timedelta(days=(4 - month.weekday()) % 7)
        if start <= first_friday <= end:
            events.append(
                Event(SRC_RULE, "US", "Non-Farm Payrolls (approx. date: first-Friday rule, check BLS)",
                      local_to_utc(first_friday, "08:30", US_EASTERN), True, "high")
            )
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return events


def build_rows(events: Iterable[Event], start: datetime, end: datetime, min_impact: str = "medium") -> list[dict[str, Any]]:
    """Filter to the window / impact floor and build CalendarEvent rows with stable ids."""
    floor = IMPACT_RANK.get(min_impact, 1)
    rows: dict[str, dict[str, Any]] = {}
    for ev in sorted(events, key=lambda e: (e.when, e.source, e.region, e.title)):
        if not (start <= ev.when <= end) or IMPACT_RANK.get(ev.impact, 0) < floor or ev.region not in REGION_CODES:
            continue
        eid = event_id(ev.source, ev.region, ev.title, ev.when.date())
        if eid in rows:  # same title twice on one day (two speeches): keep both
            eid = event_id(ev.source, ev.region, ev.title, ev.when.date(), ev.when.strftime("%H:%M"))
        rows[eid] = {
            "event_id": eid, "region": ev.region, "title": ev.title[:256], "event_time": ev.when,
            "time_known": 1 if ev.time_known else 0, "impact": ev.impact, "actual": ev.actual[:32],
            "forecast": ev.forecast[:32], "previous": ev.previous[:32], "source": ev.source,
        }
    return list(rows.values())


def keep_known_values(rows: list[dict[str, Any]], existing: dict[str, tuple[str, str, str]]) -> None:
    """Never blank a value we already have (an actual filled earlier must survive a re-run)."""
    for row in rows:
        old = existing.get(row["event_id"])
        if old:
            for col, value in zip(("actual", "forecast", "previous"), old):
                if not row[col] and value:
                    row[col] = value


# ------------------------------------------------------------------ network
def _get(url: str, cfg: dict[str, Any], **params: Any) -> requests.Response:
    resp = requests.get(
        url, params=params or None, timeout=cfg["timeout_seconds"],
        headers={"User-Agent": cfg["user_agent"], "Accept": "*/*"},
    )
    resp.raise_for_status()
    return resp


def _fetch_ons(cfg: dict[str, Any], end: date) -> list[Event]:
    events: list[Event] = []
    for query in ONS_QUERIES:
        params = {"query": query, "release-type": "type-upcoming", "toDate": end.isoformat(), "limit": 100}
        data = _get(ONS_URL, cfg, **params).json()  # the API only lists upcoming releases reliably
        events.extend(parse_ons(data))
    return events


def _fetch_fred(cfg: dict[str, Any], start: date, end: date) -> list[Event]:
    data = _get(
        FRED_URL, cfg, api_key=settings.fred_api_key, file_type="json", include_release_dates_with_no_data="true",
        realtime_start=start.isoformat(), realtime_end=end.isoformat(), sort_order="asc", limit=1000,
    ).json()
    return parse_fred(data)


def load_forexfactory(
    cfg: dict[str, Any], now: datetime, cache: Path | None = None,
    fetch: Callable[[], Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """ForexFactory weekly export with a small on-disk cache that enforces the rate limit.

    The export answers 429 when polled often, so: a cache younger than
    ``forexfactory_min_interval_minutes`` is used WITHOUT any request; otherwise
    one request is made and cached; if that fails, a cache up to 7 days old is
    used instead. Returns (rows, note for the run log).
    """
    cache = cache or FF_CACHE
    fetch = fetch or (lambda: _get(FF_URL, cfg).json())
    cached: list[dict[str, Any]] | None = None
    age_min = float("inf")
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            age_min = (now - datetime.utcfromtimestamp(cache.stat().st_mtime)).total_seconds() / 60
        except (OSError, ValueError):
            cached = None
    if isinstance(cached, list) and 0 <= age_min < float(cfg["forexfactory_min_interval_minutes"]):
        return cached, f"forexfactory: cached copy ({age_min:.0f} min old, not re-fetched)"
    try:
        data = fetch()
        if not isinstance(data, list):
            raise ValueError("unexpected payload")
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write %s: %s", cache, exc)
        return data, "forexfactory: fetched"
    except Exception as exc:  # noqa: BLE001
        if isinstance(cached, list) and age_min < 7 * 24 * 60:
            return cached, f"forexfactory: fetch failed ({type(exc).__name__}), used {age_min / 60:.1f} h old cache"
        raise


# ------------------------------------------------------------------ DB steps
def _save(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ids = [r["event_id"] for r in rows]
    existing: dict[str, tuple[str, str, str]] = {}
    for i in range(0, len(ids), 500):
        stmt = select(CalendarEvent.event_id, CalendarEvent.actual, CalendarEvent.forecast, CalendarEvent.previous).where(
            CalendarEvent.event_id.in_(ids[i : i + 500])
        )
        existing.update({eid: (a or "", f or "", p or "") for eid, a, f, p in session.execute(stmt)})
    keep_known_values(rows, existing)
    return upsert(session, CalendarEvent, rows)


def _drop_stale_future(session: Session, source: str, rows: list[dict[str, Any]], now: datetime) -> None:
    """A source's schedule was re-read in full: forget its future rows that are no longer listed."""
    ids = {r["event_id"] for r in rows}
    stmt = select(CalendarEvent.event_id).where(CalendarEvent.source == source, CalendarEvent.event_time >= now)
    gone = [eid for (eid,) in session.execute(stmt) if eid not in ids]
    for i in range(0, len(gone), 500):
        session.execute(delete(CalendarEvent).where(CalendarEvent.event_id.in_(gone[i : i + 500])))


def _ff_weeks(session: Session) -> set[date]:
    stmt = select(CalendarEvent.event_time).where(CalendarEvent.source == SRC_FF)
    return {week_key(t.date()) for (t,) in session.execute(stmt)}


def _prune_superseded(session: Session, weeks: set[date]) -> int:
    """Delete schedule-source rows inside weeks that ForexFactory covers (it has the richer rows)."""
    removed = 0
    for wk in weeks:
        lo = datetime.combine(wk, time(0, 0))
        result = session.execute(
            delete(CalendarEvent).where(
                CalendarEvent.source.in_(SCHEDULE_SOURCES), CalendarEvent.event_time >= lo,
                CalendarEvent.event_time < lo + timedelta(days=7),
            )
        )
        removed += result.rowcount or 0
    return removed


def run(session: Session) -> IngestResult:
    cfg = load_config()
    enabled: Callable[[str], bool] = lambda name: (cfg.get("sources") or {}).get(name, True) is not False  # noqa: E731
    now = datetime.utcnow()
    start = now - timedelta(days=int(cfg["lookback_days"]))
    end = now + timedelta(days=int(cfg["lookahead_days"]))
    min_impact = str(cfg["min_impact"]).lower()
    notes: list[str] = []
    failures: list[str] = []
    total = 0

    # 1. ForexFactory: this week, the richest rows. At most one request per run / per hour.
    if enabled("forexfactory"):
        try:
            data, note = load_forexfactory(cfg, now)
            events = parse_forexfactory(data)
            rows = build_rows(events, start, end, min_impact)
            total += _save(session, rows)
            notes.append(f"{note} {len(events)} events, kept {len(rows)}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"forexfactory ({type(exc).__name__}: {str(exc)[:80]})")

    # 2. Schedule sources for the weeks ForexFactory does not cover.
    use_fred = enabled("fred") and bool(settings.fred_api_key)
    jobs: list[tuple[str, str, Callable[[], list[Event]]]] = []
    if enabled("central_banks"):
        jobs.append(("central_banks", SRC_CB, lambda: parse_central_banks(cfg["central_banks"])))
    if use_fred:
        jobs.append(("fred", SRC_FRED, lambda: _fetch_fred(cfg, start.date(), end.date())))
    if enabled("bea"):
        jobs.append(("bea", SRC_BEA, lambda: parse_bea(_get(BEA_URL, cfg).json())))
    if enabled("census"):
        jobs.append(("census", SRC_CENSUS, lambda: parse_census(_get(CENSUS_URL, cfg).text)))
    if enabled("eurostat"):
        jobs.append(("eurostat", SRC_EUROSTAT, lambda: parse_eurostat(_get(EUROSTAT_URL, cfg).text)))
    if enabled("ons"):
        jobs.append(("ons", SRC_ONS, lambda: _fetch_ons(cfg, end.date())))
    if enabled("nfp_rule"):
        jobs.append(("nfp_rule", SRC_RULE, lambda: nfp_rule(start.date(), end.date())))

    weeks = _ff_weeks(session)
    fred_ok = False
    for name, source, fetch in jobs:
        if fred_ok and source in (SRC_BEA, SRC_CENSUS, SRC_RULE):
            session.execute(delete(CalendarEvent).where(CalendarEvent.source == source, CalendarEvent.event_time >= now))
            continue  # FRED has the exact US schedule
        try:
            events = [e for e in fetch() if week_key(e.when.date()) not in weeks]
            rows = build_rows(events, start, end, min_impact)
            total += _save(session, rows)
            _drop_stale_future(session, source, rows, now)
            notes.append(f"{name}: {len(rows)}")
            fred_ok = fred_ok or source == SRC_FRED
        except Exception as exc:  # noqa: BLE001 - one source failing must not fail the run
            failures.append(f"{name} ({type(exc).__name__}: {str(exc)[:80]})")
    pruned = _prune_superseded(session, weeks)
    if pruned:
        notes.append(f"superseded by forexfactory: {pruned}")

    # 3. Today's actual values.
    if enabled("myfxbook_actuals"):
        try:
            actuals = parse_myfxbook(_get(MYFXBOOK_URL, cfg).content)
            day_lo = now - timedelta(days=2)
            stmt = select(CalendarEvent).where(
                CalendarEvent.source == SRC_FF, CalendarEvent.event_time >= day_lo, CalendarEvent.event_time <= now
            )
            stored = [
                {"event_id": e.event_id, "region": e.region, "event_time": e.event_time, "time_known": e.time_known,
                 "actual": e.actual, "forecast": e.forecast, "previous": e.previous}
                for e in session.execute(stmt).scalars()
            ]
            filled = match_actuals(stored, actuals)
            for eid, value in filled.items():
                session.execute(update(CalendarEvent).where(CalendarEvent.event_id == eid).values(actual=value))
            notes.append(f"myfxbook actuals: {len(filled)} filled")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"myfxbook ({type(exc).__name__}: {str(exc)[:80]})")

    # 4. Housekeeping: forget events that fell out of the window long ago.
    session.execute(delete(CalendarEvent).where(CalendarEvent.event_time < now - timedelta(days=120)))

    if not notes and failures:
        raise RuntimeError("all calendar sources failed: " + "; ".join(failures))
    message = "; ".join(notes)
    if not use_fred:
        message += "; fred: off (no FRED_API_KEY)"
    if failures:
        message += "; failed: " + "; ".join(failures)
    return IngestResult(rows=total, message=message)
