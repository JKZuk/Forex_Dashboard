# REST API

Local, read-mostly JSON API served by FastAPI under `/api` (same process serves the
built dashboard at `/`). Code: `backend/app/main.py`, `backend/app/api/`.
Interactive schema: `http://127.0.0.1:8000/api/docs` (Swagger UI; that page itself
loads its assets from a CDN, the dashboard does not).

## Running

```bash
./run.sh              # venv + frontend build if missing/stale, then http://127.0.0.1:8000
PORT=8010 ./run.sh    # another port
./run.sh dev          # API with --reload on :8000 + Vite dev server on :5173 (proxies /api)
./run.sh build        # only rebuild frontend/dist

# throwaway demo database (synthetic data, SQLite)
export DATABASE_URL=sqlite:///$PWD/backend/demo.db
(cd backend && .venv/bin/python scripts/seed_demo.py)     # --reset to replace, --force for non-SQLite
./run.sh

# tests (temp SQLite, no network)
cd backend && .venv/bin/python -m pytest tests/test_api.py -q
```

Set `REFRESH_INTERVAL_MINUTES` > 0 in `.env` to have the server run
`pipeline.refresh` on that interval (APScheduler); 0 = manual refresh only.

## Conventions

- Dates are `YYYY-MM-DD`. Datetimes are ISO 8601 UTC with an explicit `Z`
  (`2026-11-10T13:30:00Z`); the database stores naive UTC.
- Scores are -100..+100 (positive = bullish for that region's currency), coverage 0..1.
- Horizons: `short`, `medium`, `long`. Regions always come from `app.regions.REGIONS`.
- "No data" is never an error: lists are empty, objects are `null`, and
  `has_data` / `found` flags are `false`. A fresh database answers 200 everywhere.
- Errors: `{"detail": "..."}` with 400 (bad parameter), 404 (unknown region / path),
  422 (validation), 503 (database unavailable).
- `{code}` parameters accept a region code (`JP`) or its currency (`JPY`), case-insensitive.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness, DB check, `has_data`, `frontend_built` |
| GET | `/api/regions` | region registry |
| GET | `/api/overview` | all regions: latest 3 scores, 1w change, headline, FX price + changes |
| GET | `/api/regions/{code}` | drill-down: scores, drivers, conclusion, indicators, trade series, news, events |
| GET | `/api/history/scores` | score history for charts |
| GET | `/api/history/price` | FX price history for charts |
| GET | `/api/heatmap` | currency x horizon scores + ranked pair differentials |
| GET | `/api/global` | latest `GLOBAL.*` risk / commodity gauges |
| GET | `/api/cot` | COT positioning summary, all currencies |
| GET | `/api/cot/{currency}` | COT weekly history for one currency |
| GET | `/api/calendar` | upcoming events, filter by impact / region |
| GET | `/api/news` | news feed with per-region sentiment |
| GET | `/api/series/{series_id}` | any stored series (`JP.TRADE_BALANCE`, `GLOBAL.VIX`, `FX.EURUSD`) |
| POST | `/api/refresh` | start the refresh pipeline in the background |
| GET | `/api/refresh/status` | running flag + latest run per job |

### GET /api/health
```json
{"status": "ok", "database": "ok", "dialect": "mysql", "has_data": true,
 "server_time": "2026-11-10T19:04:51Z", "frontend_built": true}
```
`status` is `degraded` when the database check fails. `has_data` = at least one score row.

### GET /api/regions
```json
[{"code": "EZ", "name": "Eurozone", "currency": "EUR", "group": "major", "core": true,
  "trade_sensitive": false, "scored": true, "fx_series_id": "FX.EURUSD"}]
```
`group`: `major` | `commodity` | `influence` | `haven`. Registry order (core regions first).

### GET /api/overview
```json
{
  "has_data": true, "as_of": "2026-11-10", "horizons": ["short", "medium", "long"],
  "regions": [{
    "region": { "...": "as /api/regions" },
    "as_of": "2026-11-10",
    "scores": {
      "short": {"horizon": "short", "score": 46.6, "label": "Bullish", "coverage": 1.0,
                "as_of": "2026-11-10", "change_1w": 12.4, "previous_as_of": "2026-11-06"}
    },
    "headline": "USD bullish near term; ...",
    "price": {"series_id": "FX.USD_INDEX", "name": "USD index", "date": "2026-11-10",
              "value": 115.89, "change_1d_pct": 0.23, "change_1w_pct": 0.48, "change_1m_pct": 0.75}
  }]
}
```
- `scores` is keyed by horizon; a missing key means no score for that horizon.
- `no_data: true` marks the scoring engine's "No Data" result (label `No Data`, score 0,
  coverage 0). It is not a neutral reading: the UI shows a grey "No data", `change_1w` is
  null, `/api/heatmap` reports the score as `null` (and leaves the currency out of that
  horizon's pair ranking), and `/api/history/scores` omits such rows.
- `change_1w` = latest score minus the stored score nearest to 7 days earlier (accepted
  window 4-14 days back, so it works with weekly history and with daily rows); `null` if none.
- `price` is the region's FX series (`regions.fx_series_id`): USD value of one unit of the
  currency, or the dollar index for USD. Changes are % vs the previous observation, and vs
  the last observation at or before 7 / 30 days earlier. `null` when the series is absent.

### GET /api/regions/{code}
```json
{
  "region": {}, "has_data": true, "as_of": "2026-11-10",
  "horizons": [{
    "horizon": "short",
    "score": { "...": "as in overview, or null" },
    "components": [{"component": "yield_differential", "label": "2Y yield differential",
                    "raw_display": "+1.35 pp vs G10 average", "signal": 0.62, "weight": 0.2,
                    "contribution": 12.4, "detail": "...", "has_data": true}]
  }],
  "conclusion": {"as_of": "2026-11-10", "headline": "...", "text": "..."},
  "price": {},
  "indicators": [{"series_id": "JP.CPI_YOY", "indicator": "CPI_YOY", "name": "CPI inflation y/y",
                  "category": "inflation", "unit": "%", "frequency": "M", "source": "fred", "note": "",
                  "bullish_when": "higher", "impact": 3,
                  "latest_date": "2026-10-01", "latest_value": 2.54,
                  "previous_date": "2026-09-01", "previous_value": 2.49, "change": 0.05,
                  "spark": [{"date": "2024-11-01", "value": 2.9}]}],
  "trade": [{"series_id": "JP.TRADE_BALANCE", "found": true, "indicator": "TRADE_BALANCE",
             "name": "...", "unit": "JPY bn", "frequency": "M", "points": [{"date": "...", "value": -267.1}]}],
  "news": [], "events": []
}
```
- `horizons` always has all three panels. `components` are those of the latest date that has
  any, sorted by |contribution| descending with no-data drivers (`signal: null`,
  `has_data: false`) last.
- `indicators`: every series stored with `series.region == code` that has observations, in
  catalog order; `spark` = last 24 observations (oldest first). `bullish_when` / `impact`
  come from `catalog.INDICATORS` (null for non-catalog series).
- `trade`: `TRADE_BALANCE`, `EXPORTS_YOY`, `IMPORTS_YOY`, `CURRENT_ACCOUNT_GDP` series that
  exist, last ~6 years of points.
- `news`: 12 most recent items tagged for the region (`region_score` = score for this
  region). `events`: next 12 events for the region from today (UTC) to +45 days.

### GET /api/history/scores
Query: `region` (required), `horizons` (csv, default all), `start`, `end` (dates, inclusive).
```json
{"region": "JP", "start": null, "end": null,
 "series": {"short": [{"date": "2024-11-01", "score": -10.8, "coverage": 1.0}], "medium": [], "long": []}}
```

### GET /api/history/price
Query: `region` (required), `start`, `end`.
```json
{"region": "JP", "currency": "JPY", "series_id": "FX.JPYUSD", "name": "JPY/USD",
 "found": true, "points": [{"date": "2026-01-02", "value": 0.006545}]}
```

### GET /api/heatmap
```json
{
  "has_data": true, "as_of": "2026-11-10", "horizons": ["short", "medium", "long"],
  "currencies": [{"region": "US", "currency": "USD", "name": "United States", "group": "major",
                  "scored": true, "as_of": "2026-11-10",
                  "scores": {"short": 46.6, "medium": 25.3, "long": 0.2},
                  "labels": {"short": "Bullish", "medium": "Bullish", "long": "Neutral"}}],
  "pairs": {"short": [{"pair": "CHF/JPY", "direction": "long", "strong": "CHF", "weak": "JPY",
                       "differential": 165.0}]}
}
```
`pairs[horizon]` lists every unordered pair of scored currencies once, sorted by
`differential` (= strong score - weak score, >= 0) descending. `pair` uses the market's
conventional base/quote order (EUR, GBP, AUD, NZD, USD, CAD, CHF, CNY, JPY); `direction` is
`long` when the base currency is the stronger one, else `short`. These are score
differentials, not advice. The base-vs-quote matrix is `scores[base] - scores[quote]`.

### GET /api/global
```json
[{"series_id": "GLOBAL.VIX", "name": "CBOE VIX (higher = risk-off)", "unit": "", "date": "2026-11-10",
  "value": 17.2, "change_1d_pct": -3.9, "change_1w_pct": -7.6, "change_1m_pct": -23.7}]
```
Only gauges that have observations are returned (catalog order, then other `GLOBAL` series).

### GET /api/cot
```json
{"has_data": true, "report_date": "2026-11-10",
 "rows": [{"currency": "JPY", "region": "JP", "report_date": "2026-11-10",
           "noncomm_long": 12000.0, "noncomm_short": 178165.0, "net": -166165.0,
           "open_interest": 355800.0, "net_pct_oi": -46.7,
           "change_1w": -10642.0, "change_4w": -20488.0,
           "pct_oi_change_1w": -2.1, "pct_oi_change_4w": -5.3,
           "percentile_3y": 0.3, "percentile_weeks": 158, "extreme": "short",
           "spark": [{"date": "2025-11-11", "value": -12.3}]}]}
```
- `net` = non-commercial long - short (contracts). USD = dollar index contract.
- `change_1w` / `change_4w`: vs the report about 7 / 28 days earlier (null if that week is missing).
- `percentile_3y`: mid-rank percentile (0-100) of the current `net_pct_oi` within that
  currency's last 3 years of weekly reports; null with fewer than 26 reports.
- `extreme`: `"long"` if percentile >= 90, `"short"` if <= 10, else null.
- `spark`: `net_pct_oi` for the last 52 reports.

### GET /api/cot/{currency}
Query: `start`, `end`. Accepts a currency or region code. Unknown currency -> empty `points`.
```json
{"currency": "EUR", "points": [{"date": "2023-05-09", "noncomm_long": 120589.0, "noncomm_short": 199864.0,
                                "net": -79275.0, "open_interest": 712117.0, "net_pct_oi": -11.13}]}
```

### GET /api/calendar
Query: `days` (1-120, default 14, days ahead of now), `past_days` (0-60, default 0),
`impact` (csv of `high,medium,low`), `region` (csv of region codes), `limit` (default 500).
The window starts at 00:00 UTC today minus `past_days`, so today's events (including
date-only ones) are always included.
```json
{"start": "2026-11-10T00:00:00Z", "end": "2026-11-24T19:04:52Z", "total": 18,
 "events": [{"id": "1e07...", "region": "US", "title": "CPI inflation y/y",
             "event_time": "2026-11-10T13:30:00Z", "time_known": true, "impact": "high",
             "actual": "", "forecast": "3.0%", "previous": "3.1%", "source": "..."}]}
```
`time_known: false` means only the date part of `event_time` is meaningful (the UI shows
"All day" and does not shift it across timezones).

### GET /api/news
Query: `region` (region code or `GLOBAL`), `kind` (`news` | `central_bank`), `days`,
`limit` (1-200, default 50), `offset`.
```json
{"total": 70, "limit": 50, "offset": 0,
 "items": [{"id": "c159...", "source": "...", "kind": "news", "title": "...", "summary": "...",
            "url": "https://...", "published_at": "2026-11-10T18:20:11Z", "sentiment": 0.5,
            "engine": "lexicon", "region_score": 0.3,
            "regions": [{"region": "GLOBAL", "score": 0.52}, {"region": "AU", "score": 0.3}]}]}
```
Newest first. `regions[].score`: +1 = bullish for that region's currency (`GLOBAL` = risk
tone), sorted by |score|. `region_score` is set only when the `region` filter is used.

### GET /api/series/{series_id}
Query: `start`, `end`. An absent series returns `{"series_id": "...", "found": false, "points": []}`
with status 200 so the UI can show an empty state.

### POST /api/refresh
Body (optional): `{"sources": ["fx_prices", "cot"]}` - names from `app.ingest.SOURCES`;
omitted / empty = all sources. Scoring always runs afterwards. Returns **202**:
```json
{"started": true, "running": true, "sources": ["macro_fred", "..."], "message": "Refresh started."}
```
`started: false` when a refresh is already running. Unknown source -> 400.

### GET /api/refresh/status
```json
{"running": false, "server_time": "2026-11-10T19:04:52Z",
 "last_success_at": "2026-11-10T18:25:05Z", "last_finished_at": "2026-11-10T18:25:05Z",
 "sources": ["macro_fred", "macro_official", "fx_prices", "cot", "news", "calendar"],
 "auto_refresh_minutes": 0,
 "jobs": [{"job": "news", "status": "error", "started_at": "...Z", "finished_at": "...Z",
           "rows": 0, "message": "ConnectionError: ...", "last_success_at": "...Z"}]}
```
- `jobs`: the latest `refresh_runs` row per job, in pipeline order (`SOURCES`, then
  `scoring`, then any other job names found). `status`: `ok` | `error` | `running` |
  `never` (no run recorded) | `interrupted` (a `running` row older than 45 minutes with no
  refresh active in this process, e.g. the server was stopped mid-refresh).
- `running`: a refresh is active in this process, or a recent `running` row exists (e.g.
  `python -m app.cli refresh` in another terminal).
- `last_success_at`: most recent `finished_at` of any job run with status `ok`;
  per-job `last_success_at` likewise.

The dashboard POSTs `/api/refresh`, polls this endpoint every 1.5 s while `running`, and
reloads all view data when `running` returns to false.

## Frontend routes (hash based)

`#/` overview, `#/heatmap`, `#/region/{code}`, `#/cot` or `#/cot/{currency}`, `#/calendar`, `#/news`.
