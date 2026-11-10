"""Data access for scoring.

`DataStore.load()` reads every table the scoring engine needs ONCE (via pandas)
and keeps each series as a pair of numpy arrays. `Context` is a point-in-time
view of the store for one `as_of` date: components only ever see data through
it, so look-ahead protection and staleness rules live in exactly one place.

To plug in a new (e.g. paid) data source nothing here changes: ingestion writes
`Observation` rows under a series id and components read them by that id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog import INDICATOR_BY_KEY
from ..models import CalendarEvent, CotPosition, NewsItem, NewsRegionSentiment, Observation, SeriesMeta
from ..regions import FX_USD_INDEX, REGIONS, Region, fx_series_id, series_id

_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def _ordinals(values: Any) -> np.ndarray:
    """Dates -> proleptic ordinals (int64)."""
    days = pd.to_datetime(pd.Series(values)).values.astype("datetime64[D]").astype(np.int64)
    return days + _EPOCH_ORDINAL


def _ordinal_float(dt: datetime) -> float:
    return dt.toordinal() + (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0


# ------------------------------------------------------------------- series --
@dataclass(frozen=True)
class TS:
    """A time series: observation dates (ordinals, ascending) and values."""

    sid: str
    d: np.ndarray
    v: np.ndarray
    freq: str = "D"
    lag: int = 0  # publication lag in days (for release-date estimates)

    def __len__(self) -> int:
        return len(self.d)

    @property
    def last(self) -> float:
        return float(self.v[-1])

    @property
    def last_date(self) -> int:
        return int(self.d[-1])

    def head(self, n: int) -> "TS":
        return TS(self.sid, self.d[:n], self.v[:n], self.freq, self.lag)

    def at(self, ordinal: int, tolerance: int | None = None) -> float | None:
        """Value of the last observation on or before `ordinal`."""
        i = int(np.searchsorted(self.d, ordinal, side="right")) - 1
        if i < 0:
            return None
        if tolerance is not None and ordinal - int(self.d[i]) > tolerance:
            return None
        return float(self.v[i])

    def ago(self, days: int) -> float | None:
        """Value `days` calendar days before the latest observation."""
        return self.at(self.last_date - days, tolerance=max(100, days))

    def chg(self, days: int) -> float | None:
        past = self.ago(days)
        return None if past is None else self.last - past

    def since(self, days: int) -> np.ndarray:
        """Values observed in the last `days` days (relative to the latest obs)."""
        i = int(np.searchsorted(self.d, self.last_date - days, side="right"))
        return self.v[i:]

    def between(self, lo: int, hi: int) -> np.ndarray:
        """Values with lo < obs_date <= hi."""
        i = int(np.searchsorted(self.d, lo, side="right"))
        j = int(np.searchsorted(self.d, hi, side="right"))
        return self.v[i:j]


@dataclass(frozen=True)
class NewsArr:
    t: np.ndarray  # published time, fractional ordinal days (ascending)
    score: np.ndarray
    central_bank: np.ndarray  # bool


@dataclass(frozen=True)
class CalSurprise:
    t: float  # event time, fractional ordinal days
    title: str
    impact: str
    actual: float
    forecast: float
    previous: float | None


_NUM_RE = re.compile(r"^\s*[<>~]?\s*([-+]?\d[\d,]*\.?\d*|[-+]?\.\d+)\s*(%|[kKmMbBtT])?")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_number(text: str | None) -> float | None:
    """Parse calendar strings like '250K', '3.2%', '-0.1', '1,234' -> float."""
    if not text:
        return None
    m = _NUM_RE.match(str(text).replace("−", "-"))
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    return value * _SUFFIX.get(suffix, 1.0)


def _infer_freq(d: np.ndarray) -> str:
    if len(d) < 3:
        return "M"
    gap = float(np.median(np.diff(d[-24:])))
    if gap <= 3:
        return "D"
    if gap <= 10:
        return "W"
    if gap <= 45:
        return "M"
    if gap <= 135:
        return "Q"
    return "A"


# -------------------------------------------------------------------- store --
@dataclass
class DataStore:
    series: dict[str, TS] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    cot: dict[str, TS] = field(default_factory=dict)  # currency -> net % of OI
    news: dict[str, NewsArr] = field(default_factory=dict)  # region -> items
    calendar: dict[str, list[CalSurprise]] = field(default_factory=dict)
    fx_rel: dict[str, TS] = field(default_factory=dict)  # currency -> log index vs basket
    _surprise_cache: dict[tuple, np.ndarray] = field(default_factory=dict)

    # ---------------------------------------------------------------- load --
    @classmethod
    def load(cls, session: Session, cfg: dict[str, Any]) -> "DataStore":
        store = cls()
        general = cfg.get("general", {})
        lag_default: dict[str, int] = general.get("publication_lag_days", {})
        lag_by_ind: dict[str, dict[str, int]] = general.get("publication_lag_by_indicator", {}) or {}

        meta = {
            row.series_id: (row.frequency or "", row.note or "")
            for row in session.execute(select(SeriesMeta.series_id, SeriesMeta.frequency, SeriesMeta.note))
        }
        rows = session.execute(
            select(Observation.series_id, Observation.obs_date, Observation.value)
            .order_by(Observation.series_id, Observation.obs_date)
        ).all()
        if rows:
            df = pd.DataFrame(rows, columns=["sid", "d", "v"])
            df["o"] = _ordinals(df["d"])
            df = df[np.isfinite(df["v"].astype(float))]
            for sid, grp in df.groupby("sid", sort=False):
                d = grp["o"].to_numpy(dtype=np.int64)
                v = grp["v"].to_numpy(dtype=float)
                freq = (meta.get(sid, ("", ""))[0] or "").upper()[:1]
                if freq not in ("D", "W", "M", "Q", "A"):
                    freq = _infer_freq(d)
                indicator = sid.split(".", 1)[1] if "." in sid else sid
                lag = int(lag_by_ind.get(indicator, {}).get(freq, lag_default.get(freq, 0)))
                store.series[sid] = TS(sid, d, v, freq, lag)
                store.notes[sid] = meta.get(sid, ("", ""))[1]

        cot_lag = int(general.get("cot_lag_days", 3))
        cot_rows = session.execute(
            select(CotPosition.currency, CotPosition.report_date, CotPosition.net_pct_oi)
            .order_by(CotPosition.currency, CotPosition.report_date)
        ).all()
        if cot_rows:
            cdf = pd.DataFrame(cot_rows, columns=["ccy", "d", "v"])
            cdf["o"] = _ordinals(cdf["d"])
            for ccy, grp in cdf.groupby("ccy", sort=False):
                store.cot[ccy] = TS(f"COT.{ccy}", grp["o"].to_numpy(dtype=np.int64),
                                    grp["v"].to_numpy(dtype=float), "W", cot_lag)

        news_rows = session.execute(
            select(NewsRegionSentiment.region, NewsRegionSentiment.published_at,
                   NewsRegionSentiment.score, NewsItem.kind)
            .join(NewsItem, NewsItem.url_hash == NewsRegionSentiment.url_hash, isouter=True)
            # Ingestion stores a neutral (0) row for every tagged region; routine
            # notices would dilute the tone, so only directional items count.
            .where(NewsRegionSentiment.score != 0)
            .order_by(NewsRegionSentiment.region, NewsRegionSentiment.published_at)
        ).all()
        by_region: dict[str, list[tuple[float, float, bool]]] = {}
        for region, published, score, kind in news_rows:
            if published is None or score is None:
                continue
            by_region.setdefault(region, []).append(
                (_ordinal_float(published), float(score), (kind or "news") == "central_bank")
            )
        for region, items in by_region.items():
            store.news[region] = NewsArr(
                np.array([i[0] for i in items], dtype=float),
                np.array([i[1] for i in items], dtype=float),
                np.array([i[2] for i in items], dtype=bool),
            )

        cal_rows = session.execute(
            select(CalendarEvent.region, CalendarEvent.event_time, CalendarEvent.title,
                   CalendarEvent.impact, CalendarEvent.actual, CalendarEvent.forecast,
                   CalendarEvent.previous)
            .where(CalendarEvent.actual != "", CalendarEvent.forecast != "")
            .order_by(CalendarEvent.event_time)
        ).all()
        for region, when, title, impact, actual, forecast, previous in cal_rows:
            a, f = parse_number(actual), parse_number(forecast)
            if a is None or f is None or when is None:
                continue
            store.calendar.setdefault(region, []).append(
                CalSurprise(_ordinal_float(when), title or "", (impact or "medium").lower(),
                            a, f, parse_number(previous))
            )

        store._build_fx_basket(int(general.get("fx_basket_min_members", 3)))
        return store

    # ----------------------------------------------------------- fx basket --
    def _build_fx_basket(self, min_members: int) -> None:
        """Log index of each currency against an equal-weight basket of the others.

        Built from daily log returns: rel_c = r_c - mean(r_j, j != c), with USD's
        own return in USD terms = 0. Cumulated, so the value on day t depends only
        on data up to t (point-in-time safe).
        """
        cols: dict[str, pd.Series] = {}
        for region in REGIONS:
            ccy = region.currency
            if ccy == "USD":
                continue
            ts = self.series.get(fx_series_id(ccy))
            if ts is None or len(ts) < 30 or np.any(ts.v <= 0):
                continue
            cols[ccy] = pd.Series(np.log(ts.v), index=ts.d)
        if len(cols) >= max(1, min_members):
            frame = pd.DataFrame(cols).sort_index().ffill(limit=5)
            rets = frame.diff()
            rets.insert(0, "USD", 0.0)
            rets.loc[rets.drop(columns="USD").isna().all(axis=1), "USD"] = np.nan
            total = rets.sum(axis=1, skipna=True)
            count = rets.notna().sum(axis=1)
            for ccy in rets.columns:
                own = rets[ccy]
                others = count - own.notna().astype(int)
                rel = own - (total - own.fillna(0.0)) / others.where(others >= min_members)
                rel = rel.dropna()
                if len(rel) < 30:
                    continue
                self.fx_rel[ccy] = TS(f"FXREL.{ccy}", rel.index.to_numpy(dtype=np.int64),
                                      rel.cumsum().to_numpy(dtype=float), "D", 0)
        # Fallbacks when a basket cannot be built: the USD-basis series itself.
        for region in REGIONS:
            ccy = region.currency
            if ccy in self.fx_rel:
                continue
            ts = self.series.get(FX_USD_INDEX if ccy == "USD" else fx_series_id(ccy))
            if ts is not None and len(ts) >= 30 and not np.any(ts.v <= 0):
                self.fx_rel[ccy] = TS(ts.sid, ts.d, np.log(ts.v), "D", 0)

    # ------------------------------------------------------------ surprises --
    def surprise_z(self, sid: str, mode: str, k: int, vol_obs: int, min_hist: int, z_clip: float,
                   sd_floor: float = 0.0) -> np.ndarray | None:
        """Standardised surprise for every observation of a series (NaN = n/a).

        Causal: element i uses observations <= i only, so slicing the result
        to a point-in-time prefix is safe. Cached per series.
        """
        key = (sid, mode, k, vol_obs, min_hist, z_clip, sd_floor)
        if key in self._surprise_cache:
            return self._surprise_cache[key]
        ts = self.series.get(sid)
        if ts is None:
            return None
        s = pd.Series(ts.v)
        x = s if mode == "level" else s.diff()
        surprise = x if mode == "raw_change" else x - x.shift(1).rolling(k, min_periods=k).mean()
        sd = x.shift(1).rolling(vol_obs, min_periods=min_hist).std().clip(lower=sd_floor)
        z = (surprise / sd.where(sd > 1e-12)).clip(-z_clip, z_clip).to_numpy(dtype=float)
        self._surprise_cache[key] = z
        return z


# ------------------------------------------------------------------ context --
@dataclass(frozen=True)
class Relative:
    own: float
    peer_mean: float
    diff: float
    n_peers: int


class Context:
    """Point-in-time view of the store for one as_of date."""

    def __init__(self, store: DataStore, cfg: dict[str, Any], as_of: date, today: date | None = None):
        self.store = store
        self.cfg = cfg
        self.general: dict[str, Any] = cfg.get("general", {})
        self.as_of = as_of
        self.t = as_of.toordinal()
        today = today or date.today()
        # Live run: everything in the DB is known. Historical run: apply publication lags.
        self.live = as_of >= today
        self.now = self.t + 1.0  # end of the as_of day (fractional ordinal)
        if self.live and as_of == today:
            self.now = min(self.now, _ordinal_float(datetime.utcnow()) + 1e-6)
            self.now = max(self.now, float(self.t))
        self._ts_cache: dict[str, TS | None] = {}
        self._memo: dict[Any, Any] = {}
        self._stale: dict[str, int] = self.general.get("max_stale_days", {})
        exclude = set(self.general.get("peer_exclude", []) or [])
        self.scored: list[Region] = [r for r in REGIONS if r.scored]
        self._peer_pool: list[Region] = [r for r in self.scored if r.code not in exclude]
        self.min_peers = int(self.general.get("min_peers", 2))

    # ------------------------------------------------------------- series --
    def _known(self, ts: TS) -> TS | None:
        cutoff = self.t if self.live else self.t - ts.lag
        n = int(np.searchsorted(ts.d, cutoff, side="right"))
        if n == 0:
            return None
        if self.t - int(ts.d[n - 1]) > int(self._stale.get(ts.freq, 10 ** 6)):
            return None  # stale: treat as missing rather than carry forward
        return ts if n == len(ts) else ts.head(n)

    def ts(self, sid: str) -> TS | None:
        """Series as known on as_of (None when absent, not yet published, or stale)."""
        if sid not in self._ts_cache:
            full = self.store.series.get(sid)
            self._ts_cache[sid] = None if full is None else self._known(full)
        return self._ts_cache[sid]

    def region_ts(self, region: str, indicator: str) -> TS | None:
        return self.ts(series_id(region, indicator))

    def latest(self, region: str, indicator: str) -> float | None:
        ts = self.region_ts(region, indicator)
        return None if ts is None else ts.last

    def fx(self, currency: str) -> TS | None:
        key = f"__fx__{currency}"
        if key not in self._ts_cache:
            full = self.store.fx_rel.get(currency)
            self._ts_cache[key] = None if full is None else self._known(full)
        return self._ts_cache[key]

    def fx_basis(self, currency: str) -> str:
        full = self.store.fx_rel.get(currency)
        return "vs basket" if full is not None and full.sid.startswith("FXREL.") else "vs USD" if currency != "USD" else "USD index"

    def cot(self, currency: str) -> TS | None:
        key = f"__cot__{currency}"
        if key not in self._ts_cache:
            full = self.store.cot.get(currency)
            self._ts_cache[key] = None if full is None else self._known(full)
        return self._ts_cache[key]

    def release_age(self, ts: TS) -> np.ndarray:
        """Estimated days since each observation was published (>= 0)."""
        released = ts.d + ts.lag
        if self.live:
            released = np.minimum(released, self.t)
        return np.maximum(0, self.t - released)

    # --------------------------------------------------------------- news --
    def news(self, region: str, lookback_days: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(age_days, score, is_central_bank) of items in the lookback window."""
        arr = self.store.news.get(region)
        if arr is None:
            return None
        i = int(np.searchsorted(arr.t, self.now - lookback_days, side="left"))
        j = int(np.searchsorted(arr.t, self.now, side="right"))
        if j <= i:
            return None
        return self.now - arr.t[i:j], arr.score[i:j], arr.central_bank[i:j]

    def calendar(self, region: str, window_days: float) -> list[tuple[float, CalSurprise]]:
        """(age_days, event) for events with actual+forecast inside the window."""
        out = []
        for ev in self.store.calendar.get(region, []):
            if self.now - window_days <= ev.t <= self.now:
                out.append((self.now - ev.t, ev))
        return out

    # ----------------------------------------------------------- relative --
    def memo(self, key: Any, fn: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def relative(self, name: str, region: Region, fn: Callable[[Region], float | None]) -> Relative | None:
        """Region's reading minus the average of its peers' readings.

        `fn(region)` returns the raw reading or None. `name` keys the per-date
        cache so each region's reading is computed once.
        """
        values: dict[str, float | None] = self.memo(("xs", name), lambda: {r.code: fn(r) for r in self.scored})
        own = values.get(region.code)
        if own is None:
            return None
        peers = [values[r.code] for r in self._peer_pool if r.code != region.code and values.get(r.code) is not None]
        if len(peers) < self.min_peers:
            return None
        mean = float(np.mean(peers))
        return Relative(float(own), mean, float(own) - mean, len(peers))

    # ---------------------------------------------------------- inflation --
    def inflation(self, region: str) -> tuple[float, str] | None:
        for key in self.general.get("inflation_preference", ["CORE_CPI_YOY", "CPI_YOY"]):
            value = self.latest(region, key)
            if value is not None:
                return value, INDICATOR_BY_KEY[key].name if key in INDICATOR_BY_KEY else key
        return None
