# Global rates upstream probe findings — 2026-08-07

Recorded by `tools/probe_rates.py`. Fixtures live in `tests/fixtures/rates/`.
This note is the reference for every later parser task in this plan — read it
instead of re-probing the live endpoints.

Run command: `.venv/Scripts/python.exe tools/probe_rates.py`

## Summary

| Source | Winning URL | Format | Country field | Tenor field | Date field | Value field | Units |
|---|---|---|---|---|---|---|---|
| BIS policy rates | `https://stats.bis.org/api/v1/data/WS_CBPOL/M../all?format=csv` | CSV (SDMX flat) | `REF_AREA` (2-letter code, `XM`=Euro area) | n/a — single overnight/target policy rate per country, no curve | `TIME_PERIOD` (`YYYY-MM`, monthly) | `OBS_VALUE` | **percent** (e.g. `3.625` = 3.625%) |
| ECB yield curve | `https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&lastNObservations=1` | CSV (SDMX flat) | `REF_AREA` = `U2` (Euro area; fixed, only value present) | encoded in `KEY`/`DATA_TYPE_FM` suffix (`SR_10Y` = 10Y spot rate) — one series per tenor, tenor is not a separate column | `TIME_PERIOD` (`YYYY-MM-DD`, daily) | `OBS_VALUE` | **percent** (e.g. `3.146678757`); `UNIT`=`PCPA` |
| US Treasury curve | `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv` (candidate 1, fiscaldata API, 404'd) | CSV, **wide** (one column per tenor) | n/a — US only, implicit | column headers: `1 Mo`, `1.5 Month`, `2 Mo`, `3 Mo`, `4 Mo`, `6 Mo`, `1 Yr`, `2 Yr`, `3 Yr`, `5 Yr`, `7 Yr`, `10 Yr`, `20 Yr`, `30 Yr` | `Date` (`MM/DD/YYYY`) | one cell per tenor column | **percent** (e.g. `4.65` = 4.65%) |
| Japan MOF JGB curve | `https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv` | CSV, **wide** (one column per tenor), with a title row and a footer note | n/a — Japan only, implicit | column headers: `1Y`,`2Y`,`3Y`,`4Y`,`5Y`,`6Y`,`7Y`,`8Y`,`9Y`,`10Y`,`15Y`,`20Y`,`25Y`,`30Y`,`40Y` | `Date` (`YYYY/M/D`, no zero-padding) | one cell per tenor column | **percent**, explicitly stated in file title row: `(Unit : %)` |
| FRED international rates | not probed — `FRED_API_KEY` not set in this environment | — | — | — | — | — | — |

FRED was intentionally skipped per the task plan (Task 4 owns obtaining and probing with a real key); this is not a probe failure.

**No source failed every candidate.** All four non-FRED sources succeeded, though two of them (BIS, UST) only worked on a fallback candidate, not the first-choice URL — see per-source notes below.

## BIS policy rates (`bis_cbpol`)

- Candidate 1 (`.../api/v2/data/dataflow/BIS/WS_CBPOL/1.0/M..?format=csv&lastNObservations=24`) → **HTTP 404**. The v2 dataflow-scoped path with the `M..` key pattern and `lastNObservations` query param does not resolve on the current API.
- Candidate 2 (`.../api/v1/data/WS_CBPOL/M../all?format=csv`) → **HTTP 200**, worked. This is the older v1 API. Note it does *not* honor a `lastNObservations` limit — the URL just says `all`, so it returned the **entire history since 1986** for all 49 countries the BIS carries (12.7 MB, 25,050 rows).
- Candidate 3 (v2 `all` variant) was never tried since candidate 2 already succeeded.

**Fixture note:** the raw 12.7 MB / 25,050-row response was too large to be a sane committed fixture. I trimmed it post-hoc (script, not by URL substitution) to the last 24 monthly observations per country — 1,176 rows, 556 KB — matching what the original (404'd) v2 candidate intended via `lastNObservations=24`. The committed `bis_cbpol.csv` is this trimmed version; the trim preserved the header row and per-row structure exactly, just fewer rows per country.

Columns (verbatim header): `FREQ,REF_AREA,UNIT_MEASURE,UNIT_MULT,TIME_FORMAT,COMPILATION,DECIMALS,SOURCE_REF,SUPP_INFO_BREAKS,TITLE,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_CONF,OBS_PRE_BREAK`

- **Country**: `REF_AREA` — 2-letter code (mostly ISO 3166-1 alpha-2: `US`, `JP`, `GB`, `DE`...) plus `XM` for the Euro area aggregate. **Note the mismatch**: BIS uses `XM` for the euro area; the ECB yield-curve series (below) uses `U2` for the same entity. A country-code join across these two sources needs an explicit mapping, not a direct string match.
- **Tenor**: none. This dataset is a single central-bank policy rate per country per month (overnight/target rate) — there is no term structure here, unlike the yield-curve sources.
- **Date**: `TIME_PERIOD`, monthly, format `YYYY-MM`.
- **Value**: `OBS_VALUE`, plain decimal number. Confirmed **percent** by inspecting magnitudes: US=3.625, GB=3.75, JP=1, XM=2.25, CH=0, and Argentina ranging 40–133 (consistent with real, extreme ARS policy rates in percent, not basis points or decimal fraction).
- `UNIT_MEASURE` is a numeric SDMX code (`368` for every row observed) — did not resolve it against a codelist, but the observed value magnitudes make the unit unambiguous (percent).
- `SOURCE_REF` and `TITLE`/`SUPP_INFO_BREAKS`/`COMPILATION` carry long free-text descriptions (rate definition changes over time, central bank name) — useful for a tooltip/footnote, not needed for parsing the numeric series itself. These fields massively inflate row size (each row repeats the full description text).
- `OBS_STATUS`/`OBS_CONF`/`OBS_PRE_BREAK` are SDMX observation-quality flags; observed values were `A`/`F`/empty respectively across the sample — likely safe to ignore for v1 of the parser.

## ECB yield curve (`ecb_yc_10y`)

Single URL, worked first try. Columns (verbatim header):
`KEY,FREQ,REF_AREA,CURRENCY,PROVIDER_FM,INSTRUMENT_FM,PROVIDER_FM_ID,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_CONF,OBS_PRE_BREAK,OBS_COM,TIME_FORMAT,BREAKS,COLLECTION,COMPILING_ORG,DISS_ORG,DOM_SER_IDS,FM_CONTRACT_TIME,FM_COUPON_RATE,FM_IDENTIFIER,FM_LOT_SIZE,FM_MATURITY,FM_OUTS_AMOUNT,FM_PUT_CALL,FM_STRIKE_PRICE,PUBL_MU,PUBL_PUBLIC,UNIT_INDEX_BASE,COMPILATION,COVERAGE,DECIMALS,SOURCE_AGENCY,SOURCE_PUB,TITLE,TITLE_COMPL,UNIT,UNIT_MULT`

- **Country**: `REF_AREA` = `U2` (fixed — Euro area, changing composition). The requested URL scopes to exactly one series (the 10Y spot rate); it is not a country-parameterized endpoint the way BIS is. To get a full curve you request one URL per tenor (`SR_1Y`, `SR_2Y`, ... appended after `SV_C_YM.` in the key), each returning a single-row response like this one when `lastNObservations=1`.
- **Tenor**: not a separate column. It is embedded as the last dot-segment of `KEY` / equivalently the value of `DATA_TYPE_FM` (`SR_10Y` here = "spot rate, 10-year"). Parser will need to parse this suffix or just track which tenor it requested per-URL.
- **Date**: `TIME_PERIOD`, format `YYYY-MM-DD`, daily.
- **Value**: `OBS_VALUE` = `3.146678757` (high precision, `DECIMALS`=6 nominal but actual string has more digits). Confirmed **percent** — `UNIT` column = `PCPA` (SDMX code, "percent per annum") and magnitude (~3.1) matches known euro-area 10Y yield level.
- `TITLE_COMPL` spells out methodology in English prose ("Euro area..., Government bond, nominal, all issuers whose rating is triple A - Svensson model..."), useful for a source/methodology footnote.
- Response is a single data row plus header — very small (1 KB) since `lastNObservations=1` is honored by this API (unlike BIS v1).

## US Treasury par yield curve (`ust_curve`)

- Candidate 1 (`api.fiscaldata.treasury.gov/.../v2/accounting/od/daily_treasury_yield_curve?sort=-record_date&page[size]=1`) → **HTTP 404**. This fiscaldata.treasury.gov endpoint path does not currently resolve (likely renamed/restructured — this is exactly the kind of drift the task brief warned about).
- Candidate 2 (`home.treasury.gov/.../daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&...`) → **HTTP 200**, worked. This is the classic treasury.gov CSV export and it ignores any notion of "latest only" — it returned **every daily observation for calendar year 2026 to date** (152 rows, most recent 2026-08-07 first, descending by date).
- **File extension caveat**: the probe script's `suffix` for this source is `"raw"` (not `csv`) per the brief's `CANDIDATES` table, so the fixture is named `ust_curve.raw` even though its content is plain CSV. This is intentional per the brief script, not a bug — later tasks should treat `.raw` as CSV for this source.
- **Format**: wide CSV — one row per date, one column per tenor. Verbatim header: `Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"`.
- **Country**: none — single-country source (US), implicit.
- **Tenor**: the column header itself (`"1 Mo"`, `"3 Mo"`, `"10 Yr"`, etc.) — inconsistent label style vs MOF (`1Y` vs `1 Mo`/`10 Yr` — mixed "Mo"/"Yr" units within the same header row, and note `"1.5 Month"` is spelled out fully unlike the rest). A parser needs a tenor-label normalizer that understands both `Mo`/`Month` and `Yr` suffixes with a leading decimal (`1.5 Month`).
- **Date**: `Date`, format `MM/DD/YYYY`, descending (newest first).
- **Value**: one cell per tenor column, plain decimal, **percent** (e.g. `4.65` = 4.65% for 10Yr on 2026-08-07). Empty/missing tenor cells were not observed in this sample but should be defensively handled (Treasury does leave some tenor columns blank on days a given maturity isn't auctioned/quoted).

## Japan MOF JGB curve (`mof_jgb`)

Candidate 1 (English MOF path) worked first try; candidate 2 was never attempted.

- **Format**: wide CSV with a non-tabular title row and a footer note — not a clean rectangular CSV throughout. Row 1: `Interest Rate (August 2026),,,,,,,,,,,,,,,(Unit : %)` — title plus the units marker in the *last* column of that row. Row 2 is the real header: `Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y`. Data rows follow. After the data, there's a blank row then a footer note row: `"  ¦If you cannot download the latest csv data, please clear the browser's cache and download again.",...`. A parser must skip row 1, treat row 2 as header, and stop/ignore once a row's `Date` cell is empty or non-date text.
- **Country**: none — Japan only, implicit.
- **Tenor**: column header (`1Y`, `2Y`, ... `40Y`) — clean, consistent `NY` format, no mixed month/year labels (contrast with UST above).
- **Date**: `Date`, format `YYYY/M/D` (no zero-padding on month or day, e.g. `2026/8/3`).
- **Value**: one cell per tenor column, plain decimal, **percent** — explicitly confirmed by the `(Unit : %)` marker in the title row (e.g. `2.824` = 2.824% for 10Y on 2026/8/3).
- Only 4 rows of data were returned in this probe (most recent business days of the requested month) — this endpoint appears to serve only the current month's data, not a rolling window or full history. Later tasks needing history will need a different MOF endpoint/file (e.g. year-by-year archives) — not investigated here, out of scope for this task.

## FRED international rates (`fred`)

Not probed. `FRED_API_KEY` is not set in this environment and the task brief explicitly directs Task 4 to own obtaining a key and probing FRED separately. `probe_fred()` printed its skip message and returned an empty result, as expected — this is a correct outcome, not a failure of this task.

## Cross-source observations for later parser tasks

1. **Units are percent across the board** for all four probed sources — no basis-point or decimal-fraction encoding was observed anywhere. A single `value_pct -> float` convention should work uniformly once FRED is added (verify FRED separately in Task 4, since some FRED series are indexed differently).
2. **Country coding is inconsistent between sources that share an entity.** BIS uses `XM` for the Euro area; ECB uses `U2` for the same aggregate. Any cross-source join needs an explicit alias table, not raw string equality on country/area codes.
3. **Wide vs. long shape differs by source.** BIS and ECB are long/SDMX-flat (one row per observation, tenor-or-series encoded in the query/key). UST and MOF are wide (one row per date, one column per tenor) — and each has its own quirks (UST mixes `Mo`/`Yr`/`Month` labels including a decimal `1.5 Month`; MOF has a non-tabular title/footer wrapping the real table).
4. **API drift confirmed real** (this was the risk the task brief called out): both BIS's and UST's first-choice candidate URLs 404'd; only the fallback candidates worked. This validates the plan's ordered-candidate-list approach and justifies keeping fallbacks in the shipped parser rather than hardcoding a single URL per source.
5. **BIS `lastNObservations` is not honored by the v1 CSV endpoint** — v1 always returns full history. If the shipped parser wants to limit request size, it must filter client-side (as this task's fixture trim did) or find a working v2 path in a later task.
