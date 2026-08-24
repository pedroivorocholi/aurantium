# Day Brief — Design

**Date:** 2026-08-20
**Status:** Implemented
**Plan:** `~/.claude/plans/add-a-news-and-whimsical-riddle.md`

---

## 1. What this is

A panel that answers "why did this stock move on that day?", plus the chart
integration that makes the question askable without leaving the terminal.

Aurantium could always show *that* a stock moved. It could not say *why* — you
left for a browser and lost the workspace. Day Brief closes that.

For a symbol and an anchor date it shows the session's OHLCV, its volume against
its own 30-day average, the same session's move for a broad index and a sector
proxy, the **EXCESS** between stock and index, the corporate events that landed
in a window around the date, and the headlines from that window.

**EXCESS is the line that earns the feature.** Stock move minus benchmark move
answers "was it this company, or was it everything?" — the one question a price
chart can never answer on its own. Verified against Meta on 2022-02-03: the
stock −26.39%, the S&P −2.44%, excess −23.95%, verdict *"stock-specific, not the
market"*.

---

## 2. Decisions fixed before implementation

| Decision | Value |
|---|---|
| Surface | New dockable panel `day_brief` + chart integration. Not a popup, not a mode in News. |
| Depth | Full explainer: OHLCV, volume ratio, index **and** sector comparison, EXCESS, events, news |
| Chart marks | Event glyphs **plus** an unusual-move flag (2.5σ) |
| Date linking | New `DateContext` bus mirroring `rates_context.py` |
| Gesture | **No plain click, no double-click on the plot.** See §4. |
| Interval | An adjustable window around an anchor date, not a free range |
| News source | `gnews` with `start_date`/`end_date`. No new dependency, no API key. |

### 2.1 Why gnews, and why this was not a risk

`gnews` 0.8.2 was already a dependency and already accepted `start_date` /
`end_date`, which it translates into Google News `after:`/`before:` operators
(`gnews.py:93-109`). `providers/news.py:415-466` simply never passed them.

This was verified before any code was written:

| Window | Top result |
|---|---|
| 2022-02-02 → 02-05 | *"Facebook stock plummets 26% in its biggest one-day drop ever"* — CNBC |
| 2019-01-02 → 01-05 | *"Apple's shock downgrade rattles global stock markets"* — The Guardian |

Seven years of reach, 8/8 results per window, no key.

### 2.2 Why a separate `DateContext`

The same argument `rates_context.py:1-13` already makes. `SymbolContext` carries
free-text tickers and every panel joins group "A" by default with no type
discrimination; publishing a date there would make the chart, news, fundamentals
and options panels all try to load it as a ticker.

`DateContext` carries an **anchor plus a window token**, not a free range. The
stat block describes the anchor session; events and news sweep the window. That
split is what lets "widen until you catch the story" work without a second date
control fighting the first.

### 2.3 Why the marks are the click target

An action that opens or re-centres a panel must not fire by accident, and a plot
is a surface people click while reading. `chart.py:1660-1662` shows a plain
left-click is currently inert, so binding it was technically free — and still
wrong.

Instead the click target is the glyph layer. Marks exist *only* on days that
have something to explain, so aiming at one is deliberate by construction, and
seeing them is how the feature teaches itself.

---

## 3. Data topics

Three topics, each a single-segment prefix (DataHub's O(1) policy path), each
keyed by a string that caches to SQLite for free.

| Topic | Payload | Provider |
|---|---|---|
| `daystat:SYM:YYYY-MM-DD` | `{date, snapped_from, o,h,l,c,v, pct, avg_vol_30, vol_ratio, bench_sym, bench_pct, sector_sym, sector_pct, excess_pct, verdict}` | `providers/dayinfo.py` |
| `dayev:SYM:START..END` | `{events:[{date, kind, title, detail}]}` | `providers/dayinfo.py` |
| `newsr:SYM:START..END` | `{symbol, start, end, items:[…], found, hidden}` | `providers/news.py` |

All three at `ttl_s=21600`. Long TTLs are unusually safe here: **a past trading
day is immutable**, so a revisit is instant and works offline.

The panel subscribes to three topics and renders three blocks. The cross-source
joining stays in the provider, per the rule set in
`2026-08-07-global-rates-design.md` §2.2.

### 3.1 Benchmark and sector

`sector_meta.py` maps the eleven yfinance sector strings to SPDR Select Sector
ETFs, and listing suffixes to local indices (`.SA`→`^BVSP`, `.DE`→`^GDAXI`, …).
Both maps **fail closed**: an unknown sector or suffix yields `None` and the line
is omitted. A silently wrong EXCESS is worse than a missing one. Sector proxies
are US-only, because pairing XLK with a Frankfurt listing would produce a number
that looks meaningful and isn't.

Verified: `PETR4.SA` benchmarks against `^BVSP` with no sector line.

### 3.2 Ranged news waterfall

Deliberately **not** the live order. gnews first (the only tier with archive
depth), NewsAPI second but only inside its ~1-month free-tier lookback, RSS as a
recency top-up post-filtered on the parsed publish date.

**yfinance is absent entirely.** `Ticker.news` has no date control, so including
it would silently return today's headlines for a 2019 query — the worst failure
this feature could have. Items with an unparseable date are excluded for the same
reason.

`_gate` stays last: filter, then cap.

---

## 4. Gestures

| Gesture | Where |
|---|---|
| Click a mark in the EVENTS lane | `_events_lane.py` `EventsLane._clicked` |
| Right-click the chart → *"What happened on Tue 14 Mar 2026?"* | `chart.py` `_build_chart_menu` |
| `D` with the crosshair on a bar | `chart.py` `_install_day_shortcut` |
| `/day 2026-03-14`, `/day A..B`, bare `AAPL 2026-03-14` | `app.py` `_set_as_of`, `_as_of_spec` |
| In-panel: date field, `◀ ▶` stepping, `1D/3D/1W/1M` chips | `panels/day_brief.py` |

`D` is scoped `WidgetWithChildrenShortcut`, so it never steals a keystroke from
the command bar. The context-menu label is built from the bar under the cursor,
captured before the menu grabs the mouse.

---

## 5. Implementation notes that differ from the plan

Four things changed once the code met the codebase. Recorded because each was a
real constraint, not a preference.

1. **`dayinfo.py` fetches yfinance directly** rather than routing through
   `history:` topics. The `Provider` contract (`datahub.py:34-46`) is
   publish-only — providers do not consume each other's topics, and no
   mechanism exists for it. The 429 risk the plan was guarding against is
   handled instead by `_HIST_MEMO`, an in-process memo for benchmark and sector
   series, which are the ones repeated across every symbol in a session.

2. **The lane lives in `panels/_events_lane.py`**, not inside `chart.py`.
   `chart.py` was already 2086 lines; adding a glyph layer to it would have made
   the worst file worse.

3. **Geometric marks use pyqtgraph's built-in symbols**, not Unicode glyphs.
   `QPainterPath.addText` does **no font substitution**, so a monospace face
   without U+25B2 would draw nothing at all. Letters (E/D/S) still come from the
   font — every monospace face has A–Z. Qt's ordinary text rendering *does*
   substitute, which is why the same characters are safe in the panel's event
   list.

4. **The lane marks rating *changes* only.** yfinance's
   `upgrades_downgrades` includes reiterations and maintained ratings; Meta on
   2022-02-03 returned nineteen rows, most of them not changes. Marking them all
   would break the "marks appear only where there's something to see" guarantee.
   The Day Brief still lists them all, with the action spelled out
   (`ACTION_WORDS`) so a reiteration never reads as an upgrade.

---

## 5a. Fixed after first live use (2026-08-24)

Two things the test suite could not have caught, both found by actually using
the app.

**The chart gestures opened nothing.** `_pick_date` published to `DateContext`
and stopped there; only `/day` (`app.py::_set_as_of`) created a panel. On a
workspace with no Day Brief docked — which is *every* workspace saved before
this panel existed — clicking a mark, pressing `D` and the context-menu entry
all produced a toast and nothing else. The feature's three primary gestures
were dead on arrival.

Root cause: "ensure the panel exists" lived in one of the two code paths. Fixed
by extracting `MainWindow.open_day_brief(anchor, window, group, source)` and
routing both through it. The opened panel joins the *originating chart's* link
group, so a brief opened from a group-B chart follows that chart. Four
regression tests in `test_chart_events_lane.py` cover it.

This bug also made the second one look worse than it was: marks that do nothing
when clicked read as pure clutter.

**The marks were too heavy.** 13px glyphs in a 30px lane, next to 11px chart
text, made the lane compete with the price it annotates. Reduced to
`MARK_SIZE = 8` / `HOVER_SIZE = 11` / `LANE_HEIGHT = 20`, caption 8pt→7pt, and
the three are now named constants at the top of `_events_lane.py` so the next
adjustment is a one-line edit. The lane is a footnote to the price, not a second
chart.

**It opened by squeezing the workspace.** `open_day_brief` docked the new
panel to the right, which took width from every panel the user actually keeps
open and forced sideways scrolling in them — to show something that gets read
once and closed.

A new Day Brief now opens as its own window, parked against the right edge of
the screen (not centred: it opens in response to a chart click and must not
land on top of the chart being asked about), clamped to the available geometry.
A Day Brief the user has deliberately docked is left where it is and simply
re-centred.

This is a deliberate, single exception to aurantium's never-float rule, and it
is expressed as `Panel.FLOATABLE` — a class attribute, not an `add_panel()`
argument. That matters for more than tidiness: the dock feature bits have to be
identical when a saved layout is restored, and the layout spec carries no such
flag, so a call-site flag would have silently lost floating on every restart.
Verified: floating state, the feature bit and the anchor all survive a layout
round-trip. The panel stays movable and floatable, so dragging it into the
workspace docks it for good.

**The window chips lied about their own size.** The token was stored as a
*radius*, so a chip labelled `1W` resolved to fifteen days and `1M` to
sixty-one — and the panel printed that count directly beneath the chip, so the
label and the number visibly contradicted each other.

`WINDOW_SPAN_DAYS` now stores the **total** span (1 / 3 / 7 / 31, odd so the
anchor sits exactly in the middle) and `window_radius()` derives the reach.
`DEFAULT_WINDOW` moves from `1D` to `3D`: the original reason for a minimum
radius of one — weekend news explaining a Monday gap — was sound, but it belongs
in the default, not smuggled into what "1D" means. `1D` is now a strict single
session. Parametrised tests assert, for every token, that the resolved span
equals the number on the chip and that the anchor is centred.

---

## 6. Failure modes and what happens

| Case | Behaviour | Verified |
|---|---|---|
| Weekend / holiday | Snaps to the prior session and says *"2026-08-15 was not a session — showing 2026-08-14"* | yes, live |
| Pre-IPO / no data | `{empty: true, reason}`, panel shows the reason | yes, live |
| Non-US listing | Local index, sector line omitted | yes, live (`PETR4.SA` → `^BVSP`) |
| Unknown suffix | No benchmark, no EXCESS, line says so | unit test |
| Language gate empties the window | *"N headlines hidden by your reading languages"* + route to Settings | unit test |
| No headlines at all | *"No headlines for … — try a wider window"* | unit test |
| yfinance 429 | `RATE_LIMIT_GATE` short-circuits, standard message | shared path |
| Junk in the layout file | `from_json` / `restore` guard shape, never raise | unit test |

---

## 7. Files

**New:** `date_context.py` · `sector_meta.py` · `providers/dayinfo.py` ·
`panels/day_brief.py` · `panels/_events_lane.py`

**Changed:** `providers/news.py` (ranged waterfall) · `providers/__init__.py`
(register + policies) · `panels/__init__.py` (`BUILTIN`) · `panels/chart.py`
(lane, menu, `D`, publish) · `app.py` (`/day`, bare `SYM DATE`, layout
persistence) · `onboarding_dialog.py` (F1) · `PANELS.md` · `aurantium.spec`

**Tests:** `test_date_context.py` · `test_day_brief.py` ·
`test_day_brief_panel.py` · `test_chart_events_lane.py` — 119 new, 414 total,
all passing.

---

## 8. F1

Both tabs carry it, per the project rule that a shortcut absent from the F1
sheet does not exist: a *"Day Brief — why did it move?"* table in the shortcuts
tab (lane click, `D`, right-click, `/day`, bare `SYM DATE`, lane toggle) and a
section in the guide explaining the glyph vocabulary, the EXCESS line, the
window chips, and the snapping notice.
