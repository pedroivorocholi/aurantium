# Global Rates — Design

**Date:** 2026-08-07
**Status:** Approved, not implemented
**Parent:** Plan A2 of `docs/superpowers/specs/2026-08-05-aurantium-launch-design.md` §3.2
(that file currently lives in the untracked `Dashboard/docs/` tree — see §11)

---

## 1. What this is

Two new panels and one new provider giving Aurantium multi-country policy rates and
sovereign yield curves, sourced entirely from free, redistributable data. Nothing here
is blocked on the data licence in the launch spec §5.

- **Global Rates** — a breadth monitor. Policy rate, last change, direction and
  available yields across every jurisdiction BIS covers. Free tier.
- **Sovereign Curves** — a depth panel. Full yield curves for the countries that
  publish them, overlaid for comparison, with slope analytics. Pro tier.

Clicking a country in the monitor re-centres the curves panel. That is the cross-panel
linking PRODUCT.md names as the product's one differentiator, applied to macro for the
first time.

---

## 2. Decisions fixed before design

These were settled in brainstorming. Do not reopen them without a reason.

| Decision | Value |
|---|---|
| Shape | Two panels, linked — not one combined panel |
| Linking | A new `RatesContext`, separate from `SymbolContext` |
| Sparse coverage | Countries with partial data get a dashed 2-point curve, explicitly marked |
| Tier | Monitor free · Curves Pro (BIS content free regardless — see §7) |
| `macro.py` | Its US yield curve is **removed**; all curve work moves to the new panels |
| Provider shape | Country-shaped topics, provider does the joining (approach B of three) |

### 2.1 Why a separate `RatesContext`

`SymbolContext` (`symbol_context.py:51`) carries one free-text symbol per link group, and
every panel joins group "A" by default with no type discrimination on the value. Publishing
a country code into that channel would make the chart, news, fundamentals and options
panels all attempt to load it as a ticker.

The alternative considered was a prefixed pseudo-symbol (`"RATES:JP"`), rejected because it
requires a guard clause in every existing panel *and* silently breaks any third-party panel
in `user_panels/` written before the convention existed.

`RatesContext` reuses the same A/B/C/D group vocabulary and badge UI, so to the user the two
behave identically.

### 2.2 Why the provider joins, not the panels

Source-shaped topics (`bis:cbpol:BR`, `ecb:yc`, …) would leave the monitor subscribing to
~40 topics and performing the cross-source join itself, duplicated again in the curves
panel. `macro.py` is already 692 lines; pushing more assembly into panels makes the worst
files worse.

A `providers/rates/` subpackage was also considered and deferred. Five sources do not
justify it, there is no provider-subpackage precedent in this codebase, and it adds import
surface to the frozen build — which has bitten this project before. The topic names are the
contract, so `rates.py` can be split into a package later without touching a panel.

---

## 3. Architecture

```
NEW  aurantium/rates_meta.py             curated country table
NEW  aurantium/rates_context.py          RatesContext singleton
NEW  aurantium/providers/rates.py        RatesProvider
NEW  aurantium/panels/global_rates.py    the monitor      (free)
NEW  aurantium/panels/sovereign_curves.py the curves      (Pro)
NEW  aurantium/rates_allowlist.py        generated: vetted FRED series
NEW  tools/verify_rates.py               regenerates the allowlist, verifies the table

MOD  aurantium/panels/macro.py           curve removed, retitled "Macro Monitor"
MOD  aurantium/providers/__init__.py     registration + TopicPolicy
MOD  aurantium/onboarding_dialog.py      F1 guide, same change (project rule)
```

### 3.1 `rates_meta.py`

The spine. Mirrors the established `commodities_meta.py` pattern: a curated NamedTuple
table, verified live before entries are listed, with a `tools/` script to re-verify.

```python
class CountryMeta(NamedTuple):
    label: str            # "Japan"
    code: str             # "JP" — ISO 3166-1 alpha-2, the RatesContext key
    bis_key: str          # BIS jurisdiction identifier for CBPOL
    curve_source: str     # "ust" | "ecb" | "mof" | "fred" | "none"
    tenors: tuple[float, ...]   # maturities the source actually publishes, in years
    fred_short: str | None      # allowlisted series id, or None
    fred_long: str | None
    citation: str         # attribution string the UI must render
    tier: str             # "free" | "pro"
```

Widening coverage is a data edit. No code changes.

### 3.2 `rates_context.py`

A near-copy of `symbol_context.py`: same groups, `set_country(group, code, source)`,
`country_changed(group, code, source)`, `to_json`/`from_json`.

**This duplication is deliberate.** Extracting a shared base was considered and rejected:
the two contexts will diverge (a country code is validated against `rates_meta.py`, a symbol
is free text), and a shared base would couple every equity panel to rates changes. Roughly
50 lines of duplication is the cheaper of the two mistakes.

---

## 4. Data flow

### 4.1 Topics

| Topic | Payload |
|---|---|
| `rates:policy` | One envelope covering all jurisdictions: `{countries: [{code, label, policy, prev, last_change, direction, short, long, slope, sources}, …], partial: [source names], as_of}`. `slope` is `null` unless both legs are observed yields (§4.4). |
| `rates:curve:<CC>` | One country: `{code, points: [[years, yield], …], complete: bool, sources: [...], as_of}` |

Registered in `providers/__init__.py`:

```python
hub.set_policy("rates:*", TopicPolicy(ttl_s=21600, min_interval_s=300))
```

Long TTLs are correct here — policy rates move rarely, curves publish once daily. Combined
with the existing SQLite topic cache (`cache.py`), a returning user sees yesterday's world
instantly and offline while the refresh runs behind it. A rates desk is still useful a day
stale, which is not true of quotes.

### 4.2 Sources

| Source | Serves | Key | Terms |
|---|---|---|---|
| BIS Statistics (CBPOL) | Policy rates, ~38 jurisdictions | none | Free — **must cite BIS** |
| US Treasury FiscalData | Full US par yield curve | none | US public domain |
| ECB Data Portal (SDMX) | Euro-area AAA spot curve | none | Free |
| Japan MOF | JGB yield curve | none | Free |
| FRED | International short/long rates | user key | Free — **copyright filter required** |

Note the spec §3.2 attributes JGB yields to the Bank of Japan. BoJ publishes the *policy
rate*; the JGB *curve* is published by the Ministry of Finance. Both are verified in task 1.

### 4.3 `rates:policy` assembly

BIS is the spine — keyless, broad, and carrying enough history that `last_change` and
`direction` are derived by diffing the series rather than needing a separate decisions feed.
FRED fills `short`/`long` only for countries no curve source reaches.

**FRED is optional.** No key means two blank columns, not an error. The free tier must work
with zero configuration. This differs from `econ.py`, which publishes an error when
`FRED_API_KEY` is unset — correct there, wrong here.

### 4.4 `rates:curve:<CC>` routing

Routed by `curve_source`: `ust` → Treasury FiscalData, `ecb` → ECB AAA spot curve,
`mof` → MOF JGB CSV, `fred` → the sparse two-point case, `none` → an explicit
"policy rate only" payload the panel renders as an empty state, not an error.

**The provider never interpolates.** A sparse country returns exactly the points observed,
with `complete: false`. The honesty guarantee lives in the data shape, not only in styling,
so no future consumer can launder a 2-point series into a smooth curve.

Panel rendering of a sparse curve: dashed line, markers at observed tenors only, a
"partial — N observed points" label. **No slope is computed** for a country whose short leg
is a policy rate rather than an observed 2y.

---

## 5. Error handling

Degradation is **per-source, never all-or-nothing**. BIS down → FRED columns still render.
FRED absent → BIS columns still render. `rates:policy` publishes whatever it assembled plus
`partial`, surfaced in the panel status strip.

Three failure modes this codebase has already been bitten by:

- **Nothing in a panel construction path may raise.** `MainWindow.__init__` is not wrapped
  in a try at `__main__.py:327` — an exception there means no window at all (this exact bug
  happened with `available_presets()`). `RatesContext.from_json()` reads user-editable
  layout JSON: it validates codes against `rates_meta.py` and silently drops unknowns.
- **A missing FRED key is a normal state**, not a `⚠`.
- **An unlisted FRED series is refused before the request is made**, never fetched-then-
  filtered, so a network failure cannot fail open. Refusals are logged so a stale allowlist
  is diagnosable.

---

## 6. The `macro.py` migration

The riskiest part of this change: it removes something users can currently see.

**Removed** (~250 lines of 692): the curve widget, `_redraw_curve`, `_curve_hover_text`,
`_update_spread` and `spread_lbl`, `DEFAULT_TENORS`, `_TENOR_DETAILS`,
`_tenor_row_from_entry`, the tenor editor section, tenor subscriptions, and the yields half
of the status counter.

**Kept:** the instrument monitor and the CFTC positioning table.

Three safety details:

- **The panel id stays `"macro"`.** Only the title changes, to "Macro Monitor". Every saved
  layout and all three shipped presets keep resolving.
- **`restore()` must ignore a legacy `"tenors"` key silently.** Existing users have it in
  their saved layouts today. `settings()` stops writing it. Covered by a test using a real
  pre-migration settings dict.
- **`TENOR_ENTRIES` stays.** Verified: `components/symbol_search.py:88` uses it for the
  symbol completer, independently of `macro.py`.

`_update_spread`'s logic — shortest tenor vs nearest-10y, coloured by sign — is promoted
rather than deleted: it becomes the 2s10s analytic in the curves panel, with the added
refusal rule from §4.4.

Requires a release-note line and an F1 guide update, since a visible feature is going away.

---

## 7. Licence constraints as functional requirements

Both of these are conditions of use, not preferences. Each gets a test, because terms that
live only in a comment get broken eventually.

**BIS — attribution and free access.** BIS terms require citing BIS as source, and state
that inclusion in a commercial product "will not result in any additional charge to
subscribers or other users." Therefore:

- every payload carries a `sources` list, rendered in the panel header ("BIS · ECB ·
  U.S. Treasury"), with a test asserting the citation appears whenever BIS data is present;
- **BIS-sourced policy rates are free-tier and must never be gated.** Non-negotiable.

**FRED — third-party copyright.** FRED carries series whose commercial redistribution is not
permitted. They are identifiable: their notes contain "Copyright".

`tools/verify_rates.py` walks the candidate international series, pulls each one's notes via
`fred/series`, drops any that match, and writes `aurantium/rates_allowlist.py` — a generated
Python module holding the survivors and the date checked. At runtime `rates.py` fetches a
FRED series **only** if it appears there. Regenerating the allowlist joins the release-prep
checklist.

**It is a `.py` module, not a JSON data file, deliberately.** There is no `data/` directory
in `app/`; `aurantium.spec:26` bundles only `("layouts", "layouts")` and `.env.example`, so a
new data path would need a spec entry whose failure mode is silent — the FRED columns would
simply be blank forever in the frozen build. A module is bundled automatically by PyInstaller
and fails loudly if absent. `commodities_meta.py` sets the same precedent: a checked-in
Python table backed by a `tools/` verifier.

---

## 8. Tier

| Surface | Tier |
|---|---|
| Global Rates monitor — all jurisdictions, policy rate, last change, direction | Free |
| Sovereign Curves — overlays, history comparison, slope analytics | Pro |
| Anything BIS-sourced | Free, always (§7) |

**No enforcement code ships in this change.** The gating machinery is Plan B and does not
exist yet. `rates_meta.py` carries a `tier` marker so Plan B wires it up without redesign.

---

## 9. Testing

Suite is offscreen-Qt, no network (`tests/conftest.py`). `test_commodities_meta.py` is the
precedent for testing a curated table.

| File | Pins |
|---|---|
| `test_rates_meta.py` | Unique codes, `curve_source` is a known router key, citation present, tenors non-empty for non-`none` sources |
| `test_rates_provider.py` | Each fetch function against recorded fixtures — normalization, missing fields, malformed rows |
| `test_rates_policy_join.py` | BIS-only country; BIS+FRED; BIS down; FRED absent. `partial` populated; payload never empty when one source survives |
| `test_rates_context.py` | Group routing, unknown-code rejection, JSON round-trip, junk tolerance |
| `test_rates_allowlist.py` | Copyrighted series refused **before** any request; a series absent from the allowlist module fails closed |
| `test_macro_migration.py` | Pre-migration settings dict with `"tenors"` restores cleanly; panel id still `"macro"` |
| `test_rates_panels.py` | Citation renders with BIS data; sparse curves dashed with markers only at observed tenors; `none`-source shows empty state not error |

**Fixtures are recorded, not synthesized** — written by the task-1 probe into
`tests/fixtures/rates/`, so tests pin real upstream shapes and a future BIS reorganization
shows as a fixture diff rather than a mystery.

---

## 10. Risks and sequencing

**The endpoints are described from knowledge, not from today's live responses.** Shapes
drift; BIS has reorganized its API before. **Task 1 of the implementation plan is a
throwaway probe script** hitting all five sources and recording fixtures. If something has
moved, that surfaces before any panel code exists.

Other risks:

- `rates.py` lands around 400–500 lines (comparable to `fundamentals.py` at 578). If the
  probe reveals the sources need more normalization than expected, split to a subpackage
  early rather than letting it sprawl — the topic contract makes that free.
- The `macro.py` deletion is the change most likely to draw a user complaint on update.
  Release note and F1 update are mandatory, not optional.
- Frozen-build check: still run one. The allowlist is a module rather than a data file
  specifically to avoid the silent-bundling trap the preset workspaces hit (§7), but the two
  new panels must be confirmed present in the Panels menu of an **installed** build, not just
  from source — panel discovery behaves differently when frozen.

---

## 11. Out of scope

- **Tier enforcement** — Plan B. Markers only.
- **Economic calendar** (A3) and **commodities/macro analytics pack** (A4) — separate plans.
- **A "Rates Desk" preset workspace** — launch spec §3.4 names one, but preset workspaces are
  parked pending a decision (see `HANDOFF-2026-08-07-launch-work.md` §3). This change ships
  the panels; the preset waits.
- Inflation-linked and real yields, swap curves, credit spreads — roadmap.

**Documentation split to resolve separately:** `app/docs/superpowers/` is inside the git repo
and tracked; `Dashboard/docs/superpowers/` — holding the launch design, the preset-workspaces
plan and the preflight checklist — is outside `app/` and therefore version-controlled
nowhere. This spec is in the tracked tree. The August documents should be moved in.
