# PANELS.md — customize your own findash

findash is a personal, Bloomberg-style market dashboard designed to be forked
and customized **without touching core code**. This file is the complete guide.

## Get your own copy running

```
git clone <your fork>
cd app
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt     # macOS/Linux
copy .env.example .env    # optional — only needed for keyed providers
.venv\Scripts\python -m findash
```

No API keys are required: quotes, charts, news, and analyst data come from
free, keyless sources (Yahoo Finance via `yfinance`, Google News via `gnews`).
Optional keys in `.env` unlock extras: `FRED_API_KEY` (US macro series),
`EIA_API_KEY` (energy spot prices), `NEWSAPI_KEY` (better news source).

## The one concept: topics

Panels never fetch data. They subscribe to **topics** on the DataHub bus and
providers keep the topics fresh (TTL-based polling, shared cache, rate limits):

| Topic | Payload | Freshness |
|---|---|---|
| `quote:AAPL` | `{symbol, name, price, change, change_pct, prev_close, volume, currency, day_high, day_low}` | 30 s |
| `history:AAPL:6mo:1d` | `{symbol, period, interval, t:[epoch_s], o,h,l,c,v:[…]}` | 30 min |
| `analyst:AAPL` | `{target_high/low/mean, recommendation_mean, recommendation_key, analyst_count, upgrades:[…]}` | 1 h |
| `news:AAPL` | `[{title, publisher, url, published}, …]` | 5 min |
| `fred:DGS10` | `{id, title, units, points:[[date, value], …]}` (needs FRED_API_KEY) | 1 h |
| `wb:US:FP.CPI.TOTL.ZG` | World Bank indicator series (keyless) | 1 h |
| `wbc:GOLD` | Commodity quote+trend (GOLD, WTI, BRENT, COPPER, SILVER, NATGAS) | 1 h |
| `cftc:gold` | COT positioning `{commercial_net, noncommercial_net, open_interest, bias}` (keyless) | 1 h |
| `eia:spot:wti` | Energy spot price series (needs EIA_API_KEY) | 1 h |
| `newsq:any free text` | Keyword news feed (e.g. `newsq:Brazil`) — same payload as `news:` | 5 min |
| `profile:AAPL` | Company profile `{name, description, sector, industry, market_cap, pe_trailing/forward, eps, dividend_yield, beta, week52_high/low, employees, website, officers}` | 24 h |
| `financials:AAPL` | Income/balance/cashflow statements, annual+quarterly: `{income|balance|cashflow: {annual|quarterly: {columns, rows}}}` | 24 h |
| `earnings:AAPL` | `{next_date, rows: [[date, eps_est, eps_reported, surprise_pct]]}` | 6 h |
| `dividends:AAPL` | `{yield_pct, rate, ex_date, payout_ratio, history, splits}` | 6 h |
| `holders:AAPL` | `{insiders_pct, institutions_pct, top: [[holder, shares, pct, value]]}` | 24 h |
| `options:AAPL` / `options:AAPL:2026-08-21` | Option chain near ATM: `{spot, expiry, expiries, calls, puts}` | 2 min |
| `movers:gainers` (`losers`, `actives`) | `[[symbol, name, price, chg_pct, volume]]` from Yahoo's screener | 2 min |

Any symbol Yahoo Finance knows works: stocks (`AAPL`), ETFs (`SPY`), futures
(`ES=F`, `GC=F`), crypto (`BTC-USD`), indices (`^GSPC`), FX (`EURUSD=X`).

## Panel linking (the Bloomberg trick)

A global `SymbolContext` holds the **active symbol per link group** (A–D).
**Every panel starts in group A**, so clicking a ticker in any panel — a
watchlist row, the command bar — updates every open panel instantly. The
colored badge in each panel's header switches its group (or unlinks it):
run two groups side-by-side to compare two securities.

## Built-in panels

All panels are in the Panels ▸ Add Panel menu; open as many instances of each
as you like (e.g. two Topic News feeds with different queries):

| Panel | What it shows | In-panel customization |
|---|---|---|
| Watchlist | Live quote table; rows drive linked panels | Add/Remove any Yahoo symbols; list persists per instance |
| Chart | Candlesticks + SMA 50/100/200 overlays + RSI(14) sub-chart | Period buttons (1d…max), indicator toggles; all persist |
| News | Company headlines for the linked symbol | — (follows link group) |
| Topic News | Keyword news feed, independent of linked symbol | Editable query (e.g. "Brazil", "energy commodities") |
| Analyst Recs | Consensus, targets, upgrades/downgrades | — (follows link group) |
| Company Profile | Bloomberg-DES-style: description, sector, key stats, officers | — (follows link group) |
| Chart Grid | Grid of mini index/commodity charts; click a cell to drive linked panels | Comma-separated symbol list |
| Commodities | GLCO-style energy + metals monitor | "Edit…" dialog for both sections |
| Example: Macro Snapshot | CFTC positioning + commodity trends (tutorial file) | copy it to make your own |
| Financials | Bloomberg-FA-style statements table | Income/Balance/Cash Flow × Annual/Quarterly toggles |
| Earnings | Next earnings date + EPS est vs actual history | — (follows link group) |
| Dividends | Yield, rate, ex-date, payout + dividend/split history | — (follows link group) |
| Holders | Insider/institution % + top institutional holders | — (follows link group) |
| Options Chain | Calls/puts around ATM with IV, volume, OI | Expiry dropdown |
| World Indices | Americas/Europe/Asia index monitor (WEI-style) | Edit… dialog per region |
| FX Monitor | Major/other currency pairs + DXY + crypto | Edit… dialog per section |
| Sector Heatmap | 11 S&P sector ETFs as color-coded tiles | Editable tile list |
| Performance | Relative % return chart: linked symbol vs SPY/QQQ/… | Comparison list + period buttons |
| Portfolio | Your positions with live P&L and totals | Add/remove positions; persists |
| Market Movers | Top gainers / losers / most active | Category buttons |
| Macro / Rates | US yield curve, 10Y–3M inversion spread, CFTC positioning | — |

Two ready-made layouts ship in `layouts/`: `default.json` (loaded at startup)
and `bloomberg.json` — a 10-panel Bloomberg-Launchpad-style arrangement
(Brazil news / company news / analyst column, big chart + FX chart center,
chart grid + rates/FX monitor, commodities + energy news + profile column).
Rearrange anything by dragging, then File ▸ Save Layout to keep it.

## Write your own panel (one file, ~40 lines)

Drop a file into `user_panels/`, restart the app, and it appears under
Panels ▸ Add Panel. Start by copying `user_panels/example_macro.py`.

```python
from findash.panel import Panel, register_panel
from PySide6.QtWidgets import QLabel

@register_panel(id="spread", title="10Y Spread", category="Custom")
class SpreadPanel(Panel):
    def build(self):
        self.lbl = QLabel("waiting for symbol…")
        self.content_layout.addWidget(self.lbl)

    def on_symbol(self, sym):              # linked symbol changed
        self.unsubscribe_all()
        self.subscribe(f"quote:{sym}", self.on_quote)

    def on_quote(self, q):                 # topic delivery (GUI thread)
        self.lbl.setText(f"{q.get('symbol')}: {q.get('price')}")
```

The whole API surface:

- `build(self)` — create widgets into `self.content_layout`. Called once.
- `on_symbol(self, symbol)` — the active symbol of this panel's link group
  changed. Re-subscribe to the topics you care about.
- `self.subscribe(topic, callback, on_error=None)` — receive the cached value
  immediately (if any) plus every future update. Cleanup is automatic when
  the panel closes.
- `self.unsubscribe_all()` — drop old subscriptions (call at the top of
  `on_symbol` if your topics are per-symbol).
- `self.set_symbol("MSFT")` — publish a user click to the panel's link group
  (this is what makes *your* panel drive all the others).
- `self.set_status("…")` — small status text in the panel header. Provider
  errors show up there automatically.
- `settings() -> dict` / `restore(dict)` — persist panel state into layouts.

Rules of thumb: never do network I/O in a panel (add a provider instead);
guard every payload field (`q.get(...)` — values can be `None`); callbacks
run on the GUI thread, so just update widgets directly.

## Write your own data provider

Providers live in `findash/providers/`. Implement two methods and register:

```python
from findash.datahub import DataHub, Provider, TopicPolicy

class MyProvider(Provider):
    def topic_patterns(self):
        return ["myapi:*"]

    def refresh(self, topics):
        hub = DataHub.instance()
        for t in topics:
            hub.run_async(lambda t=t: self._fetch(t))   # never block refresh()

    def _fetch(self, topic):
        hub = DataHub.instance()
        try:
            hub.publish(topic, {"value": 42})            # thread-safe
        except Exception as exc:
            hub.publish_error(topic, str(exc))
```

Register it in `findash/providers/__init__.py` (`register_all_providers`),
with a policy: `hub.set_policy("myapi:*", TopicPolicy(ttl_s=600))`.
API keys: read from `os.environ` only, document them in `.env.example` —
never hardcode secrets.

## Layouts

File ▸ Save Layout writes a JSON file capturing: which panels are open (and
their per-panel settings), the full dock arrangement, each panel's link
group, and the active symbol per group. Save as `layouts/default.json` to
make it your startup layout. Layout files are plain JSON — share them along
with your custom panel files and someone else gets your exact terminal.

## Project map

```
app/
  findash/
    datahub.py         # topic bus: subscribe/publish, TTL scheduler, cache
    symbol_context.py  # link groups (the cross-panel click propagation)
    panel.py           # Panel base class + registry + discovery  ← your API
    app.py             # main window, docking, menus, layout save/load
    theme.py           # colors/stylesheet
    providers/         # market (yfinance), news (gnews), econ (FRED/WB/CFTC/EIA)
    panels/            # built-in: watchlist, chart, news, analyst
  user_panels/         # ← your panels go here (auto-discovered)
  layouts/             # ← saved layouts (default.json auto-loads)
```

Core (`findash/`) vs. yours (`user_panels/`, `layouts/`, `.env`): keeping your
work in the latter means pulling upstream updates never conflicts.
