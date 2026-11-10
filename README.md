# FX Sentiment Dashboard

A personal, local forex dashboard that scores Short-, Medium- and Long-term
sentiment (-100 to +100) for nine regions and their currencies, using only free
data, and explains every score driver by driver.

| Region | Currency | Role |
|---|---|---|
| United States, Eurozone, Japan, United Kingdom | USD, EUR, JPY, GBP | majors |
| Australia, Canada, New Zealand | AUD, CAD, NZD | commodity bloc |
| China | CNY | growth / risk influence (also feeds AUD, NZD, lightly JPY, EUR) |
| Switzerland | CHF | safe haven |

## Run it on a Mac

Requirements: Python 3.10+, Node 18+, and the local MySQL server.

1. Fill in `.env` (copy `.env.example` if it is missing):
   - `DB_USER` / `DB_PASSWORD` - a MySQL user allowed to create a schema.
   - `FRED_API_KEY` - optional, free from fred.stlouisfed.org. Without it FRED
     is read through its public CSV endpoint and exact US release dates beyond
     the current week are missing from the calendar.
2. Start the dashboard:
   ```bash
   ./run.sh
   ```
   The first run creates `backend/.venv`, installs requirements, builds the
   frontend, creates the `fx_dashboard` schema and tables, and serves
   http://127.0.0.1:8000.
3. Click **Refresh** in the header (about one minute the first time), or:
   ```bash
   cd backend && .venv/bin/python -m app.cli refresh
   ```
4. Build the score history used by the score-vs-price charts (a few seconds):
   ```bash
   cd backend && .venv/bin/python -m app.cli backfill 2023-01-01
   ```

Automatic refresh: set `REFRESH_INTERVAL_MINUTES` in `.env` (for example `240`)
and the server refreshes on that interval while it runs.

Other commands (`cd backend` first):

| Command | What it does |
|---|---|
| `.venv/bin/python -m app.cli refresh fx_prices cot` | refresh only the named sources, then rescore |
| `.venv/bin/python -m app.cli score` | recompute today's scores from stored data |
| `.venv/bin/python -m pytest -q tests` | run the test suite (SQLite, no network) |
| `../run.sh dev` | API with auto-reload plus Vite hot reload on :5173 |

To try it without MySQL, point it at a throwaway SQLite file:
`DATABASE_URL=sqlite:///$PWD/backend/dev.db ./run.sh`.

## Stack

- **Database**: your existing MySQL server. The app owns one schema,
  `fx_dashboard`, and touches nothing else. SQLAlchemy keeps the code portable
  (tests run on SQLite).
- **Backend**: Python, FastAPI, SQLAlchemy + PyMySQL, pandas. One process serves
  the API and the built frontend.
- **Frontend**: React + TypeScript + Vite, hand-written SVG charts, no runtime
  calls to anything but the local API.
- **Sentiment**: local financial lexicon with hawkish/dovish direction rules
  (default). FinBERT is optional: `pip install -r backend/requirements-finbert.txt`
  and set `SENTIMENT_ENGINE=finbert`.

## How the database is used

All tables live in the `fx_dashboard` schema and are defined in
`backend/app/models.py`. Every write is an idempotent upsert on a natural key,
so refreshes can be re-run safely.

| Table | Contents |
|---|---|
| `series`, `observations` | every time series: macro releases, yields, FX rates, risk gauges (`<REGION>.<INDICATOR>`, `FX.*`, `GLOBAL.*`) |
| `cot_positions` | weekly CFTC speculative positioning per currency |
| `news_items`, `news_region_sentiment` | headlines and their per-currency directional score |
| `calendar_events` | recent and upcoming releases and central-bank meetings |
| `scores`, `score_components`, `conclusions` | the three scores per region and date, their driver breakdown, the written conclusion |
| `refresh_runs` | log of every pipeline job (status, rows, message) |

## Layout

```
backend/app/
  config.py db.py models.py   settings, engine + upsert, schema
  regions.py catalog.py       region registry, indicator vocabulary
  ingest/                     one module per data source (see ingest/__init__.py)
  sentiment/                  local news sentiment engines + region tagging
  scoring/                    engine, one module per component family, conclusions
  api/  main.py               REST API, static frontend, scheduler
  pipeline.py cli.py          refresh orchestration, command line
backend/config/               scoring.yaml (all weights), feeds.yaml, calendar.yaml
frontend/src/                 views, components, API client
docs/                         methodology and source documentation
```

Extending it:
- **New data source (free or paid)**: add `backend/app/ingest/<name>.py` with
  `run(session) -> IngestResult` and list it in `ingest/__init__.py`.
- **New region**: add it to `regions.py`, then map its series in the ingest modules.
- **Tune scoring**: edit `backend/config/scoring.yaml`; it is re-read on every run.

## Documentation

- [docs/SCORING.md](docs/SCORING.md) - scoring methodology, every component and weight
- [docs/sources/macro.md](docs/sources/macro.md) - macro coverage matrix per region and indicator
- [docs/sources/market.md](docs/sources/market.md) - FX prices, USD index, CFTC COT
- [docs/sources/news_calendar.md](docs/sources/news_calendar.md) - feeds, sentiment approach, calendar sources
- [docs/API.md](docs/API.md) - REST endpoints

## Known limitations

- **No consensus forecasts.** Free sources do not carry them, so a "data
  surprise" is mostly the latest release against the series' own trend;
  actual-vs-forecast is used only for the current week's calendar events.
- **Proxies.** PMI is the OECD business confidence indicator (neutral 100) for
  every region. The 2-year yield is a short-rate proxy for UK, NZ, CN and CH.
- **Gaps.** No gold price; China lacks core CPI, unemployment, retail sales and
  industrial production; monthly employment change exists only for US, JP, AU, CA.
- **Central-bank text barely moves the news score.** Central-bank feeds are
  ingested, but their headlines ("Speech by Governor ...") rarely carry a
  direction the lexicon can read; most of the news signal comes from news wires.
- **Prices are one ECB reference fix per business day**, not intraday.
- **Calendar depth.** Forecast and previous values exist for the current week
  only; two RBNZ meeting dates are marked unverified.
- **Backfilled history is approximate.** It applies publication lags per
  frequency and ignores data revisions; news and calendar inputs do not exist
  for past dates.

This is an informational model built on free, delayed data. It is not
investment advice.
