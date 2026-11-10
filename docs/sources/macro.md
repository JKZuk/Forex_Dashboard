# Macro data sources: `macro_fred` and `macro_official`

Everything here is free and keyless. Code: `backend/app/ingest/macro_fred.py`
(mapping in `_fred_series.py`), `backend/app/ingest/macro_official.py`, shared
helpers in `_macro_util.py`. Run with `python -m app.cli refresh macro_fred macro_official`.

All dates and "latest observation" values below are what the sources returned on
**2026-11-10**.

## Conventions (read this before using the series)

- Series id is `<REGION>.<KEY>`; every id is written by exactly one source.
- `obs_date` of a monthly or quarterly observation is the **first day of the
  reference period** (September 2026 -> `2026-09-01`, 2026-Q3 -> `2026-07-01`).
  It is not the release date. Weekly US jobless claims are dated by week-ending
  Saturday. Daily series only have business days: forward-fill when aligning.
- History starts 2010-01-01 unless the source starts later (see the tables).
- Units follow `catalog.py`: rates, yields and y/y changes in percent
  (`4.25` = 4.25%), `EMPLOYMENT_CHG` and `JOBLESS_CLAIMS` in thousands.
- `TRADE_BALANCE` is a monthly goods balance in **billions**: `USD bn` everywhere
  except the euro area (`EUR bn`). Check `SeriesMeta.unit`. Positive = surplus.
  Levels are not comparable across regions (compare each with its own history).
- `PMI` is **not** the S&P Global / ISM PMI (proprietary). For all nine regions it
  is the OECD business confidence indicator for manufacturing, amplitude-adjusted,
  **neutral = 100** (long-run average), and it usually stays within a few points of 100. It moves
  far less than a PMI: a reading of 101 is already clearly expansionary.
- `CONSUMER_CONF` is the OECD consumer confidence indicator (neutral = 100) for
  every region except the US, which is the University of Michigan index
  (1966Q1 = 100, no fixed neutral level, currently around 50). Use z-scores or
  changes, never the raw level, when comparing regions.
- `YIELD_2Y` is a real 2-year government yield for US, EZ, JP, AU and CA. For UK,
  NZ, CN and CH it is a short-rate proxy, flagged `proxy:` in `SeriesMeta.note`.
- Frequencies are mixed on purpose (the freshest free series wins): yields are
  daily for US, EZ, JP, AU, CA but monthly averages for UK (10Y), NZ, CN, CH.
  New Zealand CPI, unemployment, retail and production are quarterly because
  nothing more frequent is published.
- `SeriesMeta.note` carries the definition and every proxy caveat for a series.

## Sources

| Source (`SeriesMeta.source`) | Endpoint | Used for |
|---|---|---|
| `fred` | `fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>&cosd=<date>` (keyless) or `api.stlouisfed.org/fred/series/observations` when `FRED_API_KEY` is set | US releases and yields, ECB deposit rate, SONIA, global risk gauges |
| `oecd` | `sdmx.oecd.org/public/rest/data/<flow>/<key>` (SDMX-CSV). Flows `DSD_KEI@DF_KEI`, `DSD_STES@DF_CLI`, `DSD_PRICES@DF_PRICES_ALL` | Most non-US releases, 3 requests per run |
| `bis` | `stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/...`, `.../WS_LONG_CPI/1.0/...` (`format=csv&detail=dataonly`) | Policy rates JP, UK, NZ, CN, CH; Swiss CPI |
| `eurostat` | `ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/<dataset>/<key>?format=SDMX-CSV` | Euro area HICP, core HICP, unemployment, goods trade |
| `ecb` | `data-api.ecb.europa.eu/service/data/YC/...` | Euro area 2Y and 10Y (AAA government spot curve), daily |
| `boc` | `www.bankofcanada.ca/valet/observations/<ids>/json` | Canada policy rate, 2Y, 10Y (daily), CPI-trim |
| `mof_japan` | `www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv` + `.../jgbcme.csv` (current month) | JGB 2Y and 10Y, daily |
| `rba` | `www.rba.gov.au/statistics/tables/csv/f1-data.csv`, `f2-data.csv` | Australia cash rate target, 2Y, 10Y, daily |
| `estat_japan` | `dashboard.e-stat.go.jp/api/1.0/Json/getData` (Statistics Dashboard, no appId needed) | Japan CPI and core CPI |

### FRED notes

- Without a key the public CSV download is used; with `FRED_API_KEY` the official
  JSON API. Both return the same observations. Only the keyless path was tested
  live (no key was available); the API path is covered by unit tests against the
  documented response shape.
- FRED's edge server drops requests carrying an unrecognised custom `User-Agent`
  (connection reset / timeout), so the default `python-requests/x.y` agent is
  sent. It is generic and contains nothing personal.
- Monthly and quarterly ids are re-downloaded in full each run (about 200 rows
  each, and it picks up revisions). Daily and weekly ids are fetched from the last
  stored date minus 45 days.
- Y/y rates are computed from the index level. The US published no October 2025
  CPI, so the October 2026 y/y for `US.CPI_YOY` and `US.CORE_CPI_YOY` uses a
  log-linear interpolation of the September and November 2025 index as its base
  (`_macro_util.yoy_pct`); October 2025 itself has no observation.
- FRED limits history for two licensed series: `SP500` (last 10 years) and
  `BAMLH0A0HYM2` (last 3 years). `GLOBAL.HY_SPREAD` therefore starts 2023-11.
- Checked and rejected because they are stale on FRED (last observation in
  brackets): `CPALTT01JPM659N` (2021-06), `CPALTT01GBM659N` / `CPALTT01CAM659N`
  (2025-03), `CPALTT01CNM659N` / `CPALTT01CHM659N` (2025-04), `LRHUTTTTEZM156S`
  (2023-01), `BSCICP03USM665S` / `CSCICP03USM665S` (2024-01), `USAB6BLTT02STSAQ`
  (2024-10), `IRSTCB01JPM156N` (2023-12), `IR3TIB01GBM156N` (2026-01), euro-area
  `IRLTLT01EZM156N` / `XTEXVA01EZM667S` (2026-01 / 2023-04), `NAEXKP01EZQ657S`
  (2023-01). Ids that no longer exist: `GOLDAMGBD228NLBM`, `NAPM`,
  `CPALTT01EZM659N`, `PRINTO01*`, `SLRTTO01*`, `*B6BLTT02STSAQ` for non-US.
- Fresh on FRED but not used because the OECD API serves the same data one to
  two months earlier: `IRLTLT01<cc>M156N`, `IR3TIB01<cc>M156N`,
  `XTEXVA01/XTIMVA01/XTNTVA01<cc>M667S`, `LRHUTTTT<cc>M156S`, `NAEXKP01<cc>Q657S`.
  They are a ready fallback: add a `FredSeries` line and remove the region from
  the matching `KeiRule`.

### Official-source notes

- OECD, Eurostat, BIS CPI, BoC, MoF, RBA and e-Stat are re-downloaded in full on
  every run (a few MB in total; a full first run of both modules took about 75
  seconds, a re-run about 30). BIS policy rates and the ECB yield
  curve are incremental (last stored date minus 45 days).
- **BIS policy rates lag by up to two weeks.** On 2026-11-10 the BIS file ended
  2026-11-02/03 and still showed UK Bank Rate at 3.75% although the BoE's own
  database already showed 4.00% from 2026-11-05. The Bank of England database
  answers `403 Access Denied` to scripted clients, so there is no fresher free
  source for the UK; `UK.YIELD_2Y` (SONIA, daily) reflects a change within a day.
- OECD still labels the euro area `EA20`; Eurostat uses `EA21` since Bulgaria
  joined in 2026 (constant `EUROSTAT_EA`). A further enlargement will need that
  constant updated; the run reports "no data" for the Eurostat series if so.
- Eurostat replaced `prc_hicp_manr` with `prc_hicp_minr` (ECOICOP 2) in 2026;
  the new dataset is used. It includes the flash estimate, so euro-area CPI is
  usually the first October/November print available.
- `AU.CPI_YOY` and `AU.CORE_CPI_YOY`: Australia's complete monthly CPI only
  exists from 2025-04. Earlier observations in the same series are the quarterly
  CPI (dated at quarter start), so the series changes frequency in April 2025.
- `JP.CORE_CPI_YOY` is CPI less fresh food (the BoJ's definition). `CA.CORE_CPI_YOY`
  is CPI-trim (one of the BoC's two preferred measures). Others are CPI excluding
  food and energy. Euro area core excludes energy, food, alcohol and tobacco.
- `CH.UNEMPLOYMENT` is the quarterly ILO survey rate (5.0% in 2026-Q2), not the lower
  monthly SECO registered rate that Swiss headlines quote.
- `EMPLOYMENT_CHG` for JP, AU, CA is the monthly change in total employment from
  the labour force survey (thousands). Japan's is very noisy month to month.
- `EXPORTS_YOY` / `IMPORTS_YOY` are nominal values (USD for OECD-sourced regions,
  EUR for the euro area), so exchange-rate moves leak into them.
- OECD trade for the euro area is absent, hence Eurostat (extra-euro-area trade).

## Gaps

| Series | Status |
|---|---|
| `GLOBAL.GOLD` | **Missing.** FRED removed the LBMA fixings in 2022; LBMA's JSON feed answers 403 to scripted clients and stooq's CSV endpoint returned 404; the IMF commodity series on DBnomics ends 2025-06. Needs a keyed or unofficial source. |
| `PCE_YOY`, `CORE_PCE_YOY`, `JOBLESS_CLAIMS` | US-only concepts; not populated for other regions by design. |
| `EMPLOYMENT_CHG` for EZ, UK, NZ, CH, CN | No free monthly employment level (UK and NZ are quarterly / 3-month averages). |
| `AU.RETAIL_SALES_YOY` | Missing. The OECD KEI feed carries no monthly retail growth rate for Australia. |
| `CN.CORE_CPI_YOY`, `CN.UNEMPLOYMENT`, `CN.RETAIL_SALES_YOY`, `CN.INDUSTRIAL_PROD_YOY` | Missing. Not in the OECD feeds; the NBS mirror on DBnomics stopped at 2026-02. |
| `CH.CORE_CPI_YOY` | Missing. OECD's Swiss core CPI stopped at 2025-12; the SNB CPI cube only has the headline rate. |
| Daily 2Y/10Y yields for UK, NZ, CH, CN | Not available keyless. UK 10Y, and NZ / CH / CN 10Y and short rates, are OECD monthly averages. The SNB's yield cubes stopped updating in 2025-07. |
| True PMIs | Proprietary. Replaced by the OECD business confidence proxy (neutral 100). |

Series the run flagged as stale on 2026-11-10 (newest observation older than
about four months for monthly data): `UK.UNEMPLOYMENT` (2026-07, the ONS
three-month average reaches the OECD late), `JP.RETAIL_SALES_YOY` (2026-06),
`CH.INDUSTRIAL_PROD_YOY` (2026-06), `CA.PMI` (2026-07). The run reports this list
in `refresh_runs.message` every time.

## Coverage matrix

Generated from the SQLite test database after a full run on 2026-11-10.
"First obs" later than 2010-01 means the source starts later.

### US

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | fred | `DFEDTARU` | D | % | 2010-01-01 | 2026-11-10 | Fed funds target range, upper bound |
| CPI_YOY | fred | `CPIAUCSL` | M | % | 2010-01-01 | 2026-10-01 | CPI-U all items SA, y/y from index |
| CORE_CPI_YOY | fred | `CPILFESL` | M | % | 2010-01-01 | 2026-10-01 | CPI-U ex food & energy SA, y/y from index |
| PCE_YOY | fred | `PCEPI` | M | % | 2010-01-01 | 2026-09-01 | PCE price index, y/y from index |
| CORE_PCE_YOY | fred | `PCEPILFE` | M | % | 2010-01-01 | 2026-09-01 | PCE ex food & energy, y/y from index |
| UNEMPLOYMENT | fred | `UNRATE` | M | % | 2010-01-01 | 2026-10-01 | U-3 unemployment rate SA |
| EMPLOYMENT_CHG | fred | `PAYEMS` | M | thousands | 2010-01-01 | 2026-10-01 | Nonfarm payrolls, monthly change (NFP) |
| JOBLESS_CLAIMS | fred | `ICSA` | W | thousands | 2010-01-02 | 2026-10-31 | Initial claims SA, week ending Saturday |
| PMI | oecd | `DF_CLI/USA.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | fred | `UMCSENT` | M | index | 2010-01-01 | 2026-09-01 | University of Michigan sentiment (1966Q1=100); no fixed neutral level |
| RETAIL_SALES_YOY | fred | `RSAFS` | M | % | 2010-01-01 | 2026-09-01 | Retail & food services sales, nominal, y/y |
| INDUSTRIAL_PROD_YOY | fred | `INDPRO` | M | % | 2010-01-01 | 2026-09-01 | Industrial production index, y/y |
| GDP_YOY | fred | `GDPC1` | Q | % | 2010-01-01 | 2026-07-01 | Real GDP SAAR level, y/y |
| YIELD_2Y | fred | `DGS2` | D | % | 2010-01-04 | 2026-11-06 | 2Y Treasury constant maturity |
| YIELD_10Y | fred | `DGS10` | D | % | 2010-01-04 | 2026-11-06 | 10Y Treasury constant maturity |
| TRADE_BALANCE | fred | `BOPGTB` | M | USD bn | 2010-01-01 | 2026-09-01 | Goods trade balance, BoP basis, SA, monthly |
| EXPORTS_YOY | fred | `BOPGEXP` | M | % | 2010-01-01 | 2026-09-01 | Goods exports, BoP basis, nominal USD, y/y |
| IMPORTS_YOY | fred | `BOPGIMP` | M | % | 2010-01-01 | 2026-09-01 | Goods imports, BoP basis, nominal USD, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/USA.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### EZ

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | fred | `ECBDFR` | D | % | 2010-01-01 | 2026-11-10 | ECB deposit facility rate |
| CPI_YOY | eurostat | `prc_hicp_minr/M.RCH_A.TOTAL.EA` | M | % | 2010-01-01 | 2026-10-01 | HICP all items, y/y (includes flash estimate) |
| CORE_CPI_YOY | eurostat | `prc_hicp_minr/M.RCH_A.TOT_X_NRG_FOOD.EA` | M | % | 2010-01-01 | 2026-10-01 | HICP ex energy, food, alcohol & tobacco, y/y (includes flash estimate) |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | eurostat | `une_rt_m/M.SA.TOTAL.PC_ACT.T.EA21` | M | % | 2010-01-01 | 2026-09-01 | harmonised unemployment rate, SA |
| EMPLOYMENT_CHG | — | — | — | — | — | — | **missing** |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/EA20.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/EA20.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | oecd | `DF_KEI/EA20.M.TOVM.GR.G47.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | retail trade volume (ex motor vehicles), y/y |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/EA20.M.PRVM.GR.BTE.Y.GY` | M | % | 2010-01-01 | 2026-08-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/EA20.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-07-01 | real GDP, y/y |
| YIELD_2Y | ecb | `YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y` | D | % | 2010-01-04 | 2026-11-09 | ECB spot yield curve, AAA euro-area governments |
| YIELD_10Y | ecb | `YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y` | D | % | 2010-01-04 | 2026-11-09 | ECB spot yield curve, AAA euro-area governments |
| TRADE_BALANCE | eurostat | `ei_etea_m/M.BAL_RT.MIO-EUR-SA..ET-T.EA21` | M | EUR bn | 2010-01-01 | 2026-08-01 | extra-euro-area goods trade balance, SA |
| EXPORTS_YOY | eurostat | `ei_etea_m/M.EXP.MIO-EUR-SA..ET-T.EA21` | M | % | 2010-01-01 | 2026-08-01 | extra-euro-area goods exports, EUR value SA, y/y |
| IMPORTS_YOY | eurostat | `ei_etea_m/M.IMP.MIO-EUR-SA..ET-T.EA21` | M | % | 2010-01-01 | 2026-08-01 | extra-euro-area goods imports, EUR value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/EA20.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2013-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### JP

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | bis | `WS_CBPOL/D.JP` | D | % | 2010-01-01 | 2026-11-03 | BIS central bank policy rate, daily; published with a lag of up to ~2 weeks |
| CPI_YOY | estat_japan | `0703010601010030000` | M | % | 2010-01-01 | 2026-09-01 | national CPI, all items, y/y |
| CORE_CPI_YOY | estat_japan | `0703010601010030010` | M | % | 2010-01-01 | 2026-09-01 | CPI all items less fresh food (the BoJ's core), y/y |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | oecd | `DF_KEI/JPN.M.UNEMP.PT_LF._T.Y._Z` | M | % | 2010-01-01 | 2026-09-01 | labour-force-survey unemployment rate, SA |
| EMPLOYMENT_CHG | oecd | `DF_KEI/JPN.M.EMP.PS._T.Y._Z` | M | thousands | 2010-01-01 | 2026-09-01 | total employment SA, monthly change |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/JPN.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-09-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/JPN.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | oecd | `DF_KEI/JPN.M.TOVM.GR.G47.Y.GY` | M | % | 2010-01-01 | 2026-06-01 | retail trade volume (ex motor vehicles), y/y |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/JPN.M.PRVM.GR.BTE.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/JPN.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | real GDP, y/y |
| YIELD_2Y | mof_japan | `jgbcme_all.csv:2Y` | D | % | 2010-01-04 | 2026-11-09 | JGB 2Y constant-maturity yield |
| YIELD_10Y | mof_japan | `jgbcme_all.csv:10Y` | D | % | 2010-01-04 | 2026-11-09 | JGB 10Y constant-maturity yield |
| TRADE_BALANCE | oecd | `DF_KEI/JPN.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-09-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/JPN.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/JPN.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/JPN.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### UK

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | bis | `WS_CBPOL/D.GB` | D | % | 2010-01-04 | 2026-11-02 | BIS central bank policy rate, daily; published with a lag of up to ~2 weeks |
| CPI_YOY | oecd | `DF_KEI/GBR.M.CP.GR._Z._Z.GY` | M | % | 2010-01-01 | 2026-09-01 | national CPI, all items, y/y |
| CORE_CPI_YOY | oecd | `DF_PRICES_ALL/GBR.M.N.CPI.PA._TXCP01_NRG.N.GY` | M | % | 2010-01-01 | 2026-09-01 | CPI all items ex food & energy, y/y |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | oecd | `DF_KEI/GBR.M.UNEMP.PT_LF._T.Y._Z` | M | % | 2010-01-01 | 2026-07-01 | labour-force-survey unemployment rate, SA |
| EMPLOYMENT_CHG | — | — | — | — | — | — | **missing** |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/GBR.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/GBR.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | oecd | `DF_KEI/GBR.M.TOVM.GR.G47.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | retail trade volume (ex motor vehicles), y/y |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/GBR.M.PRVM.GR.BTE.Y.GY` | M | % | 2010-01-01 | 2026-08-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/GBR.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | real GDP, y/y |
| YIELD_2Y | fred | `IUDSOIA` | D | % | 2010-01-04 | 2026-11-06 | proxy: SONIA overnight rate (no free 2Y gilt series) |
| YIELD_10Y | oecd | `DF_KEI/GBR.M.IRLT.PA._Z._Z._Z` | M | % | 2010-01-01 | 2026-10-01 | long-term (10Y) government bond yield, monthly average |
| TRADE_BALANCE | oecd | `DF_KEI/GBR.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-08-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/GBR.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-08-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/GBR.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-08-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/GBR.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### AU

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | rba | `F1:FIRMMCRTD` | D | % | 2011-01-04 | 2026-11-09 | RBA cash rate target |
| CPI_YOY | oecd | `DF_KEI/AUS.M.CP.GR._Z._Z.GY` | M | % | 2010-01-01 | 2026-09-01 | national CPI, all items, y/y; quarterly observations before the monthly series starts |
| CORE_CPI_YOY | oecd | `DF_PRICES_ALL/AUS.M.N.CPI.PA._TXCP01_NRG.N.GY` | M | % | 2010-01-01 | 2026-09-01 | CPI all items ex food & energy, y/y; quarterly observations before the monthly series starts |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | oecd | `DF_KEI/AUS.M.UNEMP.PT_LF._T.Y._Z` | M | % | 2010-01-01 | 2026-09-01 | labour-force-survey unemployment rate, SA |
| EMPLOYMENT_CHG | oecd | `DF_KEI/AUS.M.EMP.PS._T.Y._Z` | M | thousands | 2010-01-01 | 2026-09-01 | total employment SA, monthly change |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/AUS.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-08-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/AUS.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | — | — | — | — | — | — | **missing** |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/AUS.Q.PRVM.GR.BTE.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/AUS.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | real GDP, y/y |
| YIELD_2Y | rba | `F2:FCMYGBAG2D` | D | % | 2013-09-02 | 2026-11-04 | Australian government 2Y bond yield, interpolated |
| YIELD_10Y | rba | `F2:FCMYGBAG10D` | D | % | 2013-05-20 | 2026-11-04 | Australian government 10Y bond yield, interpolated |
| TRADE_BALANCE | oecd | `DF_KEI/AUS.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-09-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/AUS.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/AUS.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/AUS.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### CA

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | boc | `V39079` | D | % | 2010-01-01 | 2026-11-09 | BoC target for the overnight rate |
| CPI_YOY | oecd | `DF_KEI/CAN.M.CP.GR._Z._Z.GY` | M | % | 2010-01-01 | 2026-09-01 | national CPI, all items, y/y |
| CORE_CPI_YOY | boc | `CPI_TRIM` | M | % | 2010-01-01 | 2026-09-01 | BoC preferred core measure CPI-trim, y/y |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | oecd | `DF_KEI/CAN.M.UNEMP.PT_LF._T.Y._Z` | M | % | 2010-01-01 | 2026-10-01 | labour-force-survey unemployment rate, SA |
| EMPLOYMENT_CHG | oecd | `DF_KEI/CAN.M.EMP.PS._T.Y._Z` | M | thousands | 2010-01-01 | 2026-10-01 | total employment SA, monthly change |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/CAN.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-07-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/CAN.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | oecd | `DF_KEI/CAN.M.TOVM.GR.G47.Y.GY` | M | % | 2010-01-01 | 2026-08-01 | retail trade volume (ex motor vehicles), y/y |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/CAN.M.PRVM.GR.BTE.Y.GY` | M | % | 2010-01-01 | 2026-08-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/CAN.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-07-01 | real GDP, y/y |
| YIELD_2Y | boc | `BD.CDN.2YR.DQ.YLD` | D | % | 2010-01-04 | 2026-11-09 | Government of Canada 2Y benchmark bond yield |
| YIELD_10Y | boc | `BD.CDN.10YR.DQ.YLD` | D | % | 2010-01-04 | 2026-11-09 | Government of Canada 10Y benchmark bond yield |
| TRADE_BALANCE | oecd | `DF_KEI/CAN.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-09-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/CAN.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/CAN.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/CAN.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### NZ

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | bis | `WS_CBPOL/D.NZ` | D | % | 2010-01-01 | 2026-10-30 | BIS central bank policy rate, daily; published with a lag of up to ~2 weeks |
| CPI_YOY | oecd | `DF_KEI/NZL.Q.CP.GR._Z._Z.GY` | Q | % | 2010-01-01 | 2026-07-01 | national CPI, all items, y/y |
| CORE_CPI_YOY | oecd | `DF_PRICES_ALL/NZL.Q.N.CPI.PA._TXCP01_NRG.N.GY` | Q | % | 2010-01-01 | 2026-07-01 | CPI all items ex food & energy, y/y |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | oecd | `DF_KEI/NZL.Q.UNEMP.PT_LF._T.Y._Z` | Q | % | 2010-01-01 | 2026-07-01 | labour-force-survey unemployment rate, SA |
| EMPLOYMENT_CHG | — | — | — | — | — | — | **missing** |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/NZL.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-09-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/NZL.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-09-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | oecd | `DF_KEI/NZL.Q.TOVM.GR.G47.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | retail trade volume (ex motor vehicles), y/y |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/NZL.Q.PRVM.GR.BTE.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/NZL.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | real GDP, y/y |
| YIELD_2Y | oecd | `DF_KEI/NZL.M.IR3TIB.PA._Z._Z._Z` | M | % | 2010-01-01 | 2026-10-01 | proxy: 3M interbank rate, monthly average |
| YIELD_10Y | oecd | `DF_KEI/NZL.M.IRLT.PA._Z._Z._Z` | M | % | 2010-01-01 | 2026-10-01 | long-term (10Y) government bond yield, monthly average |
| TRADE_BALANCE | oecd | `DF_KEI/NZL.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-09-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/NZL.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/NZL.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/NZL.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### CN

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | bis | `WS_CBPOL/D.CN` | D | % | 2010-01-01 | 2026-11-03 | BIS central bank policy rate, daily; published with a lag of up to ~2 weeks |
| CPI_YOY | oecd | `DF_KEI/CHN.M.CP.GR._Z._Z.GY` | M | % | 2010-01-01 | 2026-10-01 | national CPI, all items, y/y |
| CORE_CPI_YOY | — | — | — | — | — | — | **missing** |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | — | — | — | — | — | — | **missing** |
| EMPLOYMENT_CHG | — | — | — | — | — | — | **missing** |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/CHN.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-10-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/CHN.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-09-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | — | — | — | — | — | — | **missing** |
| INDUSTRIAL_PROD_YOY | — | — | — | — | — | — | **missing** |
| GDP_YOY | oecd | `DF_KEI/CHN.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-07-01 | real GDP, y/y |
| YIELD_2Y | oecd | `DF_KEI/CHN.M.IR3TIB.PA._Z._Z._Z` | M | % | 2010-01-01 | 2026-09-01 | proxy: 3M interbank rate, monthly average |
| YIELD_10Y | oecd | `DF_KEI/CHN.M.IRLT.PA._Z._Z._Z` | M | % | 2014-01-01 | 2026-09-01 | long-term (10Y) government bond yield, monthly average |
| TRADE_BALANCE | oecd | `DF_KEI/CHN.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-09-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/CHN.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/CHN.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/CHN.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### CH

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| POLICY_RATE | bis | `WS_CBPOL/D.CH` | D | % | 2010-01-01 | 2026-11-03 | BIS central bank policy rate, daily; published with a lag of up to ~2 weeks |
| CPI_YOY | bis | `WS_LONG_CPI/M.CH.771` | M | % | 2010-01-01 | 2026-09-01 | national CPI, all items, y/y |
| CORE_CPI_YOY | — | — | — | — | — | — | **missing** |
| PCE_YOY | — | — | — | — | — | — | **missing** |
| CORE_PCE_YOY | — | — | — | — | — | — | **missing** |
| UNEMPLOYMENT | oecd | `DF_KEI/CHE.Q.UNEMP.PT_LF._T.Y._Z` | Q | % | 2010-01-01 | 2026-04-01 | labour-force-survey unemployment rate, SA |
| EMPLOYMENT_CHG | — | — | — | — | — | — | **missing** |
| JOBLESS_CLAIMS | — | — | — | — | — | — | **missing** |
| PMI | oecd | `DF_CLI/CHE.M.BCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-09-01 | proxy: OECD business confidence (manufacturing), amplitude-adjusted, neutral=100 |
| CONSUMER_CONF | oecd | `DF_CLI/CHE.M.CCICP.IX._Z.AA.IX._Z.H` | M | index | 2010-01-01 | 2026-08-01 | OECD consumer confidence, amplitude-adjusted, neutral=100 |
| RETAIL_SALES_YOY | oecd | `DF_KEI/CHE.M.TOVM.GR.G47.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | retail trade volume (ex motor vehicles), y/y |
| INDUSTRIAL_PROD_YOY | oecd | `DF_KEI/CHE.M.PRVM.GR.BTE.Y.GY` | M | % | 2011-10-01 | 2026-06-01 | industrial production ex construction, y/y |
| GDP_YOY | oecd | `DF_KEI/CHE.Q.B1GQ_Q.GR._T.Y.GY` | Q | % | 2010-01-01 | 2026-04-01 | real GDP, y/y |
| YIELD_2Y | oecd | `DF_KEI/CHE.M.IR3TIB.PA._Z._Z._Z` | M | % | 2010-01-01 | 2026-10-01 | proxy: 3M interbank rate, monthly average |
| YIELD_10Y | oecd | `DF_KEI/CHE.M.IRLT.PA._Z._Z._Z` | M | % | 2010-01-01 | 2026-10-01 | long-term (10Y) government bond yield, monthly average |
| TRADE_BALANCE | oecd | `DF_KEI/CHE.M.EX-IM.USD._T.Y._Z` | M | USD bn | 2010-01-01 | 2026-09-01 | merchandise exports minus imports, SA |
| EXPORTS_YOY | oecd | `DF_KEI/CHE.M.EX.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise exports, USD value SA, y/y |
| IMPORTS_YOY | oecd | `DF_KEI/CHE.M.IM.GR._T.Y.GY` | M | % | 2010-01-01 | 2026-09-01 | merchandise imports, USD value SA, y/y |
| CURRENT_ACCOUNT_GDP | oecd | `DF_KEI/CHE.Q.CA_GDP.PT_B1GQ._T.Y._Z` | Q | % of GDP | 2010-01-01 | 2026-04-01 | current account balance, % of GDP, SA |

### GLOBAL

| Indicator | Source | Source id | Freq | Unit | First obs | Latest obs | Note |
|---|---|---|---|---|---|---|---|
| VIX | fred | `VIXCLS` | D | index | 2010-01-04 | 2026-11-09 |  |
| SPX | fred | `SP500` | D | index | 2016-11-11 | 2026-11-09 | FRED only carries the last 10 years |
| HY_SPREAD | fred | `BAMLH0A0HYM2` | D | % | 2023-11-13 | 2026-11-09 | ICE BofA US HY OAS; FRED only carries the last 3 years |
| OIL | fred | `DCOILWTICO` | D | USD/bbl | 2010-01-04 | 2026-11-03 | WTI spot, Cushing |
| GOLD | — | — | — | — | — | — | **missing** |
| COPPER | fred | `PCOPPUSDM` | M | USD/tonne | 2010-01-01 | 2026-09-01 | IMF global copper price, monthly average |
| US_10Y_REAL | fred | `DFII10` | D | % | 2010-01-04 | 2026-11-06 | 10Y TIPS constant maturity |

## Adding or changing a series

- FRED: add a `FredSeries(region, indicator, fred_id, transform, frequency, ...)`
  line to `_fred_series.py`. Transforms: `level`, `yoy`, `diff`, plus `scale`.
- OECD KEI: add the region to the `monthly` / `quarterly` tuple of a `KeiRule`,
  or add a rule (`dims` = MEASURE, UNIT_MEASURE, ACTIVITY, ADJUSTMENT, TRANSFORMATION).
- A new provider: write `fetch_<name>(start) -> list[Fetched]` in
  `macro_official.py` and append a `Provider` to `PROVIDERS`.
- Keep one source per series id (`test_no_series_is_written_by_two_sources`).
