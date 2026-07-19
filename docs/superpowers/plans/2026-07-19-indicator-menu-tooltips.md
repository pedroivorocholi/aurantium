# Indicator Menu Tooltips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hovering an entry in the chart panel's "+" indicator-add menu (Add SMA…, Add EMA…, etc.) shows a tooltip with the indicator's full name and a one-line description.

**Architecture:** `_IndicatorSpec` (chart.py) gains a `description: str` field, populated once per entry in `INDICATOR_SPECS`. `_show_add_menu` sets each `QAction`'s tooltip from it and enables tooltip display on the `QMenu`.

**Tech Stack:** Python, PySide6 (`QAction.setToolTip`, `QMenu.setToolTipsVisible`). No test framework in this repo — verified by running the app (`.venv\Scripts\python -m findash`) and by a headless (`QT_QPA_PLATFORM=offscreen`) script asserting on the real `QAction` objects, same approach used for the two prior chart.py features in this project's history.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-indicator-menu-tooltips-design.md`
- Scope is the "+" INDICATORS menu only (`_show_add_menu`, chart.py) — no other menu changes.
- Descriptions must exactly match the spec's table (see Task 1, Step 1).

---

### Task 1: Add descriptions to `_IndicatorSpec` and wire up the menu tooltips

**Files:**
- Modify: `findash/panels/chart.py:159-183` (`_IndicatorSpec` class + `INDICATOR_SPECS` dict)
- Modify: `findash/panels/chart.py:647-654` (`_show_add_menu`)

**Interfaces:**
- Consumes: nothing new from other tasks (this is the only task).
- Produces: `_IndicatorSpec.description: str`, readable by any future code the same way `spec.label` already is.

- [ ] **Step 1: Add the `description` field to `_IndicatorSpec` and populate it**

Replace the current `_IndicatorSpec` class and `INDICATOR_SPECS` dict (chart.py:159-183):

```python
class _IndicatorSpec:
    def __init__(
        self,
        kind: str,
        label: str,
        pane: str,
        default_window: Optional[int],
        lookback: Callable[[dict], int],
        description: str,
    ) -> None:
        self.kind = kind
        self.label = label
        self.pane = pane
        self.default_window = default_window  # None = no window parameter
        self.lookback = lookback  # bars of history the math needs to warm up
        self.description = description  # shown as a tooltip in the add menu


INDICATOR_SPECS: dict[str, _IndicatorSpec] = {
    "sma": _IndicatorSpec(
        "sma", "SMA", "price", 50, lambda p: p.get("window", 50),
        "Simple Moving Average — average closing price over the last N bars.",
    ),
    "ema": _IndicatorSpec(
        "ema", "EMA", "price", 21, lambda p: p.get("window", 21) * 3,
        "Exponential Moving Average — like SMA, but weights recent bars more heavily.",
    ),
    "bb": _IndicatorSpec(
        "bb", "BB", "price", 20, lambda p: p.get("window", 20),
        "Bollinger Bands — a moving average with upper/lower bands at N standard"
        " deviations, showing volatility.",
    ),
    "vwap": _IndicatorSpec(
        "vwap", "VWAP", "price", None, lambda p: 0,
        "Volume Weighted Average Price — average price weighted by traded volume.",
    ),
    "volume": _IndicatorSpec(
        "volume", "VOL", "osc", None, lambda p: 0,
        "Volume — number of shares traded per bar.",
    ),
    "rsi": _IndicatorSpec(
        "rsi", "RSI", "osc", 14, lambda p: p.get("window", 14) + 1,
        "Relative Strength Index — momentum oscillator (0–100) showing"
        " overbought/oversold conditions.",
    ),
    "macd": _IndicatorSpec(
        "macd", "MACD", "osc", None, lambda p: 26 + 9,
        "Moving Average Convergence Divergence — trend-following momentum"
        " indicator from the difference of two EMAs.",
    ),
}
```

- [ ] **Step 2: Set tooltips on the menu actions**

Replace the current `_show_add_menu` (chart.py:647-654):

```python
    def _show_add_menu(self) -> None:
        menu = QMenu(self)
        for kind, spec in INDICATOR_SPECS.items():
            text = f"Add {spec.label}…" if spec.default_window else f"Add {spec.label}"
            act = QAction(text, menu)
            act.triggered.connect(lambda _=False, k=kind: self._add_indicator_ui(k))
            menu.addAction(act)
        menu.exec(self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))
```

with:

```python
    def _show_add_menu(self) -> None:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for kind, spec in INDICATOR_SPECS.items():
            text = f"Add {spec.label}…" if spec.default_window else f"Add {spec.label}"
            act = QAction(text, menu)
            act.setToolTip(spec.description)
            act.triggered.connect(lambda _=False, k=kind: self._add_indicator_ui(k))
            menu.addAction(act)
        menu.exec(self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))
```

`menu.setToolTipsVisible(True)` is required — Qt's `QMenu` does not display
action tooltips on hover by default even when `QAction.setToolTip` is set.

- [ ] **Step 3: Headless verification**

Run this from `app/` (adjust the path if your shell differs):

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -c "
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from findash.panels.chart import ChartPanel, INDICATOR_SPECS

p = ChartPanel(); p.build()

expected = {
    'sma': 'Simple Moving Average',
    'ema': 'Exponential Moving Average',
    'bb': 'Bollinger Bands',
    'vwap': 'Volume Weighted Average Price',
    'volume': 'Volume',
    'rsi': 'Relative Strength Index',
    'macd': 'Moving Average Convergence Divergence',
}
for kind, starts_with in expected.items():
    spec = INDICATOR_SPECS[kind]
    assert spec.description.startswith(starts_with), (kind, spec.description)
print('OK: all 7 descriptions present and correctly worded')
"
```

Run: `.venv\Scripts\python -m findash`

In the running app, open the Chart panel, click "+" in the INDICATORS row, and hover each of the 7 entries (Add SMA…, Add EMA…, Add BB…, Add VWAP, Add VOL, Add RSI…, Add MACD). Confirm a tooltip appears for each, after the OS's normal hover delay, matching the description table in the spec. Confirm clicking an entry still works exactly as before (period dialog where applicable, then the color picker, then the indicator is added).

Expected: the script prints `OK: all 7 descriptions present and correctly worded`; the manual hover check shows all 7 tooltips; adding an indicator still works.

- [ ] **Step 4: Commit**

```bash
git add findash/panels/chart.py
git commit -m "feat: show indicator descriptions as tooltips in the add menu"
```

---

## Self-Review Notes

- **Spec coverage:** The spec's only requirement — 7 descriptions wired to menu-item tooltips, tooltips visible on the QMenu — is fully covered by Task 1's two edits. "Out of scope" items (chart-type submenu, chip tooltips, indicator behavior/math, color-on-add flow) are untouched — no other method is modified.
- **Placeholder scan:** No TBD/TODO; both replacement code blocks are complete and copy-pasteable; the verification script and manual steps are concrete.
- **Type consistency:** `_IndicatorSpec.__init__` gains `description: str` as its last positional parameter; every one of the 7 `INDICATOR_SPECS` construction calls in Step 1 passes it positionally in the same order. `spec.description` in `_show_add_menu` (Step 2) matches the attribute name set in `__init__` (Step 1: `self.description = description`).
