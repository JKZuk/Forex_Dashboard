"""News + central-bank text ingestion with local sentiment.

Reads the RSS/Atom feeds listed in backend/config/feeds.yaml, keeps only the
headline, the feed's own short summary and the link (no article scraping),
tags regions, scores currency direction locally (app.sentiment) and upserts
NewsItem + NewsRegionSentiment.

Feed content is untrusted data: it is only parsed as text, HTML is stripped,
nothing in it is executed or followed.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import CONFIG_DIR
from app.db import upsert
from app.ingest import IngestResult
from app.models import NewsItem, NewsRegionSentiment
from app.regions import REGION_CODES
from app.sentiment import GLOBAL_REGION, SentimentEngine, TextItem, get_engine, is_macro_relevant

log = logging.getLogger("ingest.news")

FEEDS_FILE = CONFIG_DIR / "feeds.yaml"
DEFAULTS: dict[str, Any] = {
    "max_age_days": 60,
    "max_items_per_feed": 60,
    "summary_max_chars": 500,
    "timeout_seconds": 20,
    "user_agent": "fx-dashboard/1.0 (personal local dashboard; RSS reader)",
}
_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
_TAG_RE = re.compile(r"(?s)<(script|style)\b.*?</\1\s*>|<!--.*?-->|<[^>]*>")
_WS_RE = re.compile(r"\s+")
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+-\s+")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "at_medium", "at_campaign")


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    kind: str = "news"
    default_region: str | None = None
    title_only: bool = False
    max_items: int | None = None


@dataclass
class RawItem:
    feed: Feed
    title: str
    summary: str
    url: str
    published: datetime | None  # UTC naive; None when the feed has no date


# ------------------------------------------------------------------ pure helpers
def load_feeds(path: Path = FEEDS_FILE) -> tuple[list[Feed], dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = {**DEFAULTS, **(data.get("settings") or {})}
    feeds: list[Feed] = []
    for row in data.get("feeds") or []:
        if not isinstance(row, dict) or not row.get("url") or row.get("enabled") is False:
            continue
        region = row.get("default_region")
        if region and region not in REGION_CODES:
            log.warning("feed %s: unknown default_region %r ignored", row.get("name"), region)
            region = None
        kind = "central_bank" if row.get("kind") == "central_bank" else "news"
        feeds.append(
            Feed(
                name=str(row.get("name") or urlsplit(row["url"]).netloc)[:64],
                url=str(row["url"]),
                kind=kind,
                default_region=region,
                title_only=bool(row.get("title_only", False)),
                max_items=int(row["max_items"]) if row.get("max_items") else None,
            )
        )
    return feeds, cfg


def clean_text(raw: str | None, max_chars: int | None = None) -> str:
    """Strip HTML/entities/control characters and collapse whitespace; optional word-safe cap."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", str(raw))
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)  # entities may have hidden a second layer of markup
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = _WS_RE.sub(" ", text).strip()
    if max_chars and len(text) > max_chars:
        cut = text[: max_chars - 1]
        if " " in cut[max_chars // 2 :]:
            cut = cut[: cut.rindex(" ")]
        text = cut.rstrip(" ,;:-") + "…"
    return text


def canonical_url(url: str) -> str:
    """Drop fragments and tracking parameters so the same story dedupes across feeds."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _entry_time(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6])  # feedparser normalises to UTC
            except (TypeError, ValueError):
                continue
    return None


def parse_feed(content: bytes | str, feed: Feed, summary_max_chars: int = 500, max_items: int = 60) -> list[RawItem]:
    """Parse feed bytes into RawItems (no network, used directly by the tests)."""
    parsed = feedparser.parse(content)
    items: list[RawItem] = []
    for entry in parsed.entries[: feed.max_items or max_items]:
        url = canonical_url(str(entry.get("link") or ""))
        title = clean_text(entry.get("title"))
        if not url or not title or len(url) > 1024:
            continue
        publisher = clean_text((entry.get("source") or {}).get("title")) if isinstance(entry.get("source"), dict) else ""
        if publisher and title.endswith(f" - {publisher}"):  # Google News: "Headline - Publisher"
            title = title[: -len(publisher) - 3].rstrip()
        title = _DATE_PREFIX_RE.sub("", title)  # SNB: "2026-11-10 - Headline"
        summary = "" if feed.title_only else clean_text(entry.get("summary"), summary_max_chars)
        if summary and (summary == title or title.startswith(summary.rstrip("…"))):
            summary = ""
        items.append(RawItem(feed, title[:512], summary, url, _entry_time(entry)))
    return items


def build_rows(
    items: list[RawItem],
    engine: SentimentEngine,
    now: datetime,
    max_age_days: int = 60,
    known_dates: dict[str, datetime] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Score items and build (news_items rows, news_region_sentiment rows). Pure, no I/O."""
    known_dates = known_dates or {}
    cutoff = now - timedelta(days=max_age_days)
    seen: set[str] = set()
    kept: list[tuple[RawItem, str, datetime]] = []
    for item in items:
        key = url_hash(item.url)
        if key in seen:
            continue
        published = item.published or known_dates.get(key) or now  # dateless feed: first-seen time
        if published > now:
            published = now
        if published < cutoff:
            continue
        seen.add(key)
        kept.append((item, key, published))

    scored = engine.score_many(
        [
            TextItem(
                title=i.title,
                summary=i.summary,
                default_region=i.feed.default_region,
                force_default=i.feed.kind == "central_bank",
            )
            for i, _, _ in kept
        ]
    )
    news_rows: list[dict] = []
    region_rows: list[dict] = []
    for (item, key, published), result in zip(kept, scored):
        regions = {r: s for r, s in result.regions.items() if r in REGION_CODES}
        if item.feed.kind != "central_bank":
            if not regions and result.risk is None:
                continue  # not about any of our currencies
            if not result.has_signal and not is_macro_relevant(item.title, item.summary):
                continue  # neutral and off-topic (company / general news that merely names a country)
        news_rows.append(
            {
                "url_hash": key,
                "source": item.feed.name,
                "kind": item.feed.kind,
                "title": item.title,
                "summary": item.summary,
                "url": item.url,
                "published_at": published,
                "sentiment": max(-1.0, min(1.0, float(result.tone))),
                "engine": engine.name,
            }
        )
        for region, score in regions.items():
            region_rows.append({"url_hash": key, "region": region, "score": float(score), "published_at": published})
        if result.risk is not None:
            region_rows.append(
                {"url_hash": key, "region": GLOBAL_REGION, "score": float(result.risk), "published_at": published}
            )
    return news_rows, region_rows


# ------------------------------------------------------------------ network + DB
def _fetch(feed: Feed, cfg: dict[str, Any]) -> tuple[Feed, list[RawItem], str]:
    try:
        resp = requests.get(
            feed.url,
            headers={"User-Agent": cfg["user_agent"], "Accept": _ACCEPT},
            timeout=cfg["timeout_seconds"],
        )
        resp.raise_for_status()
        items = parse_feed(resp.content, feed, cfg["summary_max_chars"], cfg["max_items_per_feed"])
        return feed, items, "" if items else "no entries"
    except Exception as exc:  # noqa: BLE001 - one bad feed must not fail the run
        return feed, [], f"{type(exc).__name__}: {str(exc)[:80]}"


def run(session: Session) -> IngestResult:
    feeds, cfg = load_feeds()
    if not feeds:
        return IngestResult(0, f"no feeds configured in {FEEDS_FILE}")
    engine = get_engine()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda f: _fetch(f, cfg), feeds))
    failures = [f"{feed.name} ({err})" for feed, _, err in results if err]
    items = [item for _, feed_items, _ in results for item in feed_items]
    if not items:
        raise RuntimeError("all news feeds failed: " + "; ".join(failures)[:1500])

    now = datetime.utcnow()
    hashes = list({url_hash(i.url) for i in items})
    known: dict[str, datetime] = {}
    for i in range(0, len(hashes), 500):
        rows = session.execute(
            select(NewsItem.url_hash, NewsItem.published_at).where(NewsItem.url_hash.in_(hashes[i : i + 500]))
        )
        known.update({h: d for h, d in rows})

    news_rows, region_rows = build_rows(items, engine, now, int(cfg["max_age_days"]), known)
    kept_hashes = [r["url_hash"] for r in news_rows]
    for i in range(0, len(kept_hashes), 500):  # region set may change when the lexicon is tuned
        session.execute(delete(NewsRegionSentiment).where(NewsRegionSentiment.url_hash.in_(kept_hashes[i : i + 500])))
    upsert(session, NewsItem, news_rows)
    upsert(session, NewsRegionSentiment, region_rows)

    ok = len(feeds) - len(failures)
    new = sum(1 for h in kept_hashes if h not in known)
    msg = (
        f"{len(news_rows)} relevant items ({new} new) of {len(items)} fetched from {ok}/{len(feeds)} feeds; "
        f"{len(region_rows)} region scores; engine={engine.name}"
    )
    if failures:
        msg += "; failed: " + "; ".join(failures)
    return IngestResult(rows=len(news_rows), message=msg)
