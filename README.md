# findash

A personal, Bloomberg-style market terminal for your desktop — free data,
linked panels, and a plugin model that lets anyone fork it and build their
own panels without touching core code.

- **Linked panels by default**: click a ticker anywhere (watchlist, command
  bar) and the chart, news, analyst recs, and every other open panel follow
  instantly — Bloomberg Launchpad "link groups", but on out of the box.
- **Free data, no signup**: Yahoo Finance (quotes, charts, fundamentals,
  analyst recs) + Google News. Optional free keys unlock FRED macro data and
  EIA energy prices.
- **Yours to reshape**: panels dock, split, float, and tear off to other
  monitors; layouts save/load as JSON; new panels are single Python files
  dropped into `user_panels/`.

## Run it

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m findash
```

Optional: `copy .env.example .env` and add free API keys (FRED, EIA, NewsAPI).

Or double-click: `findash.bat` (or a desktop shortcut pointing at
`.venv\Scripts\pythonw.exe -m findash` with `findash.ico`) launches the app
without a console window.

## Ship it as a standalone app

Build a single `findash.exe` (no Python needed) — or a full `Setup.exe`
installer — to hand to anyone on Windows. See **[BUILD.md](BUILD.md)**.

## Arrange it

Drag the borders between panels to resize, drag a title bar to move/split/tab a
panel, double-click a title bar to float it, or pin a panel to a window edge.
Saved arrangements are managed in-app under the **Layout** menu — *Save Current
Layout…* names a snapshot, and your last arrangement is restored automatically on
next launch. Layouts are stored per-user (no folder to manage); *File ▸ Export
Layout* shares one as a file.

## Customize it

See **[PANELS.md](PANELS.md)** — how topics work, the 5-method panel API, a
copy-paste example panel, custom providers, and layout sharing.

## Design lineage

The architecture adapts the best ideas found in
[Fincept Terminal](https://github.com/Fincept-Corporation/FinceptTerminal)
(DataHub topic bus, TTL refresh policies, link groups) with one deliberate
inversion: linking is **on by default**. Research notes live in
`../research/`. Local desktop app only: no auth, no telemetry, no cloud.
