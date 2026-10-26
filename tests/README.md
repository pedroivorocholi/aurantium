# tests/

Run everything from the repo root: `pytest tests/ -q` (Qt runs offscreen; see
`conftest.py`). Each folder groups one area of the app; file names stay unique
across folders, so `pytest -k <name>` and `tests/<area>/test_<name>.py` both work.

| Folder | Covers |
|---|---|
| `charts/` | Price chart behaviour (zoom, log scale, compare, export, events lane), plot maths, and every palette (`palette_checks.py` is their shared validator). |
| `panels/` | Individual panels (Company, Day Brief, options, portfolio) and the panel contract: registry, quick guides, header, responsive layout, shared state vocabulary, panel windows. |
| `components/` | Shared widgets and helpers: tables (fit width, responsive columns, smooth scroll, empty state), number formatting, list editor, symbol search, notifier, glossary. |
| `ui/` | App chrome and feel: theme, type scale, depth, icons, focus ring, press feedback, motion, transitions, popups, splash, title bar. |
| `data/` | Providers and curated metadata: rates (FRED allowlist, policy join, parsers), commodities, symbol catalog, news relevance and language gate, Day Brief maths. |
| `linking/` | The cross-panel linking contexts: `SymbolContext`, `DateContext`, `RatesContext`. |
| `workspaces/` | Startup, preset workspaces (shipped, arrangement, chooser) and the Workspaces menu. |
| `fixtures/` | Recorded upstream responses (`rates/`, written by `tools/data/probe_rates.py`). |

Every new panel needs a quick guide: `panels/test_panel_guides.py` enforces it.
