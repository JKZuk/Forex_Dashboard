# Market data sources: FX prices and COT positioning

Both sources are free and keyless. Code: `backend/app/ingest/fx_prices.py`,
`backend/app/ingest/cot.py` (helpers `_fx_providers.py`, `_fx_http.py`).
Run with `python -m app.cli refresh fx_prices cot`.

## FX prices (`fx_prices`)

### What is stored

| Series id | Meaning |
|---|---|
| `FX.EURUSD`, `FX.JPYUSD`, `FX.GBPUSD`, `FX.AUDUSD`, `FX.CADUSD`, `FX.NZDUSD`, `FX.CNYUSD`, `FX.CHFUSD` | USD value of ONE unit of the currency. `FX.JPYUSD` is ~0.0063, not ~158. Rising always = that currency strengthening vs USD. |
| `FX.SEKUSD` | Same convention. Stored only because it is a component of the USD index; SEK is not a region. |
| `FX.USD_INDEX` | Dollar basket index, higher = stronger USD, 100 at the first common date (2010-01-04). |

All are in `observations` with `region="FX"`, `category="fx"`, `frequency="D"`,
history from 2010-01-01 (first fix: 2010-01-04).

### Providers

Providers implement the `FxProvider` protocol in `_fx_providers.py`
(`fetch(currencies, start, end) -> {ccy: {date: usd_per_unit}}`) and are tried in
the order of `PROVIDERS`; the first that returns data wins. To add yfinance,
Twelve Data or a paid feed, write a class with that method and put it in the list.

1. **Frankfurter** (primary) - `https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=EUR,JPY,...`
   One call returns the whole range (about 0.5 MB for 2010 to today). The older
   host `api.frankfurter.app` still answers but `api.frankfurter.dev/v1` is the
   current one. The API quotes currency units per 1 USD; we invert. It snaps the
   start date back to the previous business day; that extra row is clipped.
2. **ECB Data Portal** (fallback) - `https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY+....EUR.SP00.A?startPeriod=...&endPeriod=...&format=csvdata`
   The same underlying data, quoted per 1 EUR and crossed through the USD fix.
   Checked against Frankfurter: identical dates, values within 0.004% (rounding
   of Frankfurter's pre-computed USD crosses).

### USD index

`FX.USD_INDEX` uses the ICE U.S. Dollar Index (DXY) fixed weights in a geometric
basket:

| EUR | JPY | GBP | CAD | SEK | CHF |
|---|---|---|---|---|---|
| 57.6% | 13.6% | 11.9% | 9.1% | 4.2% | 3.6% |

`index_t = 100 * prod((p_t / p_0) ^ -w)` where `p` is USD per unit of the
component and `p_0` is its value on the first date all six components exist.
AUD, NZD and CNY carry no weight (as in DXY). The index is recomputed from the
stored component series on every run, so the base date never drifts.

It tracks DXY's moves but is not the official level: it is rebased to 100
rather than DXY's 1973 base (multiply by roughly 0.775, the DXY close around the
base date, for an approximate DXY level) and is built from ECB fixes rather than
live quotes.

### Limitations

- ECB reference rates: **one fix per TARGET business day** (set around 14:15 CET,
  published around 16:00 CET). Not intraday, not a New York close, no
  open/high/low, no weekends or TARGET holidays (even if other markets trade).
  Today's value appears only after publication.
- Non-EUR pairs are crosses of two euro fixes.
- CNY is the onshore rate as fixed by the ECB, not CNH.
- Incremental: each run re-fetches from the oldest "last stored date" minus 7
  days; full history if any series is empty.

## COT positioning (`cot`)

### Source

CFTC Public Reporting Environment (Socrata), **Legacy report, Futures Only**:
`https://publicreporting.cftc.gov/resource/6dca-aqww.json`

Query (SoQL): `$select` of the five fields below, `$where=cftc_contract_market_code in (...) AND report_date_as_yyyy_mm_dd >= '<start>T00:00:00'`,
`$order`, `$limit=10000` with `$offset` paging. No app token is needed at this
volume (one small request per run).

Fields used: `cftc_contract_market_code`, `report_date_as_yyyy_mm_dd`,
`noncomm_positions_long_all`, `noncomm_positions_short_all`, `open_interest_all`.

The yearly zips at `cftc.gov/files/dea/history/deacot<YYYY>.zip` hold the same
data but need a full-year download per run, so they are not used.

### Contracts

Matched by CFTC contract market code, never by name (names changed in February
2022, e.g. "BRITISH POUND STERLING" to "BRITISH POUND", "U.S. DOLLAR INDEX" to
"USD INDEX"; the codes did not).

| Stored `currency` | Code | Contract |
|---|---|---|
| EUR | 099741 | Euro FX - CME |
| JPY | 097741 | Japanese Yen - CME |
| GBP | 096742 | British Pound - CME |
| AUD | 232741 | Australian Dollar - CME |
| CAD | 090741 | Canadian Dollar - CME |
| NZD | 112741 | NZ Dollar - CME |
| CHF | 092741 | Swiss Franc - CME |
| USD | 098662 | USD Index - ICE Futures U.S. |

No CNY: there is no CFTC-reported yuan contract with meaningful positioning.

### What is stored (`cot_positions`)

`noncomm_long`, `noncomm_short`, `noncomm_net = long - short`, `open_interest`,
`net_pct_oi = net / open_interest * 100`. History from 2015-01-06 (first report
of 2015), weekly.

Sign convention: every contract is quoted currency-vs-USD, so **positive net =
speculators long that currency against the dollar**, no flips. For `USD`
(dollar index) positive = long the dollar.

### Limitations

- Weekly. `report_date` is the Tuesday the positions were measured; the CFTC
  publishes on Friday ~15:30 ET (later in US holiday weeks), so data is 3+ days
  old on release and up to 10 days old just before the next one.
- Publication stops during US government shutdowns and is backfilled later. The
  job appends `STALE` to its message when the latest report is over 14 days old.
- "Non-commercial" in the legacy report is the classic speculator proxy but
  mixes hedge funds, CTAs and other reportables. Futures only: no options, and
  nothing about the much larger OTC spot/forward market.
- The dollar-index contract is small (roughly 50k contracts of open interest)
  so its `net_pct_oi` is noisier than the CME currencies.
- Rows with zero open interest are skipped.
- Incremental: each run re-fetches the last 28 days (to pick up CFTC
  corrections) and upserts; full history if any currency is empty.

## HTTP behaviour

`_fx_http.get`: 10 s connect / 60 s read timeout, up to 3 attempts with
exponential backoff on network errors and HTTP 429/5xx, User-Agent
`fx-dashboard/1.0 (python-requests)` (no personal information).
