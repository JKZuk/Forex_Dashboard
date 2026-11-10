"""Fill a database with plausible SYNTHETIC data for every table scoring reads.

For tests and demos only - the numbers are random walks, not market data.

    cd backend
    DATABASE_URL=sqlite:////tmp/demo.db .venv/bin/python scripts/seed_synthetic.py [--years 7] [--seed 42] [--end 2026-11-10]

Refuses to write to anything but SQLite unless --force is given, so it cannot
pollute the real MySQL database by accident. Deliberately leaves realistic
gaps (e.g. no Chinese 2Y yield / COT, quarterly CPI for AU and NZ, a PMI proxy
with neutral=100) so graceful degradation is exercised.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.catalog import GLOBAL_SERIES, INDICATOR_BY_KEY  # noqa: E402
from app.db import upsert  # noqa: E402
from app.ingest import save_series  # noqa: E402
from app.models import CalendarEvent, CotPosition, NewsItem, NewsRegionSentiment  # noqa: E402
from app.regions import FX_USD_INDEX, REGIONS, fx_series_id, series_id  # noqa: E402

FX_START = {"EUR": 1.10, "JPY": 0.0075, "GBP": 1.27, "AUD": 0.66, "CAD": 0.74, "NZD": 0.61, "CNY": 0.14, "CHF": 1.10}
FX_VOL = {"CNY": 0.002, "JPY": 0.006}
GLOBAL_START = {"VIX": 18.0, "SPX": 4200.0, "HY_SPREAD": 4.0, "OIL": 75.0, "GOLD": 1900.0, "COPPER": 4.0, "US_10Y_REAL": 1.5}

# (mean, monthly shock sd, persistence) for monthly AR(1) macro series
MACRO = {
    "CPI_YOY": (2.6, 0.25, 0.95), "CORE_CPI_YOY": (2.7, 0.15, 0.96), "UNEMPLOYMENT": (4.5, 0.12, 0.97),
    "EMPLOYMENT_CHG": (25.0, 30.0, 0.2), "PMI": (51.0, 1.2, 0.85), "CONSUMER_CONF": (100.0, 3.0, 0.85),
    "RETAIL_SALES_YOY": (2.5, 1.2, 0.7), "INDUSTRIAL_PROD_YOY": (1.5, 1.5, 0.7),
    "TRADE_BALANCE": (0.0, 1.5, 0.8), "EXPORTS_YOY": (4.0, 4.0, 0.7), "IMPORTS_YOY": (4.0, 4.0, 0.7),
}
QUARTERLY = {"GDP_YOY": (1.8, 0.5, 0.8), "CURRENT_ACCOUNT_GDP": (0.0, 0.4, 0.92)}
REGION_BIAS = {  # structural offsets so regions differ
    "US": {"POLICY": 4.0, "GDP_YOY": 0.6, "CURRENT_ACCOUNT_GDP": -3.2, "TRADE_BALANCE": -70.0},
    "EZ": {"POLICY": 2.5, "GDP_YOY": -0.6, "CURRENT_ACCOUNT_GDP": 2.5, "TRADE_BALANCE": 15.0},
    "JP": {"POLICY": 0.3, "GDP_YOY": -0.9, "CURRENT_ACCOUNT_GDP": 3.8, "TRADE_BALANCE": -4.0, "CPI": -0.6},
    "UK": {"POLICY": 4.2, "GDP_YOY": -0.5, "CURRENT_ACCOUNT_GDP": -3.0, "TRADE_BALANCE": -15.0, "CPI": 0.6},
    "AU": {"POLICY": 3.8, "GDP_YOY": 0.2, "CURRENT_ACCOUNT_GDP": -1.0, "TRADE_BALANCE": 6.0},
    "CA": {"POLICY": 3.2, "GDP_YOY": 0.0, "CURRENT_ACCOUNT_GDP": -1.2, "TRADE_BALANCE": 0.5},
    "NZ": {"POLICY": 4.0, "GDP_YOY": 0.1, "CURRENT_ACCOUNT_GDP": -5.5, "TRADE_BALANCE": -0.8},
    "CN": {"POLICY": 3.2, "GDP_YOY": 3.2, "CURRENT_ACCOUNT_GDP": 1.8, "TRADE_BALANCE": 70.0, "CPI": -1.8},
    "CH": {"POLICY": 0.8, "GDP_YOY": -0.4, "CURRENT_ACCOUNT_GDP": 7.0, "TRADE_BALANCE": 4.0, "CPI": -1.6},
}
# series deliberately absent: free data has gaps
MISSING = {
    ("CN", "YIELD_2Y"), ("CN", "UNEMPLOYMENT"), ("CN", "EMPLOYMENT_CHG"), ("CN", "CORE_CPI_YOY"),
    ("NZ", "PMI"), ("NZ", "INDUSTRIAL_PROD_YOY"), ("CH", "RETAIL_SALES_YOY"), ("CH", "EMPLOYMENT_CHG"),
    ("JP", "CONSUMER_CONF"), ("EZ", "EMPLOYMENT_CHG"),
}
QUARTERLY_CPI = {"AU", "NZ"}
COT_CURRENCIES = ["USD", "EUR", "JPY", "GBP", "AUD", "CAD", "NZD", "CHF"]


def _bdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _month_starts(start: date, end: date, step: int = 1) -> list[date]:
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        if (m - 1) % step == 0:
            out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _ar1(rng: np.random.Generator, n: int, mean: float, sd: float, phi: float) -> np.ndarray:
    x = np.empty(n)
    level = mean + rng.normal(0, sd * 2)
    for i in range(n):
        level = mean + phi * (level - mean) + rng.normal(0, sd)
        x[i] = level
    return x


def _gbm(rng: np.random.Generator, n: int, start: float, vol: float, drift: float = 0.0) -> np.ndarray:
    return start * np.exp(np.cumsum(rng.normal(drift, vol, n)))


def seed(session: Session, end: date | None = None, years: int = 7, seed: int = 42) -> dict[str, int]:
    """Write synthetic data ending at `end` (default today). Returns row counts."""
    rng = np.random.default_rng(seed)
    end = end or date.today()
    start = date(end.year - years, end.month, 1)
    counts = {"observations": 0, "cot": 0, "news": 0, "calendar": 0}

    def save(sid: str, region: str, indicator: str, name: str, category: str, dates, values, freq: str, unit: str = "", note: str = "") -> None:
        counts["observations"] += save_series(
            session, series_id=sid, region=region, indicator=indicator, name=name, category=category,
            points=list(zip(dates, (float(v) for v in values))), unit=unit, frequency=freq,
            source="synthetic", source_code=sid, note=note)

    days = _bdays(start, end)
    n = len(days)

    # ---- FX (USD value of one unit) + dollar index
    log_sum = np.zeros(n)
    for region in REGIONS:
        ccy = region.currency
        if ccy == "USD":
            continue
        px = _gbm(rng, n, FX_START[ccy], FX_VOL.get(ccy, 0.0055))
        log_sum += np.log(px / px[0])
        save(fx_series_id(ccy), "FX", f"{ccy}USD", f"{ccy}/USD", "fx", days, px, "D")
    save(FX_USD_INDEX, "FX", "USD_INDEX", "US dollar index (synthetic basket)", "fx", days,
         100.0 * np.exp(-log_sum / (len(REGIONS) - 1)), "D")

    # ---- global gauges
    for sid, name in GLOBAL_SERIES.items():
        key = sid.split(".", 1)[1]
        if key == "VIX":
            vals = np.exp(_ar1(rng, n, np.log(18.0), 0.06, 0.97))
        elif key == "HY_SPREAD":
            vals = np.exp(_ar1(rng, n, np.log(4.0), 0.02, 0.99))
        elif key == "US_10Y_REAL":
            vals = _ar1(rng, n, 1.5, 0.04, 0.995)
        elif key == "SPX":
            vals = _gbm(rng, n, GLOBAL_START[key], 0.010, 0.0003)
        else:
            vals = _gbm(rng, n, GLOBAL_START[key], 0.015)
        save(sid, "GLOBAL", key, name, "risk", days, vals, "D")

    # ---- macro: monthly up to the last month a real feed would have (~6 weeks old)
    months = _month_starts(start, end - timedelta(days=38))
    quarters = _month_starts(start, end - timedelta(days=120), step=3)
    weeks = [d for d in days if d.weekday() == 5 - 1 and d <= end - timedelta(days=5)]  # Fridays

    for region in REGIONS:
        code, bias = region.code, REGION_BIAS[region.code]

        def put(key: str, dates, values, freq: str, note: str = "") -> None:
            if (code, key) in MISSING:
                return
            ind = INDICATOR_BY_KEY[key]
            save(series_id(code, key), code, key, f"{region.name} {ind.name}", ind.category, dates, values, freq, ind.unit, note)

        # policy rate: slow random walk in 25bp steps around the regional level
        steps = rng.choice([-0.25, 0.0, 0.25], size=len(months), p=[0.12, 0.76, 0.12])
        policy = np.clip(bias["POLICY"] + np.cumsum(steps) - np.cumsum(steps)[-1] * 0.5, -0.75, 8.0)
        put("POLICY_RATE", months, policy, "M")

        cpi_bias = bias.get("CPI", 0.0)
        for key, (mean, sd, phi) in MACRO.items():
            m = mean + (cpi_bias if "CPI" in key else 0.0) + (bias.get(key, 0.0) if key == "TRADE_BALANCE" else 0.0)
            scale = max(1.0, abs(bias["TRADE_BALANCE"]) / 6.0) if key == "TRADE_BALANCE" else 1.0
            if "CPI" in key and code in QUARTERLY_CPI:
                put(key, quarters, _ar1(rng, len(quarters), m, sd * 1.5, phi ** 3), "Q")
            elif key == "PMI" and code == "JP":
                put(key, months, _ar1(rng, len(months), 100.0, 0.5, phi), "M",
                    note="proxy: OECD business confidence, neutral=100")
            else:
                put(key, months, _ar1(rng, len(months), m, sd * scale, phi), "M")
        if code == "US":
            put("PCE_YOY", months, _ar1(rng, len(months), 2.4, 0.2, 0.95), "M")
            put("CORE_PCE_YOY", months, _ar1(rng, len(months), 2.5, 0.12, 0.96), "M")
            put("JOBLESS_CLAIMS", weeks, _ar1(rng, len(weeks), 225.0, 9.0, 0.9), "W")
        for key, (mean, sd, phi) in QUARTERLY.items():
            put(key, quarters, _ar1(rng, len(quarters), mean + bias.get(key, 0.0), sd, phi), "Q")

        # yields follow the policy rate; daily for the US, monthly elsewhere
        if code == "US":
            pol_daily = np.interp([d.toordinal() for d in days], [m.toordinal() for m in months], policy)
            put("YIELD_2Y", days, pol_daily + _ar1(rng, n, 0.1, 0.03, 0.99), "D")
            put("YIELD_10Y", days, pol_daily + _ar1(rng, n, 0.5, 0.035, 0.995), "D")
        else:
            put("YIELD_2Y", months, policy + _ar1(rng, len(months), 0.1, 0.12, 0.9), "M",
                note="proxy for 2Y yield" if code in ("NZ", "CH") else "")
            put("YIELD_10Y", months, policy + _ar1(rng, len(months), 0.6, 0.15, 0.92), "M")

    # ---- COT: weekly (Tuesdays), ~4 years
    tuesdays = [d for d in days if d.weekday() == 1 and d >= date(end.year - 4, end.month, 1)]
    cot_rows = []
    for ccy in COT_CURRENCIES:
        net_pct = np.clip(_ar1(rng, len(tuesdays), 0.0, 3.5, 0.97), -60, 60)
        oi = _ar1(rng, len(tuesdays), 200_000, 6_000, 0.95)
        for d, pct, o in zip(tuesdays, net_pct, oi):
            net = pct / 100.0 * o
            long_ = o * 0.35 + net / 2
            cot_rows.append({"currency": ccy, "report_date": d, "noncomm_long": float(long_),
                             "noncomm_short": float(long_ - net), "noncomm_net": float(net),
                             "open_interest": float(o), "net_pct_oi": float(pct)})
    counts["cot"] = upsert(session, CotPosition, cot_rows)

    # ---- news: last 12 days, region-tagged, some central-bank items
    end_dt = datetime(end.year, end.month, end.day, 12, 0)
    items, sentiments = [], []
    region_tone = {r.code: rng.normal(0, 0.25) for r in REGIONS}
    region_tone["GLOBAL"] = rng.normal(0, 0.2)
    tags = list(region_tone)
    for i in range(260):
        when = end_dt - timedelta(hours=float(rng.uniform(0, 12 * 24)))
        url = f"https://example.invalid/synthetic/{seed}/{i}"
        h = hashlib.sha1(url.encode()).hexdigest()
        kind = "central_bank" if rng.random() < 0.15 else "news"
        primary = tags[int(rng.integers(len(tags)))]
        tone = float(np.clip(region_tone[primary] + rng.normal(0, 0.45), -1, 1))
        items.append({"url_hash": h, "source": "synthetic", "kind": kind,
                      "title": f"Synthetic {kind.replace('_', ' ')} item {i} on {primary}", "summary": "",
                      "url": url, "published_at": when, "sentiment": tone, "engine": "synthetic"})
        sentiments.append({"url_hash": h, "region": primary, "score": tone, "published_at": when})
        if rng.random() < 0.3:
            other = tags[int(rng.integers(len(tags)))]
            if other != primary:
                sentiments.append({"url_hash": h, "region": other, "published_at": when,
                                   "score": float(np.clip(region_tone[other] + rng.normal(0, 0.45), -1, 1))})
    upsert(session, NewsItem, items)
    counts["news"] = upsert(session, NewsRegionSentiment, sentiments)

    # ---- calendar: a few past events with actual/forecast, some upcoming without
    templates = [("CPI y/y", "high", 2.8, 0.2, "%"), ("Unemployment Rate", "high", 4.3, 0.1, "%"),
                 ("Retail Sales m/m", "medium", 0.3, 0.3, "%"), ("Manufacturing PMI", "medium", 50.5, 0.6, "")]
    events = []
    for region in REGIONS[:7]:
        for j, (title, impact, level, sd, unit) in enumerate(templates):
            past = j % 2 == 0 or region.code in ("US", "EZ")
            when = end_dt + timedelta(days=float(-rng.uniform(1, 20) if past else rng.uniform(1, 10)))
            forecast, previous = level + rng.normal(0, sd), level + rng.normal(0, sd)
            actual = forecast + rng.normal(0, sd)
            eid = hashlib.sha1(f"synthetic|{region.code}|{title}|{when.date()}".encode()).hexdigest()
            events.append({"event_id": eid, "region": region.code, "title": f"{region.name} {title}",
                           "event_time": when, "time_known": 1, "impact": impact,
                           "actual": f"{actual:.1f}{unit}" if past else "", "forecast": f"{forecast:.1f}{unit}",
                           "previous": f"{previous:.1f}{unit}", "source": "synthetic"})
    counts["calendar"] = upsert(session, CalendarEvent, events)
    session.flush()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--years", type=int, default=7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--force", action="store_true", help="allow a non-SQLite database")
    args = parser.parse_args()

    from app.config import settings
    from app.db import init_db, session_scope

    if not settings.database_url.startswith("sqlite") and not args.force:
        print("Refusing to seed a non-SQLite database. Set DATABASE_URL=sqlite:///... (or pass --force).")
        return 2
    init_db()
    with session_scope() as session:
        counts = seed(session, end=args.end, years=args.years, seed=args.seed)
    print("Seeded synthetic data:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
