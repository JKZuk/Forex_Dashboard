# News, central-bank text and economic calendar

Owner modules: `backend/app/ingest/news.py`, `backend/app/ingest/calendar.py`,
`backend/app/sentiment/`. Config: `backend/config/feeds.yaml`,
`backend/config/calendar.yaml`. Tests: `backend/tests/test_news_calendar.py`
(offline). Everything is free and keyless; sentiment runs locally.

All statements below were checked on 2026-11-10.

## 1. News (`news.py`)

Reads the RSS/Atom feeds in `config/feeds.yaml` (8 parallel requests, 20 s
timeout, generic User-Agent). Stores only the headline, the feed's own short
summary (HTML stripped, capped at 500 chars) and the link. No article scraping.
A failing feed is listed in `refresh_runs.message` and never fails the run.

| Step | Rule |
|---|---|
| Dedupe | `url_hash = sha1(canonical url)`; tracking params (`utm_*` etc.) and fragments removed |
| Age | items older than 60 days are skipped; future-dated items are clamped to "now" |
| No date in feed | first-seen time is used and kept on later runs (Nikkei Asia) |
| Relevance | `central_bank` items are always kept. `news` items need a region or a risk signal, and neutral ones must also contain macro/FX vocabulary (drops "Toyota to sell minitrucks in US") |
| Re-runs | idempotent; region rows of re-seen items are rewritten, so lexicon tuning takes effect |

### Feeds kept (47, all returned fresh entries)

Central banks (`kind=central_bank`, every item tagged with the bank's region):
Federal Reserve press releases + speeches, ECB press/speeches + blog, Bank of
England news + speeches, Bank of Japan what's-new, RBA media releases +
speeches, Bank of Canada press releases + speeches, RBNZ news, SNB news + press
releases, BIS central bankers' speeches (all banks; the only route to PBoC
speeches in English).

News (`kind=news`): investingLive (ex-ForexLive) news / central banks / forex,
Investing.com forex / economic indicators / economy, Nasdaq-RTTNews currencies,
ActionForex, CNBC economy / top / finance, MarketWatch top stories, WSJ markets
/ economy, FT markets, BBC business / economy, Guardian economics, Nikkei Asia,
Japan Times, ABC Australia business, RNZ business, CBC business, SCMP China
economy, and eight Google News RSS queries (EUR, JPY, GBP, AUD, CAD, NZD, CNY,
CHF; title only, 30 items each) as a robust fallback for thin regions.

### Feeds rejected

| Feed | Reason |
|---|---|
| FXStreet `/rss/news`, `/rss/analysis` | HTTP 503 |
| DailyFX market-news | HTTP 403 (site retired) |
| Yahoo Finance `rssindex`, `rss/topstories` | HTTP 404 |
| Reuters agency feed | HTTP 404 (Reuters has no public RSS) |
| MarketWatch MarketPulse | answers, but newest entry is ~16 months old |
| FXEmpire news | answers, but newest entry is ~3 months old |
| swissinfo business | HTTP 410 |
| PBoC | no English RSS; the English news page is plain HTML |
| RBNZ `/rss/news` | HTTP 403 (the working path is `/feeds/news`) |
| Bank of Canada site-wide feed | lists future events, not news |
| forexlive.com | same content as investinglive.com (kept the latter) |
| Seeking Alpha forex | opinion titles without summaries, low signal |
| Fed `press_monetary.xml`, BoE publications, SNB speeches | subsets of feeds already kept |

## 2. Sentiment (`app/sentiment/`)

| File | Role |
|---|---|
| `base.py` | `SentimentEngine` protocol, `TextItem`, `ScoredText` |
| `tagging.py` | `REGION_KEYWORDS` (data-driven keyword map) and `tag_regions` |
| `lexicon.py` | all vocabulary and weights (plain data, tune here) |
| `directional.py` | the rule engine that applies the lexicon clause by clause |
| `engines.py` | `LexiconEngine` (default) and `FinBertEngine` (optional) |
| `__init__.py` | `get_engine()` chosen by `SENTIMENT_ENGINE` |

What is stored:

- `NewsItem.sentiment` = generic tone, -1..+1 (not currency direction).
- `NewsRegionSentiment.score` = -1..+1, **+1 = bullish for that region's
  currency**. One row per region the item is about, including neutral rows
  (score 0).
- Region `GLOBAL` = risk tone, +1 risk-on, -1 risk-off. Only written when a
  risk rule fired.
- `NewsItem.kind` (`central_bank` / `news`) is exposed for weighting; no weight
  is baked into the score.

### Region tagging

Keyword/entity map per region: central bank names, officials, country,
currency codes and nicknames, currency pairs (`EUR/USD`, `Dollar-Yen`). Short
capitalised codes (`US`, `Fed`, `BoE`) are case-sensitive. The longest match
wins, so "Australian dollar" tags AU and not US; a mask list removes "Hong
Kong dollar", "multi-million-dollar" and similar. The title decides; the
summary is consulted only when the title names no region. A news feed's
`default_region` is used only when the text names no region and a directional
rule fired.

### Currency-directional rules

Applied per clause (split on punctuation and on `as / after / while / but /
amid ...`), then summed per region and squashed with `tanh`. Headline hits
count 1.5x; summary hits 0.6x and are capped.

1. **Policy language**: hawkish (+) and dovish (-) regex patterns (raise/cut
   rates, "25bp hike", tightening/easing, hawkish/dovish, QE/QT, "inflation too
   high", FX intervention, rating changes). Negators and faders flip a hit:
   "no rush to cut", "rules out a hike", "pare bets on cuts", "ends negative
   rates". Double negatives keep the sign ("refuses to rule out hikes").
2. **Subject x direction**: an economic subject near an up/down word. Each
   subject has a polarity: +1 when higher is bullish (inflation, GDP, jobs,
   retail sales, PMI, exports, yields), **-1 for inverted subjects**
   (unemployment, jobless claims, layoffs, deficit, borrowing). Surprise
   phrases override the verb: "less than expected", "misses forecasts", and
   numeric forms like `+0.3% vs +0.2% expected`.
3. **Currency price action** (lower weight): "yen slides", "EUR/USD jumps"
   (base up, quote down), "pound falls against the dollar".
4. **Risk tone** to `GLOBAL`: risk-off/on phrases and equity indices moving.
   Geopolitical words only count when the text also mentions markets.

`ScoredText.hits` lists every rule that fired, so any score can be explained.

### Engines

- `lexicon` (default): pure Python, no downloads, about 1 ms per item.
- `finbert`: `ProsusAI/finbert` through `transformers`, lazily imported and run
  locally. It supplies only the generic tone; direction still comes from the
  rules above, because FinBERT reads "Fed cuts rates" as positive. If selected
  but not installed, a warning is logged and the lexicon engine is used.
  Install with `backend/.venv/bin/pip install -r backend/requirements-finbert.txt`.
  **Not installed or run here, so the FinBERT path is untested.**

### Observed quality (real feeds, 2026-11-10)

About 740 items stored per run, about 300 with a non-zero directional or risk
score. From eyeballing roughly 150 scored headlines: about 80-85% of non-zero
scores point the right way. FX/central-bank wires are the most reliable.
Known weak spots:

- Company or general news that happens to use macro words ("growth model
  falters" for a bubble-tea chain scored CN negative).
- Hedged or conditional statements ("not necessarily a mistake if the Fed
  hikes" scored negative).
- Literal phrases ("sells notes at a high yield of 5.02%" scored US positive).
- Commodity links are not modelled (oil for CAD, iron ore for AUD).
- English only. No sarcasm or quote attribution.
- Magnitudes are rough; treat the score as direction plus rough confidence.

## 3. Calendar (`calendar.py`)

Window: last 14 days to next 42 days; impact floor `medium` (both in
`config/calendar.yaml`). `event_id = sha1(source|region|title|date)`; a second
event with the same title on the same day gets `|HH:MM` appended. Times are UTC
naive; `time_known=0` means only the date is known (stored as 00:00).

| `source` | What | Coverage | Limits |
|---|---|---|---|
| `forexfactory` | `nfs.faireconomy.media/ff_calendar_thisweek.json` | current week, all nine currencies, impact, forecast, previous | **This week only** (`nextweek` is 404). No `actual` field. Rate-limited: a few requests in 15 minutes returned HTTP 429. |
| (merged) | myfxbook calendar RSS | today's actual values | Today only. Merged into ForexFactory rows on region + time (5 min) + identical previous (and forecast); ambiguous matches are skipped. |
| `cb_schedule` | static list in `calendar.yaml` | FOMC, ECB, BoE, BoJ, RBA, BoC, RBNZ, SNB decisions for 2026-2027 | Must be refreshed by hand yearly. |
| `bea` | `apps.bea.gov/API/signup/release_dates.json` | US GDP, PCE / Personal Income, trade | exact times |
| `census` | Census economic indicator calendar page | US retail sales, durable goods, housing starts | The page currently lists 2027 only, so it yields nothing until January falls inside the window. |
| `eurostat` | Eurostat release calendar iCal | euro-area flash/final HICP, GDP, unemployment | dates only (releases are normally 11:00 CET) |
| `ons` | ONS release search API | UK CPI, labour market, GDP, retail sales | upcoming releases only |
| `fred` | `fred/releases/dates` with `include_release_dates_with_no_data=true` | exact US schedule (payrolls, CPI, PCE, GDP, retail sales, PPI, JOLTS, claims) | **Only with `FRED_API_KEY`; not exercised here (no key).** When it succeeds it replaces `bea`, `census` and `rule`. |
| `rule` | first Friday of the month, 08:30 ET | US Non-Farm Payrolls | **Approximate**, and the title says so. |

ForexFactory rate limit: `load_forexfactory` keeps the last payload in
`backend/.cache/forexfactory_thisweek.json`. A copy younger than 60 minutes is
used without any request; otherwise one request is made; if it fails, a cache
up to 7 days old is used.

Duplicates: any week that has ForexFactory rows supersedes the schedule
sources for that week (their rows in that week are deleted), because
ForexFactory has the richer rows. Rescheduled releases: when a schedule source
is re-read, its future rows that are no longer listed are deleted.

Actuals: stored `actual` / `forecast` / `previous` values are never blanked by
a later run, and an actual that appears later is filled in by the upsert.

### Central-bank dates: what was verified

| Bank | Verified from the bank's own site | Not verified |
|---|---|---|
| FOMC | all 2026 and 2027 dates (2027 is officially tentative) | none |
| ECB | 29 Oct 2026, 17 Dec 2026, all 2027 | Feb-Sep 2026 (page lists upcoming meetings only) |
| BoE | all 2026 and 2027 | none |
| BoJ | all 2026 and 2027 | none |
| RBA | all 2026 and 2027 | none |
| BoC | all 2026 and 2027 | none |
| RBNZ | Apr, May, Jul, Sep, Oct 2026 (from OCR releases in the RBNZ feed) | **18 Feb 2026 and 9 Dec 2026** (from memory); **2027 missing**: the site blocks scripted access |
| SNB | 10 Dec 2026, all 2027 | Mar, Jun, Sep 2026 (page lists upcoming events only) |

Unverified dates carry "(unverified date)" in the event title.

### Gaps

- Beyond the current week there is no forecast/previous, and outside the US,
  UK and euro area only central-bank meetings are known (no free schedule was
  wired for Japan, Australia, Canada, New Zealand, China, Switzerland).
- US CPI and exact payrolls dates beyond the current week need a FRED key: the
  BLS schedule page answers 403 to scripts.
- "Recent" history builds up as the job runs. Actuals exist only for days on
  which the job ran after the release.
- ISM is on none of the free schedules; it appears only via ForexFactory.
