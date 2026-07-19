# Indicator descriptions in the "+" add menu

Date: 2026-07-19
File touched: `findash/panels/chart.py`

## Problem

The "+" button in the chart panel's INDICATORS row opens a menu listing
"Add SMA…", "Add EMA…", "Add BB…", "Add VWAP", "Add VOL", "Add RSI…",
"Add MACD" (`_show_add_menu`, driven by `INDICATOR_SPECS`). The abbreviations
aren't self-explanatory to anyone unfamiliar with technical indicators —
there's no way to learn what "BB" or "VWAP" means without adding it first.

## Scope

Only the "+" INDICATORS menu (the 7 entries from `INDICATOR_SPECS`). The
right-click "Chart type" submenu (Candlesticks/OHLC bars/Line/Area) and
everything else are unaffected.

## Design

`_IndicatorSpec` (chart.py) gains a `description: str` field, populated for
every entry in `INDICATOR_SPECS`:

| kind | description |
|---|---|
| sma | Simple Moving Average — average closing price over the last N bars. |
| ema | Exponential Moving Average — like SMA, but weights recent bars more heavily. |
| bb | Bollinger Bands — a moving average with upper/lower bands at N standard deviations, showing volatility. |
| vwap | Volume Weighted Average Price — average price weighted by traded volume. |
| volume | Volume — number of shares traded per bar. |
| rsi | Relative Strength Index — momentum oscillator (0–100) showing overbought/oversold conditions. |
| macd | Moving Average Convergence Divergence — trend-following momentum indicator from the difference of two EMAs. |

In `_show_add_menu`, each `QAction`'s tooltip is set to `spec.description`,
and `menu.setToolTipsVisible(True)` is called on the `QMenu` before
`exec()`. This call is required — Qt's `QMenu` does not display action
tooltips on hover by default even when `QAction.setToolTip` is set; without
it the tooltips would silently never appear.

## Out of scope

- No change to the chart-type submenu, chip tooltips, or any other menu.
- No change to indicator behavior, math, or the color-on-add flow.

## Testing

Manual, in the running app (no automated test suite for this panel):

1. Click "+" in the INDICATORS row.
2. Hover each entry (Add SMA…, Add EMA…, Add BB…, Add VWAP, Add VOL, Add
   RSI…, Add MACD) and confirm a tooltip appears with the matching
   description from the table above, after the OS's normal tooltip hover
   delay.
3. Confirm the menu still functions normally otherwise — clicking an entry
   still opens the period dialog (where applicable) then the color picker
   (from the 2026-07-19 color-on-add feature), then adds the indicator.
