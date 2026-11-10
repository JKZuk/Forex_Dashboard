# Scoring methodology

How the dashboard turns stored data into a sentiment score per region and
horizon, and how to tune it. Code: `backend/app/scoring/`. All weights,
lookbacks and thresholds: `backend/config/scoring.yaml` (the only file you edit
to tune). Nothing here is a forecast or advice; it is a transparent, rule-based
summary of free data.

## 1. The score in one formula

For each region (US, EZ, JP, UK, AU, CA, NZ, CN, CH) and each horizon
(short / medium / long):

```
score = 100 x k x SUM_i ( w_i / W_available ) x signal_i
```

| Term | Meaning |
|---|---|
| `signal_i` | Component reading squashed to **-1..+1**. +1 = bullish for the region's **currency**. `NULL` when there is no data. |
| `w_i` | Configured weight in `scoring.yaml` (times a region multiplier where stated, e.g. trade balance for trade-sensitive regions). |
| `W_available` | Sum of `w_i` over components that had data, so weights are **renormalised** over what is available. |
| `coverage` | `W_available / W_configured`, stored in `scores.coverage` (0..1). |
| `k` | 1 normally. If coverage is below `general.coverage_shrink_below` (default 0.5), `k = coverage / 0.5`. Thin evidence is pulled towards neutral so one surviving component cannot print a "Strong" reading alone. Set the parameter to 0 to disable. |

Every component is stored in `score_components` with its label, raw reading
(`raw_display`), `signal`, `weight`, `contribution` (points) and a one-sentence
`detail`. **Contributions sum exactly to the score.** A component without data
is stored with `signal = NULL`, `contribution = 0` and a reason in `detail`.
Nothing is ever interpolated or invented.

### Labels

| Score | Label |
|---|---|
| >= 50 | Strong Bullish |
| >= 20 | Bullish |
| > -20 | Neutral |
| > -50 | Bearish |
| <= -50 | Strong Bearish |
| no component had data | No Data (score 0, coverage 0) |

Thresholds are in `labels:` in the yaml. Because a score is a weighted average
of several partly independent signals, "Strong" readings are deliberately
rare: they need most components to agree.

## 2. Building blocks

**Relative, not absolute.** An exchange rate is a relative price, so macro
readings are measured against the average of the *other* regions that have the
same data ("peers"): `diff = own - mean(peers)`. At least `general.min_peers`
(2) peers are required, otherwise the reading is treated as missing. China is
scored against everyone else but is excluded from other regions' peer averages
(`general.peer_exclude`), because its growth and rate levels are structurally
different and CNY is managed.

**Squashing.** Raw readings become signals through `tanh(x / scale)`. The
`scale` in the yaml is the reading at which the signal is 0.76; a reading of
2 x scale gives 0.96. One outlier can therefore never dominate. Level
differentials use explicit scales in natural units (e.g. 1.5pp for a 2Y
spread); changes and surprises are standardised by the series' own history
(z-scores), which also makes the engine indifferent to units and to proxies
(e.g. a PMI proxy whose neutral level is 100 rather than 50).

**FX basket index.** Price momentum uses each currency's log index against an
equal-weight basket of the other eight currencies, built from daily returns of
the `FX.<CCY>USD` series (`rel_c = r_c - mean(r_others)`, USD's own return in
USD terms being 0). EURUSD rising on pure dollar weakness is therefore USD
weakness, not EUR strength. If fewer than `general.fx_basket_min_members`
other currencies are available the engine falls back to the USD-basis series
(`FX.USD_INDEX` for the dollar); the raw reading says which was used
("vs basket" / "vs USD" / "USD index").

**Volatility-standardised moves.** A move over N days is divided by
`daily sigma x sqrt(N business days)`, with sigma from the last year (two years
for the 12-month trend) of the series' own changes.

**Direction.** Macro releases follow `catalog.Indicator.bullish_when`: higher
inflation, activity, employment, yields and trade balances are currency-positive
(hawkish / stronger), higher unemployment and jobless claims are negative.

## 3. Short-term score (hours to days)

| Component (yaml key) | Weight | Reads |
|---|---|---|
| `data_surprise` | 0.30 | all `<REGION>.<KEY>` catalog series except yields; `calendar_events` |
| `price_momentum` | 0.25 | `FX.<CCY>USD` (all, for the basket), `FX.USD_INDEX` (fallback) |
| `risk_sentiment` | 0.25 | `GLOBAL.VIX`, `GLOBAL.SPX`, `GLOBAL.HY_SPREAD`, `news_region_sentiment` region `GLOBAL` |
| `news_sentiment` | 0.20 | `news_region_sentiment` for the region, `news_items.kind` |

### 3.1 Data surprises (0.30)

Free data has no consensus forecasts, so the surprise is measured against the
series' own recent behaviour:

- *change mode* (default): `s = (x_t - x_{t-1}) - mean(previous 3 changes)`
- *level mode* (noisy flows: `EMPLOYMENT_CHG`, `JOBLESS_CLAIMS`, `TRADE_BALANCE`):
  `s = x_t - mean(previous 3 levels)`
- *raw change* (`POLICY_RATE`): `s = x_t - x_{t-1}`; only actual moves count, an
  unchanged rate adds no information (this also makes daily step series such as
  the Fed funds target work).

`z = s / sigma`, where sigma is the standard deviation of the same quantity
over the previous 36 observations (needs at least 12), clipped to +/-3 and
signed by `bullish_when`. For the policy rate sigma has a floor of 0.15pp so a
first move after a long hold is not divided by zero.

Where the calendar feed provides both `actual` and `forecast`, a true
actual-vs-forecast item is added: sign of the miss times
`(0.5 + 0.5 x m) x 1.5 sigma`, where `m = min(1, |actual - forecast| /
max(10% of |forecast|, |forecast - previous|))`. Titles containing
"unemployment", "jobless" or "claims" are inverted. This is crude but uses the
only genuine expectations data available. (A release can be counted both from
its series and from the calendar; accepted, since the calendar item is the
better measure.)

Each item gets weight `impact_weight x 0.5^(age / half-life)`: catalog impact
3 (NFP, CPI, core CPI / PCE, policy rate, PMI, unemployment) = 3.5, impact 2 = 2.0,
impact 1 = 1.0; calendar high / medium / low = 3.5 / 2 / 1. Window 28 days,
half-life 10 days. The weighted mean is shrunk with a prior weight of 3 (about
one fresh high-impact release), so a single number cannot max the signal:

```
avg = SUM(w x z) / (SUM(w) + prior_weight);   signal = tanh(avg / 0.6)
```

*Rationale:* surprises relative to trend are what reprice rate expectations in
the days after a release; the highest-impact releases dominate by design.

### 3.2 Short-term price momentum (0.25)

Vol-standardised moves of the basket index over 7 and 30 days (50/50), each
through `tanh(z / 1.25)`. Trend-following: over days, recent direction tends
to persist and it captures whatever information the data feeds miss.

### 3.3 Global risk sentiment x currency beta (0.25)

A single gauge `R` in -1..+1 (+ = risk-on), shared by all regions:

| Input | Weight | Reading |
|---|---|---|
| VIX | 0.35 | minus `tanh(z / 1.25)`, z = average of level z-score vs 1 year and vol-standardised 7-day change |
| S&P 500 | 0.25 | `tanh(z / 1.25)` of 7- and 30-day vol-standardised returns |
| US HY spread | 0.20 | minus `tanh(z / 1.25)`, z = average of level z-score vs 1 year and 30-day change |
| Global news tone | 0.20 | `tanh(tone / 0.25)` of `GLOBAL`-tagged items, 5 days, half-life 2 days |

Missing inputs drop out and the rest are renormalised. Then
`signal = clip(R x beta)` with `risk_beta` from the yaml:

| JPY | CHF | USD | EUR | CNY | GBP | CAD | NZD | AUD |
|---|---|---|---|---|---|---|---|---|
| -1.0 | -0.8 | -0.5 | +0.2 | +0.3 | +0.35 | +0.6 | +0.9 | +1.0 |

*Rationale:* funding / haven currencies (JPY, CHF and partly USD) gain when
risk is shed; commodity and high-beta currencies gain in risk-on; EUR and GBP
are mildly pro-risk.

### 3.4 News and central-bank tone (0.20)

Weighted mean of `news_region_sentiment.score` for the region over 7 days:
weight `0.5^(age / 2 days)`, times 2.5 for `kind = central_bank`. The mean is
shrunk by `W / (W + 3)` (W = total weight, so a couple of headlines are not
trusted much) and squashed with `tanh(x / 0.25)`.

## 4. Medium-term score (weeks to ~2 months)

| Component (yaml key) | Weight | Reads |
|---|---|---|
| `yield_differential` | 0.25 | `<R>.YIELD_2Y`, `<R>.YIELD_10Y` for all regions |
| `data_surprise` | 0.20 | as 3.1, 92-day window |
| `cot_positioning` | 0.15 | `cot_positions.net_pct_oi` for the currency (`USD` = dollar index contract) |
| `price_momentum` | 0.15 | FX basket index |
| `trade_balance` | 0.10 (x1.75 for trade-sensitive regions) | `<R>.TRADE_BALANCE`, `<R>.EXPORTS_YOY` |
| `commodity_china` | 0.15 (CA, AU, NZ), 0.06 (JP, EZ), absent elsewhere | `GLOBAL.OIL`, `GLOBAL.COPPER`, `GLOBAL.GOLD`, `CN.PMI`, `CN.INDUSTRIAL_PROD_YOY`, `CN.RETAIL_SALES_YOY`, `CN.IMPORTS_YOY`, `CN.EXPORTS_YOY`, `CN.GDP_YOY` |

### 4.1 Yield differential (0.25)

Four sub-signals, all relative to peers, blended over those available:

| Sub-signal | Mix | Formula |
|---|---|---|
| 2Y level | 0.45 | `tanh((y2 - peer mean) / 1.5pp)` |
| 2Y change | 0.35 | `tanh((own 63-day change - peers' mean 63-day change) / 0.4pp)` |
| 10Y level | 0.10 | as 2Y level |
| 10Y change | 0.10 | as 2Y change |

*Rationale:* the 2-year spread is the cleanest read on relative policy
expectations and the single most reliable medium-term FX driver; its change
matters about as much as its level. 10Y is secondary.

### 4.2 Cumulative data surprises (0.20)

Same engine as 3.1 over 92 days, half-life 45 days, prior weight 6,
`tanh(avg / 0.5)`. This is the "economic surprise index" idea: a run of
beats or misses shifts the policy outlook over weeks.

### 4.3 Speculative positioning, COT (0.15)

Two ideas, combined explicitly:

- **Trend** (trend-following): change in net % of open interest over 4 and 13
  weeks, each `tanh(change / 10pp)`, averaged. Specs adding longs is bullish.
- **Extreme** (contrarian): mid-rank percentile `p` of the current net % of OI
  within the last 156 weeks (needs 52). `p >= 0.85`: crowded long,
  `extreme = -(p - 0.85) / 0.15`; `p <= 0.15`: crowded short,
  `extreme = +(0.15 - p) / 0.15`; otherwise 0.

```
if the trend points further INTO the extreme:  trend = trend x (1 - 0.5 x |extreme|)
signal = 0.6 x trend + 0.4 x extreme
```

So in the middle 70% of the range the component is pure trend-following
(max +/-0.6). At an extreme the contrarian term takes over: flat positioning at
the 3-year high gives about -0.4, and a trend that is still chasing the crowd is
cut by up to half. With less than a year of history only the trend is used.

### 4.4 Medium-term price momentum (0.15)

As 3.2 with 91- and 182-day lookbacks.

### 4.5 Trade balance trend (0.10; x1.75 for JP, AU, CA, NZ, CN)

- 70%: `z = (mean of last 92 days - mean of the preceding 365 days) / sigma of
  the last 3 years of levels`, `tanh(z / 1.0)`. Own-history standardisation
  makes the unit (local currency or USD) irrelevant.
- 30%: exports y/y minus the peer average, `tanh(diff / 10pp)`.

The multiplier applies to `regions.Region.trade_sensitive`; the stored
`weight` is the multiplied one (0.175).

### 4.6 Commodity prices and China activity (0.15 where applicable)

Only regions listed under `commodity_china.regions` get this component:

| Region | Component weight | Exposures |
|---|---|---|
| CA | 0.15 | oil +1.0 |
| AU | 0.15 | copper +0.5, China +0.5 |
| NZ | 0.15 | broad commodities +0.6, China +0.4 |
| JP | 0.06 | oil -0.6 (importer), China +0.4 |
| EZ | 0.06 | oil -0.4, China +0.6 |

- Commodity signal: vol-standardised return over 91 days (60%) and 182 days
  (40%), `tanh(z / 1.25)`. "Broad" = mean of the oil, copper and gold signals
  that exist.
- China activity gauge: for each CN activity series (weights PMI 3, industrial
  production 2, retail sales 2, imports 2, exports 1, GDP 1) average (a) the
  latest level as a z-score against its own 5 years and (b) the last 92 days
  vs the preceding year in 5-year sigmas; the weighted mean goes through
  `tanh(z / 1.0)`.

Exposures are blended by absolute weight, negative ones flip the sign.

## 5. Long-term score (months to years)

| Component (yaml key) | Weight | Reads |
|---|---|---|
| `real_rate_differential` | 0.25 | `<R>.POLICY_RATE`, `<R>.YIELD_10Y`, `<R>.CORE_CPI_YOY` (else `<R>.CPI_YOY`) |
| `policy_trajectory` | 0.20 | `<R>.POLICY_RATE`, inflation as above |
| `growth_differential` | 0.15 | `<R>.GDP_YOY` |
| `current_account` | 0.15 | `<R>.CURRENT_ACCOUNT_GDP` |
| `trade_balance` | 0.10 (x1.75 trade-sensitive) | `<R>.TRADE_BALANCE` |
| `trend_valuation` | 0.15 | FX basket index |

- **Real rate differential (0.25).** Real policy rate (policy rate minus
  inflation) and real 10Y yield, each relative to peers, `tanh(diff / 1.5pp)`,
  50/50. Inflation = first available of `general.inflation_preference`.
  Capital migrates to where inflation-adjusted returns are highest.
- **Policy trajectory (0.20).** 60%: 12-month change in the policy rate minus
  peers' mean change, `tanh(/1.0pp)`. 40%: inflation gap (inflation minus the
  target in `general.inflation_target`, default 2%, AU 2.5%, CN 3%) relative to
  peers, `tanh(/1.0pp)`: above-target inflation implies a hawkish bias. Note
  the deliberate tension with the real-rate component: high inflation is
  hawkish here but erodes real rates there, so it only helps a currency whose
  central bank actually responds.
- **Growth differential (0.15).** 60%: GDP y/y minus peer mean,
  `tanh(/1.5pp)`. 40%: growth as a z-score against the economy's own last 20
  quarters, relative to peers' same measure, `tanh(/1.0)`: this rewards
  acceleration, not just structurally fast growers.
- **Current account (0.15).** 70%: % of GDP minus peer mean, `tanh(/4pp)`.
  30%: own 2-year change, `tanh(/2pp)`. Surplus economies have structural
  demand for their currency; deficit economies depend on capital inflows.
- **Structural trade balance (0.10, x1.75 trade-sensitive).** Last 12 months
  vs the preceding 24 months in 5-year sigmas, `tanh(z / 1.0)`.
- **12-month trend and valuation (0.15).** 75%: vol-standardised 12-month
  return of the basket index, `tanh(z / 1.0)` (trend-following). 25%:
  deviation from the 5-year average (needs 3 years of history) in sigmas,
  `-tanh(z / 2.0)`: a small contrarian weight when a currency is far from its
  long-run norm.

## 6. Why these weights

- Rates dominate: rate differentials and their drivers (surprises in CPI,
  jobs, policy decisions) carry 45-50% of every horizon directly or
  indirectly, which matches how G10 FX actually trades.
- Short horizon: flow and information (surprises, price action, risk mood,
  headlines). No slow fundamentals, which do not move in days.
- Medium horizon: the 2Y spread is the anchor; positioning and momentum
  describe how far the market has already moved; trade and commodities matter
  for the currencies they matter for.
- Long horizon: real returns, policy regime, growth and external balances;
  price trend gets a modest weight with a contrarian valuation tilt.
- Trade balance: x1.75 for Japan and the commodity bloc (and China), in the
  medium and long horizons only.
- China is scored like any region where data allows and additionally feeds
  AUD / NZD (and lightly JPY / EUR) through the China activity gauge.

## 7. Point-in-time rules

- A score for `as_of` only uses observations with `obs_date <= as_of`, news
  and calendar items published by the end of that day, and COT reports dated
  on or before it.
- **Today's run** uses everything in the database (it has been published by
  definition).
- **Historical dates (backfill)** additionally apply a publication lag, since
  `obs_date` is the *start* of the reference period: an observation becomes
  known on `obs_date + lag`:

  | Frequency | Lag (days) | Example |
  |---|---|---|
  | Daily | 0 | |
  | Weekly | 5 | |
  | Monthly | 40 | September data known ~10 October |
  | Quarterly | 125 | Q3 (1 July) known ~3 November |
  | Annual | 455 | |
  | Monthly yields / policy rate / copper | 31-35 | monthly averages are known at month end |
  | COT | 3 | Tuesday positions published Friday |

  This is an approximation: real release dates vary by indicator and country
  (NFP is faster than 40 days, trade and current-account data slower). It is
  conservative enough to keep the history honest but backfilled scores can
  differ slightly from what a live run would have produced on the day. Data
  *revisions* are not modelled: history is scored with today's vintage.
- **Staleness.** A series whose latest known observation is older than
  `general.max_stale_days` (D 14, W 35, M 150, Q 320, A 900 days) is treated as
  missing instead of being carried forward indefinitely.
- News and calendar history does not exist for old backfill dates; those
  components drop out and `coverage` shows it (short-term coverage is about
  0.8 before the news history starts).
- Backfill stores a `Score` row for every date; component rows and conclusions
  are kept for every date too (`backfill.store_detail: all`; set `latest` to
  keep them for the last date only).

## 8. Written conclusions

`conclusions.headline`, e.g. `USD: bullish short-term, neutral medium-term,
bearish long-term` (or "neutral across all horizons"). `conclusions.text` is
built from templates, no LLM:

1. the three labels with scores;
2. the top two supports and top two drags across all horizons (components
   contributing at least 3 points), each with its horizon and raw reading;
3. a divergence note where relevant ("short-term bounce inside a longer-term
   downtrend", "all three horizons point the same way", "horizons disagree
   markedly" when they are 40+ points apart);
4. a coverage warning for any horizon below 60% coverage.

## 9. How to tune

Edit `backend/config/scoring.yaml`; the file is re-read on every scoring run.
Then `python -m app.cli score` (today) or `python -m app.cli backfill
2023-01-01` (rewrite history with the new settings).

| You want | Change |
|---|---|
| A component to matter more / less | its `weight` (weights are renormalised, they need not sum to 1) |
| Switch a component off | `weight: 0`, or delete it |
| A component to react more aggressively | lower its `scale` / `z_scale` |
| Different label cut-offs | `labels:` |
| Different haven / risk behaviour | `risk_beta:` |
| Different commodity links | `horizons.medium.components.commodity_china.regions` |
| Trade to matter more for the trade-sensitive bloc | `trade_sensitive_multiplier` |
| Faster / slower surprise decay | `window_days`, `half_life_days` |
| Stricter history (no look-ahead) | `general.publication_lag_days` |
| Less shrinkage when data is thin | `general.coverage_shrink_below` (0 = off) |

To add a component: write a function in `app/scoring/components/`, decorate it
with `@register("my_kind")`, import the module in `components/__init__.py`,
and reference `kind: my_kind` under a horizon in the yaml. To use a new (e.g.
paid) data source nothing in scoring changes as long as ingestion stores it
under the catalog series id.

## 10. Known limitations

- No consensus forecasts in free data: "surprise" is mostly relative to each
  series' own trend, not to market expectations.
- A component counts as fully covered when any of its sub-inputs has data
  (e.g. yield differential with only a 10Y series).
- Relative readings depend on which peers have data; if a peer's series
  appears or disappears the differential can jump.
- Some 2Y "yields" are proxies (see `series.note`, e.g. SONIA for the UK); the
  engine uses them as given.
- Monthly series mixed with daily ones (e.g. yields) are compared at their
  latest known value; a monthly average lags a daily close.
- CNY is managed; China's scores describe the macro backdrop more than a
  tradeable view.
- Publication lags are per frequency, not per indicator; revisions are ignored.
