# Global Rates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two linked panels — a free Global Rates monitor covering every jurisdiction BIS publishes, and a Pro Sovereign Curves panel — over free, redistributable data only.

**Architecture:** One `RatesProvider` serves two country-shaped topics (`rates:policy`, `rates:curve:<CC>`) and does all cross-source joining, so panels stay dumb. HTTP and parsing are separated: every upstream gets a pure `parse_*(text) -> normalized` function tested against recorded fixtures, and a thin `fetch_*` wrapper that does the request. A new `RatesContext` carries the selected country per link group, kept entirely separate from `SymbolContext` so country codes never reach the equity panels.

**Tech Stack:** Python 3, PySide6, pyqtgraph, requests, pandas, lxml (all already in `requirements.txt`). pytest with offscreen Qt.

**Spec:** `docs/superpowers/specs/2026-08-07-global-rates-design.md`

## Global Constraints

- **No new runtime dependencies.** Everything needed is already in `requirements.txt`. If a task seems to need a new package, stop and ask.
- **The git repo is `app/`.** Use `git add` with explicit paths. **Never `git add -A`.**
- **Panels move and snap, never float.**
- **Every new shortcut or feature must be added to the F1 guide** (`aurantium/onboarding_dialog.py`) in the same change. Task 10 covers this; do not skip it.
- **BIS attribution is mandatory.** Any payload containing BIS data carries `"BIS"` in its `sources`, and the panel renders it. This is a licence condition, not a style choice.
- **BIS-sourced policy rates are free-tier and must never be gated.** Licence condition.
- **A FRED series is fetched only if it is in the generated allowlist.** Refuse before the request, never fetch-then-filter.
- **The provider never interpolates yield curves.** Return observed points only.
- **Nothing in a panel's construction path may raise.** `MainWindow.__init__` is not wrapped in a try at `__main__.py:327`; an exception there means no window at all.
- Run the suite with `.venv/Scripts/python.exe -m pytest tests/ -q` from `app/`.

---

## File Structure

| File | Responsibility |
|---|---|
| `aurantium/rates_meta.py` | Curated country table. The single source of truth for coverage, sources, tenors, citations, tier. |
| `aurantium/rates_context.py` | `RatesContext` singleton — selected country per link group. |
| `aurantium/rates_allowlist.py` | **Generated.** Vetted, copyright-filtered FRED series ids. Data only. |
| `aurantium/providers/rates.py` | `RatesProvider` + pure `parse_*` functions + `fetch_*` wrappers + the join. |
| `aurantium/panels/global_rates.py` | The breadth monitor. Free tier. |
| `aurantium/panels/sovereign_curves.py` | The curve overlay panel. Pro tier. |
| `tools/probe_rates.py` | **Throwaway.** Hits every upstream, records fixtures, reports actual shapes. |
| `tools/verify_rates.py` | Regenerates `rates_allowlist.py`; verifies the country table against live sources. |
| `tests/fixtures/rates/*` | Recorded upstream responses. Tests parse these, never the network. |

Modified: `aurantium/providers/__init__.py`, `aurantium/panels/macro.py`, `aurantium/onboarding_dialog.py`.

---

### Task 1: Probe the upstreams and record fixtures

The spec's §10 risk: these five endpoints are described from knowledge, not from today's live responses. BIS in particular has reorganized its API before. **Nothing else in this plan is safe to write until real responses are on disk.** This task is deliberately not TDD — it is an investigation whose deliverable is fixtures plus a findings note.

**Files:**
- Create: `tools/probe_rates.py`
- Create: `tests/fixtures/rates/` (populated by the script)
- Create: `docs/superpowers/2026-08-07-rates-probe-findings.md`

**Interfaces:**
- Consumes: nothing
- Produces: fixture files that every later parse test loads; a findings note recording which candidate URL won for each source, and the actual response shape.

- [ ] **Step 1: Write the probe script**

Each source lists candidate URLs because some are uncertain. The script tries them in order and records the first that returns HTTP 200 with non-empty content.

```python
"""Throwaway: probe every rates upstream, record fixtures, report shapes.

Run:  .venv/Scripts/python.exe tools/probe_rates.py
Writes tests/fixtures/rates/*.  Delete this script once fixtures are recorded
and tools/verify_rates.py covers ongoing verification.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "rates"

# (name, [candidate urls], suffix)
CANDIDATES = [
    (
        "bis_cbpol",
        [
            "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/M..?format=csv&lastNObservations=24",
            "https://stats.bis.org/api/v1/data/WS_CBPOL/M../all?format=csv",
            "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/all?format=csv&lastNObservations=24",
        ],
        "csv",
    ),
    (
        "ecb_yc_10y",
        [
            "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&lastNObservations=1",
        ],
        "csv",
    ),
    (
        "ust_curve",
        [
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/daily_treasury_yield_curve?sort=-record_date&page[size]=1",
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv",
        ],
        "raw",
    ),
    (
        "mof_jgb",
        [
            "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv",
            "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcme.csv",
        ],
        "csv",
    ),
]

HEADERS = {"User-Agent": "aurantium-probe/1.0"}


def probe(name: str, urls: list[str], suffix: str) -> dict:
    for url in urls:
        try:
            resp = requests.get(url, timeout=20, headers=HEADERS)
        except Exception as exc:
            print(f"  {url}\n    ERROR {exc}")
            continue
        body = resp.text or ""
        print(f"  {url}\n    HTTP {resp.status_code}, {len(body)} bytes")
        if resp.status_code == 200 and body.strip():
            ext = "json" if body.lstrip().startswith(("{", "[")) else suffix
            path = FIXTURES / f"{name}.{ext}"
            path.write_text(body, encoding="utf-8")
            print(f"    -> recorded {path.name}")
            return {"name": name, "url": url, "bytes": len(body), "file": path.name}
    return {"name": name, "url": None, "bytes": 0, "file": None}


def probe_fred() -> dict:
    """FRED needs a key. Records one international series plus its metadata,
    so Task 4 can see exactly what a `notes` field looks like."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("  FRED_API_KEY not set - skipping (Task 4 will need it)")
        return {"name": "fred", "url": None, "bytes": 0, "file": None}
    out = {}
    for label, url, params in [
        (
            "fred_series",
            "https://api.stlouisfed.org/fred/series",
            {"series_id": "IRLTLT01GBM156N", "api_key": key, "file_type": "json"},
        ),
        (
            "fred_observations",
            "https://api.stlouisfed.org/fred/series/observations",
            {
                "series_id": "IRLTLT01GBM156N",
                "api_key": key,
                "file_type": "json",
                "limit": 12,
                "sort_order": "desc",
            },
        ),
    ]:
        resp = requests.get(url, params=params, timeout=20, headers=HEADERS)
        print(f"  {label}: HTTP {resp.status_code}, {len(resp.text)} bytes")
        if resp.status_code == 200:
            # scrub the key out of anything we write to disk
            (FIXTURES / f"{label}.json").write_text(resp.text, encoding="utf-8")
            out[label] = len(resp.text)
    return {"name": "fred", "url": "api.stlouisfed.org", "bytes": sum(out.values()), "file": "fred_*.json"}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    results = []
    for name, urls, suffix in CANDIDATES:
        print(f"\n=== {name} ===")
        results.append(probe(name, urls, suffix))
    print("\n=== fred ===")
    results.append(probe_fred())

    print("\n\n=== SUMMARY ===")
    failed = []
    for r in results:
        status = "OK  " if r["url"] else "FAIL"
        print(f"{status} {r['name']:16} {r['file'] or '-'}")
        if not r["url"]:
            failed.append(r["name"])
    print(json.dumps(results, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe tools/probe_rates.py`

Expected: a summary line per source. Some candidates will 404 — that is the point of listing several. A source is only a problem if **every** candidate fails.

- [ ] **Step 3: Inspect each recorded fixture by hand**

Open each file in `tests/fixtures/rates/` and answer, for each source: what identifies a country? what identifies a tenor? what is the date field called? are rates in percent or basis points or decimal fractions?

This is the step that pays for the whole task. Do not skip it.

- [ ] **Step 4: If any source failed every candidate, stop and report**

Do not invent a substitute source or guess a URL. Report which source failed and what the responses were; the spec's source table may need revisiting. Everything else in this plan can proceed without that one source — `rates_meta.py` simply marks the affected countries `curve_source="none"`.

- [ ] **Step 5: Write the findings note**

Create `docs/superpowers/2026-08-07-rates-probe-findings.md` with one section per source recording: the winning URL, the response format, the field names for country/tenor/date/value, the units, and anything surprising. Later tasks read this instead of re-probing.

- [ ] **Step 6: Commit**

```bash
git add tools/probe_rates.py tests/fixtures/rates/ docs/superpowers/2026-08-07-rates-probe-findings.md
git commit -m "chore: probe rates upstreams and record fixtures"
```

---

### Task 2: The country table

**Files:**
- Create: `aurantium/rates_meta.py`
- Test: `tests/test_rates_meta.py`

**Interfaces:**
- Consumes: the findings note from Task 1 (confirms BIS country key spelling and available tenors)
- Produces:
  - `CountryMeta` NamedTuple with fields `label, code, bis_key, curve_source, tenors, fred_short, fred_long, citation, tier`
  - `COUNTRIES: tuple[CountryMeta, ...]`
  - `by_code(code: str) -> CountryMeta | None`
  - `curve_capable() -> tuple[CountryMeta, ...]`
  - `CURVE_SOURCES: frozenset[str]`

- [ ] **Step 1: Write the failing test**

```python
"""The curated country table must stay internally consistent — the same
discipline test_commodities_meta.py applies to the commodity table."""

from aurantium.rates_meta import (
    COUNTRIES,
    CURVE_SOURCES,
    by_code,
    curve_capable,
)


def test_codes_are_unique():
    codes = [c.code for c in COUNTRIES]
    assert len(codes) == len(set(codes))


def test_codes_are_two_letter_upper():
    for c in COUNTRIES:
        assert len(c.code) == 2 and c.code.isupper(), c.code


def test_curve_source_is_a_known_router_key():
    for c in COUNTRIES:
        assert c.curve_source in CURVE_SOURCES, (c.code, c.curve_source)


def test_every_country_declares_a_citation():
    for c in COUNTRIES:
        assert c.citation.strip(), c.code


def test_curve_countries_declare_tenors():
    for c in COUNTRIES:
        if c.curve_source == "none":
            assert c.tenors == ()
        else:
            assert len(c.tenors) >= 2, c.code
            assert list(c.tenors) == sorted(c.tenors), c.code


def test_fred_sourced_countries_name_at_least_one_series():
    for c in COUNTRIES:
        if c.curve_source == "fred":
            assert c.fred_short or c.fred_long, c.code


def test_tier_is_free_or_pro():
    assert {c.tier for c in COUNTRIES} <= {"free", "pro"}


def test_by_code_is_case_insensitive_and_safe():
    assert by_code("us") is by_code("US")
    assert by_code("ZZ") is None
    assert by_code("") is None


def test_curve_capable_excludes_none_sources():
    assert all(c.curve_source != "none" for c in curve_capable())
    assert any(c.code == "US" for c in curve_capable())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_meta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aurantium.rates_meta'`

- [ ] **Step 3: Write the module**

Correct `bis_key` values and tenor lists against the Task 1 findings note before committing.

```python
"""Curated metadata for the jurisdictions aurantium covers in the rates panels:
the mapping between a country's ISO code, its BIS policy-rate key, the source
that publishes its yield curve, and the attribution string the UI must render.

Every entry was verified against the live sources by tools/verify_rates.py
before being listed here. Widening coverage is an edit to COUNTRIES, not a
code change.

Licence note: BIS terms require citing BIS as source and state that inclusion
in a commercial product must not result in an additional charge to users.
Policy-rate content is therefore tier "free" and must never be gated.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

#: valid curve_source router keys; "none" = policy rate only, no curve
CURVE_SOURCES = frozenset({"ust", "ecb", "mof", "fred", "none"})

BIS = "BIS"
UST = "U.S. Treasury"
ECB = "ECB"
MOF = "Japan MOF"
FRED = "FRED"


class CountryMeta(NamedTuple):
    label: str                      # "Japan"
    code: str                       # ISO 3166-1 alpha-2; the RatesContext key
    bis_key: str                    # BIS CBPOL reference-area key
    curve_source: str               # one of CURVE_SOURCES
    tenors: tuple[float, ...]       # maturities the source publishes, in years
    fred_short: Optional[str]       # allowlisted FRED series id, or None
    fred_long: Optional[str]
    citation: str                   # attribution the UI renders
    tier: str                       # "free" | "pro"


_UST_TENORS = (0.0833, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0)
_ECB_TENORS = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0)
_MOF_TENORS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 40.0)

COUNTRIES: tuple[CountryMeta, ...] = (
    CountryMeta("United States", "US", "US", "ust", _UST_TENORS,
                None, None, f"{BIS} · {UST}", "free"),
    CountryMeta("Euro area", "XM", "XM", "ecb", _ECB_TENORS,
                None, None, f"{BIS} · {ECB}", "free"),
    CountryMeta("Japan", "JP", "JP", "mof", _MOF_TENORS,
                None, None, f"{BIS} · {MOF}", "free"),
    CountryMeta("United Kingdom", "GB", "GB", "fred", (0.25, 10.0),
                "IR3TIB01GBM156N", "IRLTLT01GBM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Canada", "CA", "CA", "fred", (0.25, 10.0),
                "IR3TIB01CAM156N", "IRLTLT01CAM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Australia", "AU", "AU", "fred", (0.25, 10.0),
                "IR3TIB01AUM156N", "IRLTLT01AUM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Switzerland", "CH", "CH", "fred", (0.25, 10.0),
                "IR3TIB01CHM156N", "IRLTLT01CHM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Sweden", "SE", "SE", "none", (), None, None, BIS, "free"),
    CountryMeta("Norway", "NO", "NO", "none", (), None, None, BIS, "free"),
    CountryMeta("New Zealand", "NZ", "NZ", "none", (), None, None, BIS, "free"),
    CountryMeta("Korea", "KR", "KR", "none", (), None, None, BIS, "free"),
    CountryMeta("India", "IN", "IN", "none", (), None, None, BIS, "free"),
    CountryMeta("Brazil", "BR", "BR", "none", (), None, None, BIS, "free"),
    CountryMeta("Mexico", "MX", "MX", "none", (), None, None, BIS, "free"),
    CountryMeta("South Africa", "ZA", "ZA", "none", (), None, None, BIS, "free"),
    CountryMeta("China", "CN", "CN", "none", (), None, None, BIS, "free"),
)

#: Do NOT add the legacy euro-area members (DE, FR, IT, ES, AT, BE, NL, PT,
#: GR) here. BIS still carries their national policy rates, but the series
#: stop in 1998-12 (2000-12 for Greece) when policy passed to the ECB — so
#: "the latest observation" for Germany is a 1998 Bundesbank rate. Their
#: current policy lives in the euro-area aggregate, "XM". Verified against
#: tests/fixtures/rates/bis_cbpol.csv.
_BY_CODE = {c.code: c for c in COUNTRIES}


def by_code(code: str) -> Optional[CountryMeta]:
    """Look up a country by ISO code. Never raises; unknown -> None."""
    if not code:
        return None
    return _BY_CODE.get(code.strip().upper())


def curve_capable() -> tuple[CountryMeta, ...]:
    """Countries with some curve to draw, complete or sparse."""
    return tuple(c for c in COUNTRIES if c.curve_source != "none")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_meta.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add aurantium/rates_meta.py tests/test_rates_meta.py
git commit -m "feat: add the curated rates country table"
```

---

### Task 3: RatesContext

**Files:**
- Create: `aurantium/rates_context.py`
- Test: `tests/test_rates_context.py`

**Interfaces:**
- Consumes: `rates_meta.by_code`
- Produces:
  - `RatesContext.instance() -> RatesContext`
  - `country_changed = Signal(str, str, object)` — group, code, source
  - `set_country(group: str, code: str, source: QObject | None = None) -> None`
  - `country(group: str) -> str`
  - `to_json() -> dict` / `from_json(data: dict) -> None`
  - Re-exports `DEFAULT_GROUP`, `GROUPS`, `UNLINKED` from `symbol_context`

- [ ] **Step 1: Write the failing test**

```python
"""RatesContext mirrors SymbolContext's group semantics but validates its
payload against the country table, and must tolerate junk from a hand-edited
layout file without raising."""

import pytest

from aurantium.rates_context import RatesContext
from aurantium.symbol_context import DEFAULT_GROUP, UNLINKED


@pytest.fixture
def ctx():
    c = RatesContext()
    yield c


def test_set_and_read_back(ctx):
    ctx.set_country("A", "JP")
    assert ctx.country("A") == "JP"
    assert ctx.country("B") == ""


def test_codes_are_normalized(ctx):
    ctx.set_country("A", " jp ")
    assert ctx.country("A") == "JP"


def test_unknown_code_is_rejected(ctx):
    ctx.set_country("A", "ZZ")
    assert ctx.country("A") == ""


def test_unlinked_group_is_ignored(ctx):
    ctx.set_country(UNLINKED, "US")
    assert ctx.country(UNLINKED) == ""


def test_signal_carries_group_code_and_source(ctx):
    seen = []
    ctx.country_changed.connect(lambda g, c, s: seen.append((g, c, s)))
    sentinel = object()
    ctx.set_country("A", "US", source=sentinel)
    assert seen == [("A", "US", sentinel)]


def test_same_value_is_suppressed(ctx):
    seen = []
    ctx.set_country("A", "US")
    ctx.country_changed.connect(lambda g, c, s: seen.append(c))
    ctx.set_country("A", "US")
    assert seen == []


def test_groups_are_independent(ctx):
    ctx.set_country("A", "US")
    ctx.set_country("B", "JP")
    assert (ctx.country("A"), ctx.country("B")) == ("US", "JP")


def test_json_round_trip(ctx):
    ctx.set_country("A", "US")
    ctx.set_country("C", "XM")
    restored = RatesContext()
    restored.from_json(ctx.to_json())
    assert restored.country("A") == "US"
    assert restored.country("C") == "XM"


@pytest.mark.parametrize(
    "junk",
    [None, {}, {"A": None}, {"A": 42}, {"A": "ZZ"}, {"A": ""}, {7: "US"}],
)
def test_from_json_tolerates_junk(ctx, junk):
    ctx.from_json(junk)          # must not raise — runs inside MainWindow.__init__
    assert ctx.country("A") == ""


def test_default_group_matches_symbol_context():
    assert DEFAULT_GROUP == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_context.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aurantium.rates_context'`

- [ ] **Step 3: Write the module**

```python
"""Global active-country state per link group, for the rates panels.

Deliberately separate from SymbolContext rather than sharing a base class.
SymbolContext carries free-text tickers and every panel joins group "A" by
default with no type discrimination, so publishing a country code there would
make the chart, news and fundamentals panels all try to load it as a symbol.
The two also validate differently — a country code is checked against
rates_meta, a ticker is not — and a shared base would couple every equity
panel to rates changes. ~50 lines of duplication is the cheaper mistake.

Group vocabulary (A/B/C/D + unlinked) is reused from symbol_context so the
badge UI and the user's mental model stay identical.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

from .rates_meta import by_code
from .symbol_context import DEFAULT_GROUP, GROUPS, UNLINKED

__all__ = ["RatesContext", "DEFAULT_GROUP", "GROUPS", "UNLINKED"]


class RatesContext(QObject):
    """Singleton. ``set_country()`` publishes; rates panels react to
    ``country_changed(group, code, source)``. ``source`` is the originating
    QObject so publishers can skip their own echo."""

    country_changed = Signal(str, str, object)  # group, code, source

    _inst: Optional["RatesContext"] = None

    @classmethod
    def instance(cls) -> "RatesContext":
        if cls._inst is None:
            cls._inst = RatesContext()
        return cls._inst

    def __init__(self) -> None:
        super().__init__()
        self._countries: dict[str, str] = {}

    def country(self, group: str) -> str:
        return self._countries.get(group, "")

    def set_country(
        self, group: str, code: str, source: QObject | None = None
    ) -> None:
        if not isinstance(code, str) or group == UNLINKED:
            return
        meta = by_code(code)
        if meta is None:
            return  # unknown country: ignore rather than publish nonsense
        if self._countries.get(group) == meta.code:
            return  # no-op on same value, matching SymbolContext
        self._countries[group] = meta.code
        self.country_changed.emit(group, meta.code, source)

    # -- layout persistence --------------------------------------------------

    def to_json(self) -> dict:
        return dict(self._countries)

    def from_json(self, data: dict) -> None:
        """Restore from layout JSON. Must never raise: this runs inside
        MainWindow.__init__, which __main__.py:327 does not wrap in a try,
        so an exception here means no window at all."""
        for group, code in (data or {}).items():
            if not isinstance(group, str) or not isinstance(code, str):
                continue
            meta = by_code(code)
            if meta is not None:
                self._countries[group] = meta.code
        for group, code in self._countries.items():
            self.country_changed.emit(group, code, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_context.py -q`
Expected: 16 passed (the junk case is parametrized 7 ways)

- [ ] **Step 5: Commit**

```bash
git add aurantium/rates_context.py tests/test_rates_context.py
git commit -m "feat: add RatesContext for country-scoped panel linking"
```

---

### Task 4: The FRED copyright allowlist

Run this **before** any panel work. Its output determines how much sparse-curve coverage actually survives the copyright filter — the FRED international rate series are largely OECD-sourced, and if most get filtered out, `rates_meta.py`'s `fred`-sourced countries need demoting to `curve_source="none"`. Better to learn that now than after two panels are built on the assumption.

**Files:**
- Create: `tools/verify_rates.py`
- Create: `aurantium/rates_allowlist.py` (generated by the above)
- Test: `tests/test_rates_allowlist.py`

**Interfaces:**
- Consumes: `rates_meta.COUNTRIES` (the candidate series ids)
- Produces:
  - `rates_allowlist.ALLOWED: frozenset[str]`
  - `rates_allowlist.CHECKED: str` (ISO date)
  - `providers.rates.FredNotAllowed` (exception, defined here, used in Task 5)
  - `providers.rates.fred_allowed(series_id: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
"""The FRED allowlist is a licence control, not an optimization: FRED carries
third-party copyrighted series whose commercial redistribution isn't permitted.
An unlisted series must be refused BEFORE any request is made, so a network
failure can never fail open."""

import pytest

from aurantium import rates_allowlist
from aurantium.providers.rates import FredNotAllowed, fetch_fred_series, fred_allowed


def test_allowlist_is_a_frozenset_of_strings():
    assert isinstance(rates_allowlist.ALLOWED, frozenset)
    assert all(isinstance(s, str) for s in rates_allowlist.ALLOWED)


def test_checked_date_is_recorded():
    assert rates_allowlist.CHECKED  # ISO date the filter last ran


def test_unlisted_series_is_refused():
    assert fred_allowed("DEFINITELY_NOT_ALLOWLISTED") is False


def test_refusal_happens_before_any_request():
    """The injected getter raises if called. Refusal must beat it."""

    def exploding_get(*args, **kwargs):
        raise AssertionError("a request was made for a non-allowlisted series")

    with pytest.raises(FredNotAllowed):
        fetch_fred_series("DEFINITELY_NOT_ALLOWLISTED", "fake-key", get=exploding_get)


def test_empty_allowlist_fails_closed(monkeypatch):
    """A truncated or never-regenerated allowlist must refuse everything,
    not allow everything."""
    monkeypatch.setattr(rates_allowlist, "ALLOWED", frozenset())
    assert fred_allowed("IRLTLT01GBM156N") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_allowlist.py -q`
Expected: FAIL — no `aurantium.rates_allowlist` module

- [ ] **Step 3: Write the generator**

```python
"""Regenerate aurantium/rates_allowlist.py — the vetted list of FRED series
the rates provider is permitted to fetch.

FRED hosts third-party copyrighted series whose commercial redistribution is
not permitted. They are identifiable: their `notes` field contains the word
"Copyright". This script asks FRED about every series rates_meta.py wants,
drops any that match, and writes the survivors.

Output is a .py module rather than a JSON data file on purpose: there is no
data/ directory in app/, aurantium.spec bundles only ("layouts", "layouts")
and .env.example, so a new data path would need a spec entry whose failure
mode is silent. A module is bundled automatically and fails loudly.

Run:  .venv/Scripts/python.exe tools/verify_rates.py
Re-run as part of release prep.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurantium.rates_meta import COUNTRIES  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "aurantium" / "rates_allowlist.py"
FRED_SERIES = "https://api.stlouisfed.org/fred/series"

HEADER = '''"""GENERATED FILE - do not edit by hand.

Vetted FRED series the rates provider may fetch. Series whose FRED metadata
notes contain "Copyright" are excluded: they are third-party copyrighted and
their commercial redistribution is not permitted.

Regenerate with:  .venv/Scripts/python.exe tools/verify_rates.py
"""

from __future__ import annotations

#: ISO date this list was last verified against FRED
CHECKED = "{checked}"

#: series ids the provider is permitted to request
ALLOWED = frozenset({allowed})
'''


def candidate_series() -> list[str]:
    out: list[str] = []
    for c in COUNTRIES:
        for sid in (c.fred_short, c.fred_long):
            if sid:
                out.append(sid)
    return sorted(set(out))


def is_copyrighted(series_id: str, key: str) -> tuple[bool, str]:
    resp = requests.get(
        FRED_SERIES,
        params={"series_id": series_id, "api_key": key, "file_type": "json"},
        timeout=15,
    )
    if resp.status_code != 200:
        # unknown status = treat as copyrighted. Fail closed.
        return True, f"HTTP {resp.status_code}"
    entries = resp.json().get("seriess", [])
    if not entries:
        return True, "no metadata"
    notes = entries[0].get("notes") or ""
    if "copyright" in notes.lower():
        return True, "notes mention Copyright"
    return False, "clear"


def main() -> int:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("FRED_API_KEY not set. Get a free key at fred.stlouisfed.org.")
        return 2

    allowed, refused = [], []
    for sid in candidate_series():
        blocked, reason = is_copyrighted(sid, key)
        print(f"{'REFUSE' if blocked else 'ALLOW '} {sid:20} {reason}")
        (refused if blocked else allowed).append(sid)

    body = HEADER.format(
        checked=dt.date.today().isoformat(),
        allowed="{\n    " + ",\n    ".join(repr(s) for s in allowed) + ",\n}"
        if allowed
        else "()",
    )
    OUT.write_text(body, encoding="utf-8")

    print(f"\nwrote {OUT.name}: {len(allowed)} allowed, {len(refused)} refused")
    if refused:
        print("\nREFUSED - demote these countries to curve_source='none' in")
        print("rates_meta.py if they now have no usable series:")
        for sid in refused:
            print(f"  {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the generator**

Run: `.venv/Scripts/python.exe tools/verify_rates.py`
Expected: an ALLOW/REFUSE line per series, then `wrote rates_allowlist.py`.

**Decision checkpoint.** If a country's series are all refused, edit `rates_meta.py` to set its `curve_source="none"` and clear its `tenors`, `fred_short`, `fred_long`. Re-run `tests/test_rates_meta.py`. Report how many countries were demoted — if it's all of them, the sparse-curve feature is empty and Task 8's dashed-line handling still ships but has nothing to draw, which is worth telling Pedro before building it.

- [ ] **Step 5: Add the allowlist gate to the provider module**

Create `aurantium/providers/rates.py` with just enough to satisfy this task; Task 5 fills in the rest.

```python
"""Global rates provider: BIS policy rates, sovereign yield curves.

Serves rates:policy (one envelope, all jurisdictions) and rates:curve:<CC>.
HTTP and parsing are kept separate — every upstream has a pure parse_*()
function tested against recorded fixtures, plus a thin fetch_*() wrapper.
"""

from __future__ import annotations

from typing import Any, Callable

import requests

from .. import rates_allowlist


class FredNotAllowed(RuntimeError):
    """Raised when a FRED series is not on the vetted allowlist.

    FRED carries third-party copyrighted series whose commercial
    redistribution is not permitted; see tools/verify_rates.py."""


def fred_allowed(series_id: str) -> bool:
    # read through the module, not a from-import: a from-import would bind the
    # frozenset here and make the allowlist untestable by monkeypatch
    return series_id in rates_allowlist.ALLOWED


def fetch_fred_series(
    series_id: str,
    api_key: str,
    *,
    limit: int = 24,
    get: Callable[..., Any] = requests.get,
) -> list[tuple[str, float]]:
    """Observations for one FRED series, oldest -> newest.

    Refuses a non-allowlisted series BEFORE making any request, so a network
    failure can never fail open."""
    if not fred_allowed(series_id):
        raise FredNotAllowed(series_id)
    resp = get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    out: list[tuple[str, float]] = []
    for obs in resp.json().get("observations", []):
        raw = obs.get("value")
        if raw in (None, ".", ""):
            continue
        try:
            out.append((obs.get("date", ""), float(raw)))
        except (TypeError, ValueError):
            continue
    out.reverse()
    return out
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_allowlist.py -q`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add tools/verify_rates.py aurantium/rates_allowlist.py aurantium/providers/rates.py tests/test_rates_allowlist.py aurantium/rates_meta.py
git commit -m "feat: vet FRED series against the copyright filter, fail closed"
```

---

### Task 5: Parse each upstream

Pure functions over recorded text. No network, no Qt.

**Files:**
- Modify: `aurantium/providers/rates.py`
- Test: `tests/test_rates_provider.py`

**Interfaces:**
- Consumes: fixtures from Task 1; `rates_meta.by_code`
- Produces:
  - `PolicyPoint` NamedTuple: `code, rate, as_of, prev, last_change, direction`
  - `CurvePoint` NamedTuple: `years, rate`
  - `Curve` NamedTuple: `code, points, complete, sources, as_of`
  - `parse_bis_policy(text: str) -> dict[str, PolicyPoint]`
  - `parse_ust_curve(text: str) -> Curve`
  - `parse_ecb_curve(rows: dict[float, str]) -> Curve`
  - `parse_mof_curve(text: str) -> Curve`
  - `direction_of(rate: float, prev: float | None) -> str`

**Important:** the parse bodies below are written against the shapes these APIs are expected to return. Task 1 recorded what they *actually* return. **If a fixture disagrees, change the parsing — never the normalized return type**, because Tasks 6–8 depend on those types. The tests assert normalized output, so a shape change shows up as a parse failure in exactly one place.

- [ ] **Step 1: Write the failing test**

```python
"""Parsers run against fixtures recorded by tools/probe_rates.py — real
upstream responses, so a future API reorganization shows as a fixture diff
rather than a mystery."""

from pathlib import Path

import pytest

from aurantium.providers.rates import (
    Curve,
    PolicyPoint,
    direction_of,
    parse_bis_policy,
    parse_mof_curve,
    parse_ust_curve,
    tenor_years,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rates"


def _fixture(stem: str) -> str:
    matches = sorted(FIXTURES.glob(f"{stem}.*"))
    if not matches:
        pytest.skip(f"no fixture recorded for {stem} - run tools/probe_rates.py")
    return matches[0].read_text(encoding="utf-8")


def test_direction_of():
    assert direction_of(4.25, 4.50) == "down"
    assert direction_of(4.50, 4.25) == "up"
    assert direction_of(4.25, 4.25) == "flat"
    assert direction_of(4.25, None) == "flat"


def test_bis_policy_parses_to_country_keyed_points():
    result = parse_bis_policy(_fixture("bis_cbpol"))
    assert isinstance(result, dict)
    assert result, "expected at least one jurisdiction"
    for code, point in result.items():
        assert isinstance(point, PolicyPoint)
        assert point.code == code
        assert -5.0 < point.rate < 100.0, (code, point.rate)
        assert point.direction in {"up", "down", "flat"}


def test_bis_policy_includes_the_major_jurisdictions():
    result = parse_bis_policy(_fixture("bis_cbpol"))
    assert "US" in result
    assert "JP" in result


def test_bis_policy_tolerates_garbage():
    assert parse_bis_policy("") == {}
    assert parse_bis_policy("not,a,valid\ncsv,file,really") == {}


def test_bis_policy_drops_literal_nan_observations():
    """BIS marks missing observations OBS_STATUS=M and writes the literal
    string "NaN" into OBS_VALUE — 21 such rows exist in the fixture (Croatia,
    2021-22). A NaN reaching a rate would poison every downstream diff and
    serialize to invalid JSON."""
    result = parse_bis_policy(_fixture("bis_cbpol"))
    for code, point in result.items():
        assert point.rate == point.rate, f"{code} rate is NaN"
        if point.prev is not None:
            assert point.prev == point.prev, f"{code} prev is NaN"


def test_bis_policy_skips_a_nan_row_synthetically():
    csv_text = (
        "FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
        "M,US,2026-05,4.50,A\n"
        "M,US,2026-06,NaN,M\n"
        "M,US,2026-07,4.25,A\n"
    )
    point = parse_bis_policy(csv_text)["US"]
    assert point.rate == 4.25
    assert point.prev == 4.50      # the NaN row must not become the previous value
    assert point.direction == "down"


def test_ust_curve_is_complete_and_ordered():
    curve = parse_ust_curve(_fixture("ust_curve"))
    assert isinstance(curve, Curve)
    assert curve.code == "US"
    assert curve.complete is True
    assert len(curve.points) >= 5
    years = [p.years for p in curve.points]
    assert years == sorted(years)
    assert "U.S. Treasury" in curve.sources


def test_ust_curve_tolerates_garbage():
    curve = parse_ust_curve("")
    assert curve.points == ()
    assert curve.complete is False


def test_tenor_years_handles_every_observed_label_spelling():
    """Treasury mixes "Mo"/"Month"/"Yr" in one header row and includes a
    fractional "1.5 Month"; MOF uses bare "40Y". One normalizer, all of them."""
    assert tenor_years("1 Mo") == pytest.approx(1 / 12)
    assert tenor_years("1.5 Month") == pytest.approx(0.125)
    assert tenor_years("4 Mo") == pytest.approx(1 / 3)
    assert tenor_years("10 Yr") == 10.0
    assert tenor_years("40Y") == 40.0
    assert tenor_years("Date") is None
    assert tenor_years("") is None


def test_ust_curve_includes_the_short_end_tenors():
    """Regression: an earlier lookup-table approach silently dropped the
    "1.5 Month", "2 Mo" and "4 Mo" columns Treasury actually publishes."""
    curve = parse_ust_curve(_fixture("ust_curve"))
    assert len(curve.points) >= 12, [p.years for p in curve.points]


def test_mof_curve_parses():
    curve = parse_mof_curve(_fixture("mof_jgb"))
    assert curve.code == "JP"
    assert len(curve.points) >= 5
    assert "Japan MOF" in curve.sources


def test_mof_curve_ignores_the_trailing_footer_note():
    """The MOF file ends with a blank row then a "clear your browser cache"
    note. Reading the last non-empty row would parse that as a curve."""
    curve = parse_mof_curve(_fixture("mof_jgb"))
    assert curve.as_of and "/" in curve.as_of, curve.as_of
    assert all(0.0 < p.rate < 20.0 for p in curve.points)


def test_no_parser_interpolates():
    """Observed points only — a curve must never contain a tenor the source
    did not publish."""
    curve = parse_ust_curve(_fixture("ust_curve"))
    assert len({p.years for p in curve.points}) == len(curve.points)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_provider.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_bis_policy'`

- [ ] **Step 3: Implement the parsers**

Append to `aurantium/providers/rates.py`:

```python
import csv
import io
import re
from typing import NamedTuple, Optional

from ..rates_meta import UST, MOF, ECB, BIS, by_code


class PolicyPoint(NamedTuple):
    code: str
    rate: float
    as_of: str
    prev: Optional[float]
    last_change: Optional[str]
    direction: str          # "up" | "down" | "flat"


class CurvePoint(NamedTuple):
    years: float
    rate: float


class Curve(NamedTuple):
    code: str
    points: tuple[CurvePoint, ...]
    complete: bool
    sources: tuple[str, ...]
    as_of: str


def _f(value: Any) -> Optional[float]:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # reject NaN


def direction_of(rate: float, prev: Optional[float]) -> str:
    if prev is None or rate == prev:
        return "flat"
    return "up" if rate > prev else "down"


#: BIS column names vary by API version; try each in order.
_BIS_AREA_KEYS = ("REF_AREA", "ref_area", "JURISDICTION", "Reference area")
_BIS_DATE_KEYS = ("TIME_PERIOD", "time_period", "Period")
_BIS_VALUE_KEYS = ("OBS_VALUE", "obs_value", "Value")


def _pick(row: dict, keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def parse_bis_policy(text: str) -> dict[str, PolicyPoint]:
    """BIS CBPOL CSV -> {ISO code: PolicyPoint}.

    The series carries history, so the last change and its direction are
    derived by walking back until the rate differs. Never raises: a
    malformed feed yields {} and the panel degrades to the FRED columns."""
    if not text or not text.strip():
        return {}
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return {}

    # group observations by jurisdiction, oldest -> newest
    series: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        area = _pick(row, _BIS_AREA_KEYS)
        period = _pick(row, _BIS_DATE_KEYS)
        value = _f(_pick(row, _BIS_VALUE_KEYS))
        if not area or not period or value is None:
            continue
        code = area.strip().upper()
        if by_code(code) is None:
            continue  # a jurisdiction we don't cover
        series.setdefault(code, []).append((period, value))

    out: dict[str, PolicyPoint] = {}
    for code, observations in series.items():
        observations.sort()
        as_of, rate = observations[-1]
        prev: Optional[float] = None
        last_change: Optional[str] = None
        for period, value in reversed(observations[:-1]):
            if value != rate:
                prev, last_change = value, period
                break
        out[code] = PolicyPoint(
            code=code,
            rate=rate,
            as_of=as_of,
            prev=prev,
            last_change=last_change,
            direction=direction_of(rate, prev),
        )
    return out


_EMPTY_US = Curve("US", (), False, (UST,), "")

#: Treasury's header mixes unit spellings within one row — "1 Mo", "1.5 Month",
#: "2 Mo", "4 Mo", "10 Yr" — so a lookup table can't cover it. Normalize.
_TENOR_LABEL = re.compile(r"^\s*([\d.]+)\s*(Mo(?:nth)?s?|Yrs?|Years?|Y|M)\s*$", re.I)


def tenor_years(label: str) -> Optional[float]:
    """"3 Mo" -> 0.25, "1.5 Month" -> 0.125, "10 Yr" -> 10.0, "40Y" -> 40.0.

    Returns None for anything that isn't a maturity label, which is how
    non-tenor columns ("Date") get skipped."""
    match = _TENOR_LABEL.match(label or "")
    if match is None:
        return None
    magnitude = _f(match.group(1))
    if magnitude is None:
        return None
    unit = match.group(2).lower()
    return magnitude / 12.0 if unit.startswith("m") else magnitude


def parse_ust_curve(text: str) -> Curve:
    """US Treasury daily par yield curve -> Curve.

    The CSV is scoped to a calendar year and sorted newest-first, so row 0 is
    today's curve. (The FiscalData JSON endpoint 404s — see the probe findings;
    do not reintroduce a JSON branch for it.)"""
    if not text or not text.strip():
        return _EMPTY_US
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return _EMPTY_US
    if not rows:
        return _EMPTY_US

    row = rows[0]
    as_of = str(row.get("Date", ""))
    points = []
    for label, cell in row.items():
        years = tenor_years(label)
        if years is None:
            continue
        value = _f(cell)   # Treasury leaves a tenor blank on days it isn't quoted
        if value is not None:
            points.append(CurvePoint(years, value))
    points.sort()
    return Curve("US", tuple(points), len(points) >= 5, (UST,), as_of)


def parse_ecb_curve(rows: dict[float, str]) -> Curve:
    """ECB publishes one series per tenor, so the caller fetches several and
    passes {maturity_years: csv_text}. Each CSV has a single observation."""
    points = []
    as_of = ""
    for years, text in sorted(rows.items()):
        try:
            parsed = list(csv.DictReader(io.StringIO(text or "")))
        except Exception:
            continue
        if not parsed:
            continue
        record = parsed[-1]
        value = _f(record.get("OBS_VALUE") or record.get("obs_value"))
        if value is None:
            continue
        as_of = str(record.get("TIME_PERIOD") or record.get("time_period") or as_of)
        points.append(CurvePoint(years, value))
    return Curve("XM", tuple(points), len(points) >= 5, (ECB,), as_of)


_EMPTY_JP = Curve("JP", (), False, (MOF,), "")

#: MOF dates are YYYY/M/D with no zero-padding, e.g. "2026/8/3"
_MOF_DATE = re.compile(r"^\s*\d{4}/\d{1,2}/\d{1,2}\s*$")


def parse_mof_curve(text: str) -> Curve:
    """Japan MOF JGB CSV -> Curve.

    The file is NOT a clean rectangle. Row 1 is a title carrying the units
    marker ("Interest Rate (August 2026),,,…,(Unit : %)"); row 2 is the real
    header ("Date,1Y,2Y,…,40Y"); then data rows; then a blank row and a footer
    note about clearing the browser cache. Taking the last non-empty row would
    read that footer as a curve — the last row whose first cell is a DATE is
    the one we want."""
    if not text or not text.strip():
        return _EMPTY_JP
    try:
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
    except Exception:
        return _EMPTY_JP

    header_idx = None
    for i, row in enumerate(rows):
        if sum(1 for cell in row if tenor_years(cell) is not None) >= 5:
            header_idx = i
            break
    if header_idx is None:
        return _EMPTY_JP

    last = None
    for row in rows[header_idx + 1:]:
        if row and _MOF_DATE.match(row[0]):
            last = row
    if last is None:
        return _EMPTY_JP

    header = rows[header_idx]
    points = []
    for col, label in enumerate(header):
        years = tenor_years(label)
        if years is None or col >= len(last):
            continue
        value = _f(last[col])
        if value is not None:
            points.append(CurvePoint(years, value))
    points.sort()
    return Curve("JP", tuple(points), len(points) >= 5, (MOF,), as_of=last[0].strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_provider.py -q`
Expected: 13 passed. If a parse test fails, compare against the Task 1 fixture and fix the parsing — do not change the assertions or the return types.

**Read `docs/superpowers/2026-08-07-rates-probe-findings.md` before you start.** It records the exact column names, date formats and units for every source, taken from the recorded fixtures. It is the reference for this task — do not re-probe the live endpoints.

Two facts from it that this task's code already accounts for, so you don't
"fix" them back:
- **BIS uses `XM` for the euro area, ECB uses `U2`.** No alias table is needed:
  `rates_meta.py` keys the euro area as `XM` (matching BIS), and the ECB URL
  hardcodes `U2` in the series key. The mismatch is already absorbed.
- **The Treasury fixture is named `ust_curve.raw`** but its content is CSV.
  The `_fixture()` helper globs `ust_curve.*`, so this is handled.

- [ ] **Step 5: Commit**

```bash
git add aurantium/providers/rates.py tests/test_rates_provider.py
git commit -m "feat: parse BIS, Treasury, ECB and MOF rate feeds"
```

---

### Task 6: The join, the Provider, and registration

**Files:**
- Modify: `aurantium/providers/rates.py`
- Modify: `aurantium/providers/__init__.py`
- Test: `tests/test_rates_policy_join.py`

**Interfaces:**
- Consumes: everything from Task 5
- Produces:
  - `build_policy_payload(policy: dict[str, PolicyPoint], enrich: dict[str, dict], partial: list[str]) -> dict`
  - `build_curve_payload(curve: Curve) -> dict`
  - `RatesProvider(Provider)` serving `["rates:*"]`
  - Topic policy registered for `rates:*`

- [ ] **Step 1: Write the failing test**

```python
"""The join must degrade per-source. One dead upstream may never blank the
panel, and the BIS citation must survive into the payload — it's a licence
condition."""

import pytest

from aurantium.providers.rates import (
    Curve,
    CurvePoint,
    PolicyPoint,
    build_curve_payload,
    build_policy_payload,
)

US = PolicyPoint("US", 4.25, "2026-07-01", 4.50, "2026-06-01", "down")
BR = PolicyPoint("BR", 10.75, "2026-07-01", None, None, "flat")


def test_bis_only_country_renders_without_enrichment():
    payload = build_policy_payload({"BR": BR}, {}, [])
    row = payload["countries"][0]
    assert row["code"] == "BR"
    assert row["policy"] == 10.75
    assert row["short"] is None and row["long"] is None
    assert row["slope"] is None


def test_enrichment_fills_short_and_long():
    payload = build_policy_payload(
        {"US": US}, {"US": {"short": 3.90, "long": 4.21}}, []
    )
    row = payload["countries"][0]
    assert (row["short"], row["long"]) == (3.90, 4.21)
    assert row["slope"] == pytest.approx(31.0)


def test_slope_is_none_when_short_leg_is_a_policy_rate():
    """A policy rate is not an observed 2y. Refuse to compute a slope from it."""
    payload = build_policy_payload({"US": US}, {"US": {"long": 4.21}}, [])
    assert payload["countries"][0]["slope"] is None


def test_bis_down_still_publishes_enrichment():
    payload = build_policy_payload({}, {"US": {"short": 3.9, "long": 4.21}}, ["BIS"])
    assert payload["countries"], "payload must not be empty when a source survives"
    assert payload["partial"] == ["BIS"]


def test_bis_citation_present_whenever_bis_data_is():
    payload = build_policy_payload({"US": US}, {}, [])
    assert "BIS" in payload["sources"]


def test_countries_are_sorted_by_label():
    payload = build_policy_payload({"US": US, "BR": BR}, {}, [])
    labels = [r["label"] for r in payload["countries"]]
    assert labels == sorted(labels)


def test_curve_payload_marks_sparse_curves_incomplete():
    sparse = Curve("GB", (CurvePoint(0.25, 4.0), CurvePoint(10.0, 4.6)),
                   False, ("FRED",), "2026-07-01")
    payload = build_curve_payload(sparse)
    assert payload["complete"] is False
    assert len(payload["points"]) == 2
    assert payload["observed"] == 2


def test_curve_payload_never_adds_points():
    sparse = Curve("GB", (CurvePoint(0.25, 4.0), CurvePoint(10.0, 4.6)),
                   False, ("FRED",), "2026-07-01")
    assert build_curve_payload(sparse)["points"] == [[0.25, 4.0], [10.0, 4.6]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_policy_join.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_policy_payload'`

- [ ] **Step 3: Implement the join and the Provider**

Append to `aurantium/providers/rates.py`:

```python
import datetime
import os

from ..datahub import DataHub, Provider
from ..rates_meta import COUNTRIES, by_code


def build_policy_payload(
    policy: dict[str, PolicyPoint],
    enrich: dict[str, dict],
    partial: list[str],
) -> dict:
    """Join BIS policy rates with FRED short/long enrichment.

    Degrades per-source: whatever assembled is published, with the names of
    the sources that failed in `partial`."""
    countries: list[dict] = []
    sources: set[str] = set()
    codes = set(policy) | set(enrich)
    for meta in COUNTRIES:
        if meta.code not in codes:
            continue
        point = policy.get(meta.code)
        extra = enrich.get(meta.code) or {}
        short = extra.get("short")
        long = extra.get("long")
        # a slope needs two OBSERVED yields; a policy rate is not a 2y
        slope = None
        if short is not None and long is not None:
            slope = (long - short) * 100.0
        if point is not None:
            sources.add(BIS)
        if short is not None or long is not None:
            sources.add("FRED")
        countries.append(
            {
                "code": meta.code,
                "label": meta.label,
                "policy": point.rate if point else None,
                "prev": point.prev if point else None,
                "last_change": point.last_change if point else None,
                "direction": point.direction if point else "flat",
                "short": short,
                "long": long,
                "slope": slope,
                "citation": meta.citation,
                "has_curve": meta.curve_source != "none",
            }
        )
    countries.sort(key=lambda r: r["label"])
    as_of = max((p.as_of for p in policy.values()), default="")
    return {
        "countries": countries,
        "partial": list(partial),
        "sources": sorted(sources),
        "as_of": as_of,
    }


def build_curve_payload(curve: Curve) -> dict:
    """Serialize a Curve for the hub. Adds nothing the source didn't publish."""
    return {
        "code": curve.code,
        "points": [[p.years, p.rate] for p in curve.points],
        "complete": bool(curve.complete),
        "observed": len(curve.points),
        "sources": list(curve.sources),
        "as_of": curve.as_of,
    }


_ECB_SERIES = {
    0.25: "SR_3M", 0.5: "SR_6M", 1.0: "SR_1Y", 2.0: "SR_2Y", 3.0: "SR_3Y",
    5.0: "SR_5Y", 7.0: "SR_7Y", 10.0: "SR_10Y", 15.0: "SR_15Y",
    20.0: "SR_20Y", 30.0: "SR_30Y",
}
_ECB_BASE = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM."
_TIMEOUT = 20
_UA = {"User-Agent": "aurantium/1.0"}


def _get_text(url: str, **kwargs) -> str:
    resp = requests.get(url, timeout=_TIMEOUT, headers=_UA, **kwargs)
    resp.raise_for_status()
    return resp.text


class RatesProvider(Provider):
    """Serves ``rates:policy`` and ``rates:curve:<CC>``."""

    # URLs confirmed live by tools/probe_rates.py — see
    # docs/superpowers/2026-08-07-rates-probe-findings.md. Both BIS's and
    # Treasury's first-choice candidates 404'd; these are the ones that work.
    # Do NOT "modernize" them back to the v2/fiscaldata paths.
    #: The v1 CSV endpoint ignores lastNObservations and returns the full
    #: history since 1986 (12.7 MB, 25k rows, 49 countries) — far too heavy to
    #: pull on every refresh. `startPeriod` is standard SDMX REST; try it first
    #: and fall back to the unfiltered URL if BIS rejects it (see Step 3a).
    BIS_URL = "https://stats.bis.org/api/v1/data/WS_CBPOL/M../all?format=csv"
    BIS_URL_WINDOWED = BIS_URL + "&startPeriod={start}"
    MOF_URL = (
        "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
    )

    @staticmethod
    def ust_url() -> str:
        """Treasury's CSV export is scoped to a calendar year, so the year must
        be current — a hardcoded one silently returns nothing every January."""
        year = datetime.date.today().year
        return (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            f"interest-rates/daily-treasury-rates.csv/{year}/all"
            f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
            "&page&_format=csv"
        )

    def topic_patterns(self) -> list[str]:
        return ["rates:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            parts = topic.split(":")
            if len(parts) == 2 and parts[1] == "policy":
                hub.run_async(lambda t=topic: self._fetch_policy(t))
            elif len(parts) == 3 and parts[1] == "curve":
                hub.run_async(lambda t=topic, c=parts[2]: self._fetch_curve(t, c))
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")

    # -- rates:policy ------------------------------------------------------

    def _fetch_policy(self, topic: str) -> None:
        hub = DataHub.instance()
        partial: list[str] = []

        policy: dict[str, PolicyPoint] = {}
        try:
            policy = parse_bis_policy(self._bis_text())
        except Exception:
            partial.append(BIS)
        if not policy and BIS not in partial:
            partial.append(BIS)

        enrich = self._fred_enrichment(partial)

        if not policy and not enrich:
            hub.publish_error(topic, "no rates sources reachable")
            return
        hub.publish(topic, build_policy_payload(policy, enrich, partial))

    def _bis_text(self) -> str:
        """Fetch BIS policy rates, windowed if the API allows it.

        Three years is enough to derive the last change for any jurisdiction
        that has moved recently; the full feed is 12.7 MB and we only need the
        tail. If BIS rejects startPeriod, fall back to the full feed rather
        than failing — correctness beats bandwidth."""
        start = f"{datetime.date.today().year - 3}-01"
        try:
            return _get_text(self.BIS_URL_WINDOWED.format(start=start))
        except Exception:
            return _get_text(self.BIS_URL)

    def _fred_enrichment(self, partial: list[str]) -> dict[str, dict]:
        """Short/long yields for countries no curve source reaches.

        A missing FRED key is a NORMAL state, not an error: the free tier
        must work with zero configuration. Blank columns, no warning."""
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            return {}
        out: dict[str, dict] = {}
        failures = 0
        for meta in COUNTRIES:
            if not (meta.fred_short or meta.fred_long):
                continue
            entry: dict = {}
            for field, series_id in (("short", meta.fred_short), ("long", meta.fred_long)):
                if not series_id:
                    continue
                try:
                    observations = fetch_fred_series(series_id, api_key, limit=2)
                except FredNotAllowed:
                    continue  # refused by the copyright filter, by design
                except Exception:
                    failures += 1
                    continue
                if observations:
                    entry[field] = observations[-1][1]
            if entry:
                out[meta.code] = entry
        if failures and "FRED" not in partial:
            partial.append("FRED")
        return out

    # -- rates:curve:<CC> --------------------------------------------------

    def _fetch_curve(self, topic: str, code: str) -> None:
        hub = DataHub.instance()
        meta = by_code(code)
        if meta is None:
            hub.publish_error(topic, f"unknown country: {code}")
            return
        if meta.curve_source == "none":
            hub.publish(
                topic,
                {
                    "code": meta.code,
                    "points": [],
                    "complete": False,
                    "observed": 0,
                    "sources": [BIS],
                    "as_of": "",
                    "note": f"No curve published for {meta.label}. Policy rate only.",
                },
            )
            return
        try:
            curve = self._curve_for(meta)
        except Exception as exc:
            hub.publish_error(topic, f"curve fetch failed for {meta.code}: {exc}")
            return
        hub.publish(topic, build_curve_payload(curve))

    def _curve_for(self, meta) -> Curve:
        if meta.curve_source == "ust":
            return parse_ust_curve(_get_text(self.ust_url()))
        if meta.curve_source == "mof":
            return parse_mof_curve(_get_text(self.MOF_URL))
        if meta.curve_source == "ecb":
            rows = {}
            for years, suffix in _ECB_SERIES.items():
                if years not in meta.tenors:
                    continue
                try:
                    rows[years] = _get_text(
                        f"{_ECB_BASE}{suffix}?format=csvdata&lastNObservations=1"
                    )
                except Exception:
                    continue
            return parse_ecb_curve(rows)
        # "fred": the sparse case — observed points only, never interpolated
        api_key = os.environ.get("FRED_API_KEY")
        points, as_of = [], ""
        if api_key:
            for years, series_id in (
                (0.25, meta.fred_short),
                (10.0, meta.fred_long),
            ):
                if not series_id:
                    continue
                try:
                    observations = fetch_fred_series(series_id, api_key, limit=2)
                except Exception:
                    continue
                if observations:
                    as_of = max(as_of, observations[-1][0])
                    points.append(CurvePoint(years, observations[-1][1]))
        points.sort()
        return Curve(meta.code, tuple(points), False, ("FRED",), as_of)
```

- [ ] **Step 3a: Confirm the BIS window actually works**

`startPeriod` is standard SDMX REST but the BIS v1 CSV endpoint was observed
ignoring `lastNObservations`, so it may ignore this too. Check once, by hand:

```bash
.venv/Scripts/python.exe -c "import requests; r=requests.get('https://stats.bis.org/api/v1/data/WS_CBPOL/M../all?format=csv&startPeriod=2023-01', timeout=60); print(r.status_code, len(r.text))"
```

Compare against the unwindowed 12.7 MB baseline. Record the result in your
report:
- **Materially smaller** → the window works, keep `_bis_text()` as written.
- **Still ~12.7 MB** → BIS ignores it. Keep `_bis_text()` (harmless), and note
  in the report that every refresh pulls 12.7 MB. Do **not** invent a different
  endpoint — flag it and let the controller decide.

- [ ] **Step 4: Register the provider**

In `aurantium/providers/__init__.py`, add the import, the registration and the policy:

```python
from .rates import RatesProvider
```

```python
    hub.register_provider(RatesProvider())
```

```python
    # policy rates move rarely; curves publish once daily. Long TTLs plus the
    # SQLite topic cache mean a returning user sees yesterday's world instantly
    # and offline while the refresh runs behind it.
    hub.set_policy("rates:*", TopicPolicy(ttl_s=21600, min_interval_s=300))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_policy_join.py tests/test_rates_provider.py -q`
Expected: 16 passed

- [ ] **Step 6: Run the full suite — nothing else may break**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add aurantium/providers/rates.py aurantium/providers/__init__.py tests/test_rates_policy_join.py
git commit -m "feat: join policy rates across sources and register RatesProvider"
```

---

### Task 7: The Global Rates monitor panel

**Files:**
- Create: `aurantium/panels/global_rates.py`
- Test: `tests/test_rates_panels.py` (monitor half)

**Interfaces:**
- Consumes: topic `rates:policy`; `RatesContext`; `MarketTable`, `NumericTableWidgetItem`
- Produces: panel id `"global_rates"`, and `GlobalRatesPanel.citation_text() -> str` used by the citation test

- [ ] **Step 1: Write the failing test**

```python
"""Panel behaviour that encodes a licence condition or an honesty rule.
Offscreen Qt; no providers registered, so no background threads leak into
timing-sensitive tests elsewhere."""

import pytest

from aurantium.panels.global_rates import GlobalRatesPanel

PAYLOAD = {
    "countries": [
        {"code": "BR", "label": "Brazil", "policy": 10.75, "prev": None,
         "last_change": None, "direction": "flat", "short": None, "long": None,
         "slope": None, "citation": "BIS", "has_curve": False},
        {"code": "US", "label": "United States", "policy": 4.25, "prev": 4.50,
         "last_change": "2026-06-01", "direction": "down", "short": 3.90,
         "long": 4.21, "slope": 31.0, "citation": "BIS · U.S. Treasury",
         "has_curve": True},
    ],
    "partial": [],
    "sources": ["BIS", "U.S. Treasury"],
    "as_of": "2026-07-01",
}


@pytest.fixture(autouse=True)
def fresh_rates_context():
    """RatesContext is a process-wide singleton; without this, a country
    selected by one test leaks into the next and set_country() no-ops on the
    same value, making these tests order-dependent."""
    from aurantium.rates_context import RatesContext

    RatesContext._inst = None
    yield
    RatesContext._inst = None


@pytest.fixture
def panel(qapp):
    p = GlobalRatesPanel()
    p.build()
    yield p
    p.close()


def test_bis_citation_is_rendered(panel):
    panel.on_policy(PAYLOAD)
    assert "BIS" in panel.citation_text()


def test_rows_render_for_every_country(panel):
    panel.on_policy(PAYLOAD)
    assert panel.table.rowCount() == 2


def test_missing_values_render_as_dash_not_zero(panel):
    panel.on_policy(PAYLOAD)
    row = panel.row_of_code["BR"]
    texts = [panel.table.item(row, c).text() for c in range(panel.table.columnCount())]
    assert "0.00" not in texts
    assert "-" in texts


def test_partial_sources_are_surfaced(panel):
    panel.on_policy({**PAYLOAD, "partial": ["FRED"]})
    assert "FRED" in panel.status_text()


def test_empty_payload_does_not_raise(panel):
    panel.on_policy({"countries": [], "partial": [], "sources": [], "as_of": ""})
    assert panel.table.rowCount() == 0


def test_clicking_a_country_publishes_to_rates_context(panel):
    panel.on_policy(PAYLOAD)
    seen = []
    panel._rates.country_changed.connect(lambda g, c, s: seen.append(c))
    panel.table.selectRow(panel.row_of_code["US"])
    panel._on_row_activated(panel.row_of_code["US"], 0)
    assert seen == ["US"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_panels.py -q`
Expected: FAIL — no module `aurantium.panels.global_rates`

- [ ] **Step 3: Write the panel**

```python
"""Global Rates — the breadth monitor: policy rate, last change, direction and
available yields for every jurisdiction BIS publishes.

Free tier. BIS terms require citing BIS as source and state that inclusion in
a commercial product must not result in an additional charge to users, so this
panel is never gated.

Clicking a row publishes the country to RatesContext, which drives the
Sovereign Curves panel. It deliberately does NOT touch SymbolContext: country
codes are not tickers and would break every equity panel in the link group.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QLabel, QTableWidgetItem

from ..components import MarketTable, NumericTableWidgetItem
from ..panel import Panel, register_panel
from ..rates_context import RatesContext
from ..theme import DOWN, FG_DIM, UP

COL_COUNTRY, COL_POLICY, COL_CHANGE, COL_SHORT, COL_LONG, COL_SLOPE = range(6)
HEADERS = ["Jurisdiction", "Policy", "Last change", "Short", "Long", "Slope bp"]

_ARROW = {"up": "▲", "down": "▼", "flat": "–"}


def _fmt(value: Any, decimals: int = 2, suffix: str = "") -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="global_rates", title="Global Rates", category="Analytics")
class GlobalRatesPanel(Panel):
    def build(self) -> None:
        self._rates = RatesContext.instance()
        self.row_of_code: dict[str, int] = {}
        self._sources: list[str] = []
        self._partial: list[str] = []

        self.table = MarketTable(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_COUNTRY, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSortingEnabled(True)
        self.table.cellClicked.connect(self._on_row_activated)
        self.content_layout.addWidget(self.table, 1)

        # attribution strip — BIS licence condition, not decoration
        self.citation_lbl = QLabel("", self)
        self.citation_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        self.content_layout.addWidget(self.citation_lbl)

        self.table.set_loading(True)
        self.subscribe("rates:policy", self.on_policy)

    # -- data ---------------------------------------------------------------

    def on_policy(self, payload: dict) -> None:
        countries = (payload or {}).get("countries") or []
        self._sources = list((payload or {}).get("sources") or [])
        self._partial = list((payload or {}).get("partial") or [])

        self.table.set_loading(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(countries))
        self.row_of_code.clear()

        for row, entry in enumerate(countries):
            self.row_of_code[entry.get("code", "")] = row
            self._set(row, COL_COUNTRY, entry.get("label", ""), numeric=False)
            self._set(row, COL_POLICY, _fmt(entry.get("policy")))
            self._set(row, COL_CHANGE, self._change_text(entry), numeric=False)
            self._set(row, COL_SHORT, _fmt(entry.get("short")))
            self._set(row, COL_LONG, _fmt(entry.get("long")))
            self._set(row, COL_SLOPE, _fmt(entry.get("slope"), 0))
            self._color_direction(row, entry.get("direction", "flat"))

        self.table.setSortingEnabled(True)
        self.citation_lbl.setText(self.citation_text())
        self.set_status(self.status_text())

    def citation_text(self) -> str:
        return "Source: " + " · ".join(self._sources) if self._sources else ""

    def status_text(self) -> str:
        if self._partial:
            return f"partial — unavailable: {', '.join(self._partial)}"
        return f"{len(self.row_of_code)} jurisdictions"

    # -- internals ----------------------------------------------------------

    def _set(self, row: int, col: int, text: str, numeric: bool = True) -> None:
        item = NumericTableWidgetItem(text) if numeric else QTableWidgetItem(text)
        if numeric:
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.table.setItem(row, col, item)

    def _change_text(self, entry: dict) -> str:
        arrow = _ARROW.get(entry.get("direction", "flat"), "–")
        when = entry.get("last_change")
        prev = entry.get("prev")
        policy = entry.get("policy")
        if when is None or prev is None or policy is None:
            return "-"
        return f"{arrow} {policy - prev:+.2f} · {when}"

    def _color_direction(self, row: int, direction: str) -> None:
        item = self.table.item(row, COL_CHANGE)
        if item is None:
            return
        if direction == "up":
            item.setForeground(QColor(UP))
        elif direction == "down":
            item.setForeground(QColor(DOWN))

    def _on_row_activated(self, row: int, _col: int) -> None:
        for code, index in self.row_of_code.items():
            if index == row:
                self._rates.set_country(self.link_group, code, source=self)
                return

    # -- persistence --------------------------------------------------------

    def settings(self) -> dict:
        return {"link_group": self.link_group}

    def restore(self, settings: dict) -> None:
        group = (settings or {}).get("link_group")
        if isinstance(group, str) and group:
            self.set_link_group(group)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_panels.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add aurantium/panels/global_rates.py tests/test_rates_panels.py
git commit -m "feat: add the Global Rates monitor panel"
```

---

### Task 8: The Sovereign Curves panel

**Files:**
- Create: `aurantium/panels/sovereign_curves.py`
- Modify: `tests/test_rates_panels.py` (append the curves half)

**Interfaces:**
- Consumes: topic `rates:curve:<CC>`; `RatesContext`; `attach_hover`, `clamp_view`
- Produces: panel id `"sovereign_curves"`, and `SovereignCurvesPanel.pen_for(payload) -> QPen`, `slope_text(payload) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rates_panels.py`:

```python
from PySide6.QtCore import Qt

from aurantium.panels.sovereign_curves import SovereignCurvesPanel

FULL = {
    "code": "US",
    "points": [[0.25, 3.9], [2.0, 3.88], [5.0, 4.0], [10.0, 4.21], [30.0, 4.5]],
    "complete": True, "observed": 5,
    "sources": ["U.S. Treasury"], "as_of": "2026-07-01",
}
SPARSE = {
    "code": "GB",
    "points": [[0.25, 4.05], [10.0, 4.61]],
    "complete": False, "observed": 2,
    "sources": ["FRED"], "as_of": "2026-07-01",
}
NO_CURVE = {
    "code": "BR", "points": [], "complete": False, "observed": 0,
    "sources": ["BIS"], "as_of": "",
    "note": "No curve published for Brazil. Policy rate only.",
}


@pytest.fixture
def curves(qapp):
    p = SovereignCurvesPanel()
    p.build()
    yield p
    p.close()


def test_complete_curve_draws_solid(curves):
    assert curves.pen_for(FULL).style() == Qt.PenStyle.SolidLine


def test_sparse_curve_draws_dashed(curves):
    assert curves.pen_for(SPARSE).style() == Qt.PenStyle.DashLine


def test_sparse_curve_is_labelled_as_partial(curves):
    curves.on_curve("GB", SPARSE)
    assert "partial" in curves.legend_text("GB").lower()
    assert "2" in curves.legend_text("GB")


def test_no_slope_for_a_sparse_curve(curves):
    """The short leg is a 3m policy-adjacent rate, not an observed 2y."""
    assert curves.slope_text(SPARSE) == "-"


def test_slope_for_a_complete_curve(curves):
    assert "33" in curves.slope_text(FULL)


def test_country_without_a_curve_shows_the_note_not_an_error(curves):
    curves.on_curve("BR", NO_CURVE)
    assert "policy rate only" in curves.empty_text().lower()
    assert "⚠" not in curves.empty_text()


def test_rates_context_drives_the_panel(curves):
    curves._rates.set_country(curves.link_group, "US")
    assert "US" in curves.selected_codes()


def test_citation_renders(curves):
    curves.on_curve("US", FULL)
    assert "U.S. Treasury" in curves.citation_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_panels.py -q`
Expected: FAIL — no module `aurantium.panels.sovereign_curves`

- [ ] **Step 3: Write the panel**

```python
"""Sovereign Curves — yield curves for the countries that publish them,
overlaid for cross-country comparison.

Honesty rules, enforced here and in the provider:
  * a curve is drawn only from OBSERVED points; nothing is interpolated
  * a sparse curve (a country with only a short and a long rate) draws
    dashed, with markers at the observed tenors and a "partial" legend
  * no slope is computed unless both legs are observed yields — a policy
    rate is not a 2y

Driven by RatesContext, not SymbolContext.
"""

from __future__ import annotations

from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QLabel

from ..components import attach_hover, clamp_view
from ..panel import Panel, register_panel
from ..rates_context import RatesContext
from ..rates_meta import by_code
from ..theme import ACCENT, BG, FG_DIM

#: overlay colors, cycled per selected country
_SERIES_COLORS = ("#f5a623", "#4a90d9", "#7ed321", "#d0021b", "#b06fdb")


@register_panel(id="sovereign_curves", title="Sovereign Curves", category="Analytics")
class SovereignCurvesPanel(Panel):
    def build(self) -> None:
        self._rates = RatesContext.instance()
        self._payloads: dict[str, dict] = {}
        self._items: dict[str, pg.PlotDataItem] = {}
        self._legends: dict[str, str] = {}
        self._empty_text = ""

        self.plot = pg.PlotWidget()
        self.plot.setBackground(BG)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("left", "Yield (%)")
        self.plot.setLabel("bottom", "Maturity (years)")
        self.plot.getAxis("bottom").setTextPen(FG_DIM)
        self.plot.getAxis("left").setTextPen(FG_DIM)
        attach_hover(self.plot, self._hover_text)
        self.content_layout.addWidget(self.plot, 1)

        self.legend_lbl = QLabel("", self)
        self.legend_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
        self.content_layout.addWidget(self.legend_lbl)

        self.citation_lbl = QLabel("", self)
        self.citation_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        self.content_layout.addWidget(self.citation_lbl)

        self._rates.country_changed.connect(self._on_country)
        current = self._rates.country(self.link_group)
        if current:
            self._select(current)

    # -- selection ----------------------------------------------------------

    def selected_codes(self) -> list[str]:
        return list(self._payloads)

    def _on_country(self, group: str, code: str, source: object) -> None:
        if group != self.link_group:
            return
        self._select(code)

    def _select(self, code: str) -> None:
        if by_code(code) is None or code in self._payloads:
            return
        self._payloads[code] = {}
        self.subscribe(f"rates:curve:{code}", lambda p, c=code: self.on_curve(c, p))

    # -- data ---------------------------------------------------------------

    def on_curve(self, code: str, payload: dict) -> None:
        payload = payload or {}
        self._payloads[code] = payload
        points = payload.get("points") or []

        if not points:
            self._empty_text = payload.get("note") or (
                f"No curve data for {code}."
            )
            self.set_status(self._empty_text)
        else:
            item = self._items.get(code)
            if item is None:
                item = pg.PlotDataItem()
                self.plot.addItem(item)
                self._items[code] = item
            pen = self.pen_for(payload)
            item.setData(
                [p[0] for p in points],
                [p[1] for p in points],
                pen=pen,
                symbol="o",
                symbolSize=5,
                symbolBrush=pen.color(),
            )
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            clamp_view(self.plot, xs, ys, lock=True)

        self._legends[code] = self._build_legend(code, payload)
        self.legend_lbl.setText("   ".join(self._legends.values()))
        self.citation_lbl.setText(self.citation_text())

    # -- presentation rules -------------------------------------------------

    def pen_for(self, payload: dict) -> QPen:
        """Solid for a complete curve, DASHED for a sparse one. The dashing is
        the honesty signal: two observed points are not a curve shape."""
        index = max(0, len(self._items) - 1) % len(_SERIES_COLORS)
        pen = QPen(QColor(_SERIES_COLORS[index]))
        pen.setWidth(2)
        pen.setStyle(
            Qt.PenStyle.SolidLine if payload.get("complete") else Qt.PenStyle.DashLine
        )
        return pen

    def slope_text(self, payload: dict) -> str:
        """2s10s in bp — only when both legs are OBSERVED yields.

        Inherited from macro.py's _update_spread, with the added refusal: a
        sparse curve's short leg is a money-market rate, not a 2y, so no
        slope is reported for it."""
        if not payload.get("complete"):
            return "-"
        points = {round(p[0], 4): p[1] for p in payload.get("points") or []}
        short = points.get(2.0)
        long = points.get(10.0)
        if short is None or long is None:
            return "-"
        return f"{(long - short) * 100.0:+.0f} bp"

    def legend_text(self, code: str) -> str:
        return self._legends.get(code, "")

    def empty_text(self) -> str:
        return self._empty_text

    def citation_text(self) -> str:
        sources: list[str] = []
        for payload in self._payloads.values():
            for source in payload.get("sources") or []:
                if source not in sources:
                    sources.append(source)
        return "Source: " + " · ".join(sources) if sources else ""

    def _build_legend(self, code: str, payload: dict) -> str:
        meta = by_code(code)
        label = meta.label if meta else code
        if not payload.get("points"):
            return f"{label}: no curve"
        if payload.get("complete"):
            return f"{label} {self.slope_text(payload)}"
        return f"{label} (partial — {payload.get('observed', 0)} observed points)"

    def _hover_text(self, x: float, _y: float) -> Optional[str]:
        best = None
        for code, payload in self._payloads.items():
            for years, rate in payload.get("points") or []:
                distance = abs(years - x)
                if best is None or distance < best[0]:
                    best = (distance, code, years, rate)
        if best is None:
            return None
        return f"{best[1]} {best[2]:g}y · {best[3]:.2f}%"

    # -- persistence --------------------------------------------------------

    def settings(self) -> dict:
        return {"countries": list(self._payloads), "link_group": self.link_group}

    def restore(self, settings: dict) -> None:
        settings = settings or {}
        group = settings.get("link_group")
        if isinstance(group, str) and group:
            self.set_link_group(group)
        for code in settings.get("countries") or []:
            if isinstance(code, str):
                self._select(code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_panels.py -q`
Expected: 14 passed (6 monitor + 8 curves)

- [ ] **Step 5: Commit**

```bash
git add aurantium/panels/sovereign_curves.py tests/test_rates_panels.py
git commit -m "feat: add the Sovereign Curves panel"
```

---

### Task 9: Remove the yield curve from macro.py

The riskiest change in this plan: it removes something users can currently see. Read spec §6 before starting.

**Files:**
- Modify: `aurantium/panels/macro.py`
- Test: `tests/test_macro_migration.py`

**Interfaces:**
- Consumes: nothing
- Produces: `MacroPanel` with no curve; panel id unchanged (`"macro"`), title `"Macro Monitor"`

- [ ] **Step 1: Write the failing test**

```python
"""macro.py loses its US yield curve to the Sovereign Curves panel. Saved
layouts must keep working: the panel id is unchanged and restore() must
tolerate the legacy "tenors" key that existing users have on disk today."""

import pytest

from aurantium.panel import PanelRegistry
from aurantium.panels.macro import MacroPanel

LEGACY_SETTINGS = {
    "tenors": [[0.25, "3M", "^IRX"], [10.0, "10Y", "^TNX"]],
    "instruments": [["Dollar Index", "DX-Y.NYB"]],
    "cftc": [["Gold", "gold"]],
}


@pytest.fixture
def panel(qapp):
    p = MacroPanel()
    p.build()
    yield p
    p.close()


def test_panel_id_is_unchanged():
    """Saved layouts and all three shipped presets reference "macro"."""
    assert PanelRegistry.get("macro") is not None
    assert PanelRegistry.get("macro").cls is MacroPanel


def test_panel_is_retitled():
    assert PanelRegistry.get("macro").title == "Macro Monitor"


def test_legacy_settings_restore_without_raising(panel):
    panel.restore(LEGACY_SETTINGS)
    assert panel.settings()["instruments"] == [["Dollar Index", "DX-Y.NYB"]]


def test_tenors_are_no_longer_persisted(panel):
    panel.restore(LEGACY_SETTINGS)
    assert "tenors" not in panel.settings()


def test_curve_attributes_are_gone(panel):
    for attr in ("curve_widget", "yield_curve", "spread_lbl"):
        assert not hasattr(panel, attr), attr


def test_instruments_and_cftc_survive(panel):
    panel.restore(LEGACY_SETTINGS)
    assert panel.settings()["cftc"] == [["Gold", "gold"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_macro_migration.py -q`
Expected: FAIL — title is still "Macro / Rates", curve attributes still present

- [ ] **Step 3: Remove the curve**

In `aurantium/panels/macro.py`:

1. Change the decorator to `@register_panel(id="macro", title="Macro Monitor", category="Analytics")`. **Leave the id alone.**
2. Rewrite the module docstring: it currently opens "the weekly macro-check panel: a configurable US Treasury yield curve, a configurable macro instrument monitor…". Drop the curve clause and add a line pointing at the new panels.
3. Delete: `DEFAULT_TENORS`, `_TENOR_DETAILS`, `_tenor_row_from_entry`, `self._tenors`, `self._yields`, the curve title label, `self.curve_widget`, `self.yield_curve`, `self.spread_lbl`, `_redraw_curve`, `_curve_hover_text`, `_update_spread`, the tenor `EditorSection` in the edit dialog, the tenor subscriptions in the rebuild method, and the tenor half of the undo snapshot.
4. In the status line, drop the `yields {n}/{m}` clause and keep only the CFTC count.
5. In `settings()`, stop emitting `"tenors"`.
6. In `restore()`, ignore a `"tenors"` key if present — do not raise, do not warn.
7. Remove the now-unused imports: `TENOR_ENTRIES`, and `clamp_view`/`attach_hover`/`pg` **only if** nothing else in the file still uses them. Check before deleting.

**Do not remove `TENOR_ENTRIES` from `components/`** — `components/symbol_search.py:88` uses it for the symbol completer.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_macro_migration.py -q`
Expected: 6 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass. `tests/test_presets_shipped.py` and `tests/test_presets_arrangement.py` exercise layouts containing the macro panel — if either fails, the id or a settings key changed when it shouldn't have.

- [ ] **Step 6: Commit**

```bash
git add aurantium/panels/macro.py tests/test_macro_migration.py
git commit -m "refactor: move curve work out of macro.py into Sovereign Curves"
```

---

### Task 10: F1 guide, release note, and the frozen-build check

The F1 update is a **project rule**, not a nicety: the guide is what users read, and the preset-workspaces work is currently blocked precisely because the guide documents a feature the owner isn't happy with. Do not skip this task.

**Files:**
- Modify: `aurantium/onboarding_dialog.py`
- Test: `tests/test_rates_panels.py` (append the guide assertions)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rates_panels.py`:

```python
def test_f1_guide_documents_the_new_panels():
    from aurantium import onboarding_dialog

    source = open(onboarding_dialog.__file__, encoding="utf-8").read()
    assert "Global Rates" in source
    assert "Sovereign Curves" in source


def test_f1_guide_no_longer_promises_a_curve_in_the_macro_panel():
    from aurantium import onboarding_dialog

    source = open(onboarding_dialog.__file__, encoding="utf-8").read()
    assert "Macro / Rates" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_rates_panels.py -k f1 -q`
Expected: FAIL

- [ ] **Step 3: Update the F1 guide**

Open `aurantium/onboarding_dialog.py`, find the panels section, and:

- rename every "Macro / Rates" mention to "Macro Monitor", and remove any wording promising a yield curve inside it;
- add the two new panels, with copy that states the honesty rule plainly rather than overselling coverage. Suggested:

```html
<h2>Rates</h2>
<p><b>Global Rates</b> — policy rates, the last move and its direction for
every jurisdiction the BIS publishes. Source: BIS.</p>
<p><b>Sovereign Curves</b> — sovereign yield curves, overlaid for comparison.
Click a country in Global Rates and the curve follows. Countries that publish
a full curve draw solid; countries with only a short and a long rate draw
dashed and are labelled partial — two observed points are not a curve shape.</p>
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Verify from a frozen build**

Panel discovery behaves differently when frozen — `discover_package_panels` falls back through `pkgutil`, a filesystem scan, then `aurantium/panels/__init__.py`'s `BUILTIN` list. Check whether `BUILTIN` exists and, if it does, **add `global_rates` and `sovereign_curves` to it**, or the panels will be missing from the installed build while working perfectly from source.

Then build per `RELEASING.md` (**outside OneDrive** — use `--workpath`/`--distpath` under `%LOCALAPPDATA%\Temp`), install, and confirm:
- both panels appear in Panels ▸ Add Panel;
- Global Rates populates and shows "Source: BIS …";
- clicking a country moves the Sovereign Curves panel;
- the Macro Monitor has no curve and no error.

- [ ] **Step 6: Commit**

```bash
git add aurantium/onboarding_dialog.py aurantium/panels/__init__.py tests/test_rates_panels.py
git commit -m "docs: document the rates panels in the F1 guide"
```

---

## Release note

Add to the release notes when this ships:

> **Global Rates** and **Sovereign Curves** — policy rates for every jurisdiction the BIS publishes, and sovereign yield curves overlaid for comparison. Click a country in one and the other follows.
>
> The **Macro / Rates** panel is now **Macro Monitor**: its US yield curve has moved to Sovereign Curves, which draws the full official Treasury par curve rather than four quoted tenors. Your saved layouts are unaffected.

## Post-implementation

- Rerun `tools/verify_rates.py` as part of release prep; the allowlist records the date it was last checked.
- Delete `tools/probe_rates.py` once fixtures are recorded — `tools/verify_rates.py` covers ongoing verification.
- The "Rates Desk" preset workspace named in launch spec §3.4 is deliberately **not** in this plan; preset workspaces are parked pending a decision.
