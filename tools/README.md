# tools/

Maintenance scripts. None of them ship in the app. Run each from the repo root,
e.g. `.venv/Scripts/python.exe tools/release/sign_update.py <file>`.

| Folder | Script | What it does |
|---|---|---|
| `release/` | `gen_keys.py` | One-time: generates the EdDSA update-signing key pair. |
| | `sign_update.py` | Signs an installer/zip for the appcast. |
| | `ci_update_appcast.py` | CI only: adds a release's `<item>`s to `appcast.xml`. |
| | `eddsa_private.key` | **Secret, gitignored, irreplaceable.** Signs every auto-update. Never regenerate it; back it up. |
| `codegen/` | `generate_presets.py` | Regenerates the shipped preset workspaces in `layouts/presets/`. |
| | `vendor_reicon.py` | Regenerates `aurantium/reicon_icons.py` (the vendored icon glyphs). |
| `data/` | `verify_rates.py` | Checks the rates sources live and regenerates `aurantium/rates_allowlist.py`. |
| | `probe_rates.py` | Records upstream rates responses into `tests/fixtures/rates/`. |
| | `verify_markets.py` | Before adding a commodity: checks its Yahoo contract symbols quote and its CFTC market name matches exactly one family. |
| `design/` | | Local design-audit tooling (kept out of git). |

Release steps: `docs/guides/RELEASING.md`.
