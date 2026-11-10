"""Fill a database with plausible DEMO rows in every table the API reads.

For UI development and screenshots only: every number is synthetic.

    cd backend
    DATABASE_URL=sqlite:///./demo.db .venv/bin/python scripts/seed_demo.py
    DATABASE_URL=sqlite:///./demo.db .venv/bin/uvicorn app.main:app --port 8000

Safety: refuses to write to a non-SQLite database, or to one that already has
scores, unless --force is given (so it cannot silently pollute the real MySQL
schema). --reset empties all tables first.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import catalog, regions as reg  # noqa: E402
from app.db import Base, upsert  # noqa: E402
from app.ingest import SOURCES, save_series  # noqa: E402
from app.models import (  # noqa: E402
    CalendarEvent,
    Conclusion,
    CotPosition,
    NewsItem,
    NewsRegionSentiment,
    RefreshRun,
    Score,
    ScoreComponent,
)

HORIZONS = ["short", "medium", "long"]
WEEKS = 106

FX_START = {"EUR": 1.08, "JPY": 0.0068, "GBP": 1.27, "AUD": 0.66, "CAD": 0.73, "NZD": 0.60,
            "CNY": 0.139, "CHF": 1.13, "USD": 103.0}

# where each region's demo score ends up: (short, medium, long)
TARGETS = {
    "US": (34, 22, 8), "EZ": (-18, -6, 12), "JP": (-52, -30, 18), "UK": (9, -12, -20),
    "AU": (41, 27, 15), "CA": (-27, -35, -10), "NZ": (14, 5, -8), "CN": (-22, -38, -45),
    "CH": (58, 31, 24),
}

COMPONENTS = {
    "short": [
        ("price_momentum", "Price momentum (1-3 months)", 0.20, "{:+.1f}% vs 50-day average"),
        ("yield_differential", "2Y yield differential", 0.20, "{:+.2f} pp vs G10 average"),
        ("news_sentiment", "News sentiment (7 days)", 0.15, "{:+.2f} average tone"),
        ("data_surprise", "Recent data momentum", 0.15, "{:+.1f} z-score"),
        ("cot_positioning", "Speculative positioning (COT)", 0.15, "{:+.1f}% of open interest"),
        ("risk_sentiment", "Global risk appetite", 0.10, "VIX {:.1f}"),
        ("central_bank_tone", "Central bank communication", 0.05, "{:+.2f} tone"),
    ],
    "medium": [
        ("policy_path", "Policy rate trajectory", 0.22, "{:+.2f} pp over 6 months"),
        ("yield_differential", "2Y yield differential", 0.18, "{:+.2f} pp vs G10 average"),
        ("inflation_trend", "Inflation vs target", 0.15, "{:+.1f} pp above target"),
        ("growth_momentum", "Growth momentum (PMI, retail)", 0.15, "PMI {:.1f}"),
        ("labour_market", "Labour market", 0.12, "Unemployment {:.1f}%"),
        ("trade_balance", "Trade balance trend", 0.10, "{:+.1f} bn, 3-month average"),
        ("cot_positioning", "Speculative positioning (COT)", 0.08, "{:+.1f}% of open interest"),
    ],
    "long": [
        ("real_yield", "Real yield differential", 0.25, "{:+.2f} pp real 10Y"),
        ("current_account", "Current account balance", 0.20, "{:+.1f}% of GDP"),
        ("growth_trend", "Trend GDP growth", 0.20, "{:+.1f}% y/y"),
        ("terms_of_trade", "Terms of trade / exports", 0.15, "Exports {:+.1f}% y/y"),
        ("valuation", "Long-run valuation (5Y range)", 0.12, "{:.0f}th percentile of 5Y range"),
        ("inflation_trend", "Inflation vs target", 0.08, "{:+.1f} pp above target"),
    ],
}
# components shown as "no data" for some regions, to exercise the greyed state
MISSING = {
    "CN": {"cot_positioning", "yield_differential", "labour_market", "real_yield", "central_bank_tone",
           "risk_sentiment"},
    "CH": {"data_surprise", "growth_momentum"},
    "NZ": {"central_bank_tone"},
}

# indicator -> (level, noise) ; None level = not published in the demo
MACRO = {
    "POLICY_RATE": {"US": 4.5, "EZ": 2.5, "JP": 0.5, "UK": 4.25, "AU": 3.85, "CA": 2.75, "NZ": 3.25, "CN": 3.1, "CH": 0.25},
    "CPI_YOY": {"US": 2.9, "EZ": 2.2, "JP": 3.1, "UK": 3.4, "AU": 2.6, "CA": 2.1, "NZ": 2.4, "CN": 0.2, "CH": 0.4},
    "CORE_CPI_YOY": {"US": 3.2, "EZ": 2.5, "JP": 2.6, "UK": 3.6, "AU": 2.9, "CA": 2.6},
    "CORE_PCE_YOY": {"US": 2.8},
    "UNEMPLOYMENT": {"US": 4.2, "EZ": 6.3, "JP": 2.5, "UK": 4.5, "AU": 4.1, "CA": 6.8, "NZ": 5.1, "CH": 2.8},
    "EMPLOYMENT_CHG": {"US": 150.0, "AU": 25.0, "CA": 20.0},
    "PMI": {"US": 51.2, "EZ": 48.9, "JP": 49.5, "UK": 50.4, "AU": 50.9, "CN": 49.6},
    "RETAIL_SALES_YOY": {"US": 3.4, "EZ": 1.6, "JP": 2.0, "UK": 1.1, "AU": 3.0, "CA": 2.4, "CN": 4.2},
    "GDP_YOY": {"US": 2.3, "EZ": 1.1, "JP": 0.9, "UK": 1.0, "AU": 1.5, "CA": 1.4, "NZ": 0.4, "CN": 4.8, "CH": 1.3},
    "YIELD_2Y": {"US": 4.0, "EZ": 2.1, "JP": 0.8, "UK": 4.0, "AU": 3.6, "CA": 2.7, "NZ": 3.3, "CH": 0.2},
    "YIELD_10Y": {"US": 4.3, "EZ": 2.6, "JP": 1.5, "UK": 4.6, "AU": 4.3, "CA": 3.2, "NZ": 4.5, "CN": 1.7, "CH": 0.4},
    "TRADE_BALANCE": {"US": -75.0, "EZ": 14.0, "JP": -350.0, "UK": -4.5, "AU": 5.2, "CA": -0.8, "NZ": -0.4, "CN": 95.0, "CH": 4.1},
    "EXPORTS_YOY": {"US": 3.0, "EZ": 1.5, "JP": 4.5, "AU": -2.5, "CA": 1.2, "NZ": 6.0, "CN": 5.5},
    "IMPORTS_YOY": {"US": 4.5, "EZ": 0.8, "JP": 1.8, "AU": 3.5, "CA": 2.0, "NZ": 2.2, "CN": 1.0},
    "CURRENT_ACCOUNT_GDP": {"US": -3.6, "EZ": 2.6, "JP": 4.4, "UK": -2.8, "AU": -1.4, "CA": -0.9, "NZ": -6.2, "CN": 2.0, "CH": 6.5},
}
TRADE_UNIT = {"US": "USD bn", "EZ": "EUR bn", "JP": "JPY bn", "UK": "GBP bn", "AU": "AUD bn",
              "CA": "CAD bn", "NZ": "NZD bn", "CN": "USD bn", "CH": "CHF bn"}

GLOBAL_LEVELS = {"GLOBAL.VIX": (17.0, 0.05), "GLOBAL.SPX": (5600.0, 0.009), "GLOBAL.HY_SPREAD": (3.3, 0.015),
                 "GLOBAL.OIL": (72.0, 0.018), "GLOBAL.GOLD": (2650.0, 0.009), "GLOBAL.COPPER": (4.3, 0.013),
                 "GLOBAL.US_10Y_REAL": (1.9, 0.012)}

NEWS_TEMPLATES = [
    ("{cb} officials signal rates may stay higher for longer", "central_bank", 0.55),
    ("{cb} minutes show growing support for a rate cut", "central_bank", -0.5),
    ("{name} inflation surprises to the upside in latest print", "news", 0.45),
    ("{name} retail sales slump as consumers pull back", "news", -0.45),
    ("{name} labour market stays tight, wages accelerate", "news", 0.4),
    ("{name} factory activity contracts for a third month", "news", -0.4),
    ("{name} trade surplus widens on strong exports", "news", 0.35),
    ("{ccy} slides as traders trim positions ahead of data", "news", -0.3),
    ("{name} growth beats forecasts, {ccy} firms", "news", 0.5),
    ("Analysts see {ccy} range-bound as markets await {cb}", "news", 0.0),
]
CB = {"US": "Fed", "EZ": "ECB", "JP": "BoJ", "UK": "BoE", "AU": "RBA", "CA": "BoC", "NZ": "RBNZ",
      "CN": "PBoC", "CH": "SNB"}
GLOBAL_NEWS = [
    ("Global stocks rally as risk appetite returns", 0.5),
    ("Oil jumps on supply concerns; commodity currencies bid", 0.3),
    ("Tariff headlines rattle markets, havens in demand", -0.55),
    ("Volatility eases as earnings season beats expectations", 0.35),
    ("Credit spreads widen on growth worries", -0.4),
]
EVENTS = [
    ("{cb} interest rate decision", "high"), ("CPI inflation y/y", "high"), ("Unemployment rate", "high"),
    ("GDP q/q (preliminary)", "high"), ("Retail sales m/m", "medium"), ("Manufacturing PMI", "medium"),
    ("Trade balance", "medium"), ("{cb} governor speech", "medium"), ("Consumer confidence", "low"),
    ("Industrial production m/m", "low"),
]


def label_for(score: float) -> str:
    if score >= 50:
        return "Strong Bullish"
    if score >= 15:
        return "Bullish"
    if score > -15:
        return "Neutral"
    if score > -50:
        return "Bearish"
    return "Strong Bearish"


def _sha(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _months(end: date, n: int) -> list[date]:
    out, y, m = [], end.year, end.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(date(y, m, 1))
    return list(reversed(out))


def reset(session: Session) -> None:
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))


def seed(session: Session, today: date | None = None, seed_value: int = 7) -> dict[str, int]:
    """Insert the demo data set. Returns row counts per table."""
    rng = random.Random(seed_value)
    today = today or date.today()
    now = datetime.utcnow().replace(microsecond=0)
    counts: dict[str, int] = {}

    # ---- FX daily prices + global gauges -----------------------------------
    days = _business_days(today - timedelta(days=365 * 3), today)
    n = 0
    for r in reg.REGIONS:
        level, drift = FX_START[r.currency], TARGETS[r.code][1] / 100 * 0.00025
        vol = 0.003 if r.currency in ("USD", "CNY") else 0.0055
        points = []
        for d in days:
            level *= math.exp(rng.gauss(drift, vol))
            points.append((d, round(level, 6 if level < 0.1 else 4)))
        n += save_series(
            session, series_id=reg.fx_series_id(r.currency), region="FX", indicator=f"{r.currency}USD",
            name="US dollar index" if r.currency == "USD" else f"{r.currency}/USD spot",
            category="fx", points=points, unit="index" if r.currency == "USD" else "USD",
            frequency="D", source="demo",
        )
    for sid, (level, vol) in GLOBAL_LEVELS.items():
        points = []
        for d in days[-520:]:
            level *= math.exp(rng.gauss(0, vol))
            points.append((d, round(level, 2)))
        n += save_series(
            session, series_id=sid, region="GLOBAL", indicator=sid.split(".", 1)[1],
            name=catalog.GLOBAL_SERIES[sid], category="risk", points=points, frequency="D", source="demo",
        )

    # ---- macro series (monthly; quarterly for GDP / current account) --------
    months = _months(today, 72)
    for key, by_region in MACRO.items():
        ind = catalog.INDICATOR_BY_KEY[key]
        quarterly = key in ("GDP_YOY", "CURRENT_ACCOUNT_GDP")
        for code, level in by_region.items():
            scale = max(abs(level), 1.0)
            noise = {"POLICY_RATE": 0.0, "TRADE_BALANCE": 0.18, "EMPLOYMENT_CHG": 0.5,
                     "EXPORTS_YOY": 0.8, "IMPORTS_YOY": 0.8}.get(key, 0.06)
            phase, value, points = rng.uniform(0, 6.28), level, []
            for i, m in enumerate(months):
                if quarterly and m.month % 3 != 1:
                    continue
                back = len(months) - 1 - i
                cycle = math.sin(i / 9.0 + phase) * scale * (0.9 if key.endswith("_YOY") else 0.25)
                if key == "POLICY_RATE":
                    value = level + round(min(back, 30) / 6) * (0.25 if code not in ("JP", "CH") else -0.05)
                    value = max(value, -0.25)
                else:
                    value = level + cycle * min(1.0, back / 6 + 0.15) + rng.gauss(0, scale * noise)
                points.append((m, round(value, 2)))
            n += save_series(
                session, series_id=reg.series_id(code, key), region=code, indicator=key, name=ind.name,
                category=ind.category, points=points,
                unit=TRADE_UNIT[code] if key == "TRADE_BALANCE" else ind.unit,
                frequency="Q" if quarterly else "M", source="demo",
                note="Tankan-style survey, 0 = neutral" if (key, code) == ("PMI", "JP") else "",
            )
    counts["observations"] = n

    # ---- scores: weekly history ending today --------------------------------
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    weekly = [last_friday - timedelta(weeks=i) for i in range(WEEKS)][::-1]
    component_dates = weekly[-2:] + [today]
    score_rows, comp_rows, concl_rows = [], [], []
    for r in reg.REGIONS:
        missing = MISSING.get(r.code, set())
        finals: dict[str, dict[date, float]] = {}
        for hi, h in enumerate(HORIZONS):
            target = TARGETS[r.code][hi]
            smooth = (0.80, 0.90, 0.96)[hi]
            shock = (14.0, 8.0, 4.0)[hi]
            x, path = rng.uniform(-40, 40), []
            for i in range(WEEKS):
                pull = target if i > WEEKS - 30 else math.sin(i / (10.0 + 8 * hi) + hi + len(r.code)) * 45
                x = smooth * x + (1 - smooth) * pull + rng.gauss(0, shock)
                x = max(-95.0, min(95.0, x))
                path.append(x)
            templates = COMPONENTS[h]
            have = [t for t in templates if t[0] not in missing]
            coverage = sum(t[2] for t in have) / sum(t[2] for t in templates)
            finals[h] = {}
            for d in component_dates:
                base = (path[weekly.index(d)] if d in weekly else path[-1] + rng.gauss(0, shock / 3)) / 100
                total = 0.0
                for comp, label, weight, fmt in templates:
                    if comp in missing:
                        comp_rows.append(dict(
                            region=r.code, as_of=d, horizon=h, component=comp, label=label, raw_display="",
                            signal=None, weight=weight, contribution=0.0,
                            detail="No free data source available for this driver in this region.",
                        ))
                        continue
                    signal = max(-1.0, min(1.0, base + rng.gauss(0, 0.45)))
                    contribution = signal * weight / (coverage * sum(t[2] for t in templates)) * 100
                    total += contribution
                    raw_value = {"risk_sentiment": 17 - signal * 6, "growth_momentum": 50 + signal * 4,
                                 "labour_market": 4.5 - signal, "valuation": 50 - signal * 40}.get(comp, signal * 2.4)
                    direction = "supports" if signal > 0.1 else "weighs on" if signal < -0.1 else "is neutral for"
                    comp_rows.append(dict(
                        region=r.code, as_of=d, horizon=h, component=comp, label=label,
                        raw_display=fmt.format(raw_value), signal=round(signal, 3), weight=weight,
                        contribution=round(contribution, 2),
                        detail=f"{label} {direction} {r.currency}: signal {signal:+.2f} on a -1..+1 scale, "
                               f"weight {weight:.0%} of the {h}-term score.",
                    ))
                finals[h][d] = max(-100.0, min(100.0, total))
            for i, d in enumerate(weekly):
                s = finals[h].get(d, path[i])
                score_rows.append(dict(region=r.code, as_of=d, horizon=h, score=round(s, 1),
                                       label=label_for(s), coverage=round(coverage, 3)))
            if today not in weekly:
                s = finals[h][today]
                score_rows.append(dict(region=r.code, as_of=today, horizon=h, score=round(s, 1),
                                       label=label_for(s), coverage=round(coverage, 3)))
        for d in component_dates:
            s, m, lg = (finals[h][d] for h in HORIZONS)
            tone = label_for(s).lower()
            concl_rows.append(dict(
                region=r.code, as_of=d,
                headline=f"{r.currency} {tone} near term; {label_for(m).lower()} medium-term backdrop",
                text=(
                    f"Short-term sentiment for {r.currency} is {tone} ({s:+.0f}), led by momentum, rate "
                    f"differentials and news flow. Over the medium term the picture is {label_for(m).lower()} "
                    f"({m:+.0f}) as the policy path and growth data dominate. Structurally the outlook is "
                    f"{label_for(lg).lower()} ({lg:+.0f}), reflecting real yields and the external balance. "
                    "Demo text generated by scripts/seed_demo.py - not real analysis."
                ),
            ))
    counts["scores"] = upsert(session, Score, score_rows)
    counts["score_components"] = upsert(session, ScoreComponent, comp_rows)
    counts["conclusions"] = upsert(session, Conclusion, concl_rows)

    # ---- COT (weekly, Tuesdays, ~3.5 years; no CNY futures) -----------------
    last_tue = today - timedelta(days=(today.weekday() - 1) % 7)
    cot_rows = []
    for r in reg.REGIONS:
        if r.currency == "CNY":
            continue
        oi = {"EUR": 700e3, "JPY": 300e3, "GBP": 220e3, "AUD": 190e3, "CAD": 230e3,
              "NZD": 60e3, "CHF": 80e3, "USD": 40e3}[r.currency]
        x = rng.uniform(-0.1, 0.1)
        end_bias = {"JPY": -0.42, "CHF": 0.36, "AUD": 0.2}.get(r.currency, TARGETS[r.code][0] / 400)
        for i in range(184):
            d = last_tue - timedelta(weeks=183 - i)
            pull = end_bias if i > 160 else math.sin(i / 17.0 + len(r.currency)) * 0.15
            x = 0.9 * x + 0.1 * pull + rng.gauss(0, 0.025)
            this_oi = oi * (1 + 0.15 * math.sin(i / 23.0)) * math.exp(rng.gauss(0, 0.02))
            net = round(x * this_oi)
            gross = this_oi * 0.45
            cot_rows.append(dict(
                currency=r.currency, report_date=d, noncomm_long=round((gross + net) / 2),
                noncomm_short=round((gross - net) / 2), noncomm_net=float(net),
                open_interest=round(this_oi), net_pct_oi=round(net / this_oi * 100, 2),
            ))
    counts["cot_positions"] = upsert(session, CotPosition, cot_rows)

    # ---- news + per-region sentiment ---------------------------------------
    news_rows, sent_rows = [], []
    for i in range(70):
        published = now - timedelta(hours=rng.uniform(0.3, 120))
        url = f"https://example.com/demo-news/{i}"
        h = _sha(url)
        if i % 6 == 0:
            title, tone = rng.choice(GLOBAL_NEWS)
            kind = "news"
            sent_rows.append(dict(url_hash=h, region="GLOBAL", score=round(tone + rng.gauss(0, 0.1), 3), published_at=published))
            for code in ("AU", "NZ", "CA"):  # commodity bloc tracks risk appetite
                sent_rows.append(dict(url_hash=h, region=code, score=round(tone * 0.6, 3), published_at=published))
            for code in ("JP", "CH"):
                sent_rows.append(dict(url_hash=h, region=code, score=round(-tone * 0.5, 3), published_at=published))
        else:
            r = reg.REGIONS[i % len(reg.REGIONS)]
            template, kind, tone = rng.choice(NEWS_TEMPLATES)
            title = template.format(cb=CB[r.code], name=r.name, ccy=r.currency)
            tone = max(-1.0, min(1.0, tone + rng.gauss(0, 0.12)))
            sent_rows.append(dict(url_hash=h, region=r.code, score=round(tone, 3), published_at=published))
            if r.code == "CN":  # China as an influence on AUD / NZD
                for code in ("AU", "NZ"):
                    sent_rows.append(dict(url_hash=h, region=code, score=round(tone * 0.5, 3), published_at=published))
        news_rows.append(dict(
            url_hash=h, source=rng.choice(["Demo Wire", "Demo FX Daily", "Central Bank RSS (demo)"]),
            kind=kind, title=title,
            summary="Synthetic headline produced by scripts/seed_demo.py for UI development.",
            url=url, published_at=published, sentiment=round(tone, 3), engine="lexicon",
        ))
    counts["news_items"] = upsert(session, NewsItem, news_rows)
    counts["news_region_sentiment"] = upsert(session, NewsRegionSentiment, sent_rows)

    # ---- calendar: 3 past days (with actuals) + 4 weeks ahead ---------------
    event_rows = []
    midnight = datetime(today.year, today.month, today.day)
    for offset in range(-3, 29):
        day = midnight + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for _ in range(rng.randint(2, 5)):
            r = rng.choice(reg.REGIONS)
            template, impact = rng.choice(EVENTS)
            title = template.format(cb=CB[r.code])
            time_known = 0 if "speech" in title and rng.random() < 0.5 else 1
            when = day + (timedelta(hours=rng.choice([1, 7, 9, 12, 13, 15, 19]), minutes=rng.choice([0, 30]))
                          if time_known else timedelta())
            numeric = not any(k in title for k in ("speech",))
            prev = f"{rng.uniform(-1, 5):.1f}%" if numeric else ""
            event_rows.append(dict(
                event_id=_sha("demo", r.code, title, when.date()), region=r.code, title=title,
                event_time=when, time_known=time_known, impact=impact,
                actual=f"{rng.uniform(-1, 5):.1f}%" if numeric and when < now else "",
                forecast=f"{rng.uniform(-1, 5):.1f}%" if numeric and rng.random() < 0.8 else "",
                previous=prev, source="demo",
            ))
    by_id = {e["event_id"]: e for e in event_rows}
    counts["calendar_events"] = upsert(session, CalendarEvent, list(by_id.values()))

    # ---- refresh log -------------------------------------------------------
    started = now - timedelta(minutes=42)
    runs = []
    for i, job in enumerate(SOURCES + ["scoring"]):
        t0 = started + timedelta(seconds=20 * i)
        if job == "news":  # an earlier success followed by a failure, to exercise the UI
            runs.append(RefreshRun(job=job, started_at=t0 - timedelta(hours=6),
                                   finished_at=t0 - timedelta(hours=6) + timedelta(seconds=9),
                                   status="ok", rows=64, message="6 feeds"))
            runs.append(RefreshRun(job=job, started_at=t0, finished_at=t0 + timedelta(seconds=30), status="error",
                                   rows=0, message="ConnectionError: demo failure - feed timed out"))
            continue
        message = {"macro_official": "demo: 2 series skipped (NZ PMI, CH retail sales unavailable)",
                   "scoring": "scores recomputed"}.get(job, "demo seed")
        runs.append(RefreshRun(job=job, started_at=t0, finished_at=t0 + timedelta(seconds=14), status="ok",
                               rows=rng.randint(40, 4000), message=message))
    session.add_all(runs)
    counts["refresh_runs"] = len(runs)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="allow non-SQLite / non-empty databases")
    parser.add_argument("--reset", action="store_true", help="delete all rows from every table first")
    args = parser.parse_args()

    from app.config import settings
    from app.db import init_db, session_scope

    url = settings.database_url
    if not url.startswith("sqlite") and not args.force:
        print("Refusing to seed demo data into a non-SQLite database without --force.\n"
              "Set DATABASE_URL=sqlite:///./demo.db for a throwaway demo database.")
        return 2
    init_db()
    with session_scope() as session:
        if args.reset:
            reset(session)
        elif session.execute(select(Score.region).limit(1)).first() and not args.force:
            print("Database already has scores. Use --reset to replace them or --force to add anyway.")
            return 2
        counts = seed(session)
    for table, rows in counts.items():
        print(f"{table:24s} {rows}")
    print("Demo data loaded (synthetic).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
