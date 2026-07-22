# Porting Aurantium to macOS — instructions for your Claude

Aurantium is a PySide6 (Qt) desktop financial terminal. It's Windows-shipped
today, but the codebase is already largely cross-platform: `aurantium.spec`
has a macOS `BUNDLE()` target, the app icon exists as `.icns`, all
Windows-only code paths (`AppUserModelID`, WinSparkle) are already guarded
behind `sys.platform` checks, and keyboard shortcuts are defined with
portable `QKeySequence` text/`StandardKey`, which Qt auto-remaps to Cmd on
macOS (e.g. `"Ctrl+W"` becomes Cmd+W automatically — nothing to translate
there). A macOS-native auto-updater (`aurantium/updater_mac.py`) and
mac-appropriate fonts have already been added.

Your job: **build it, run it, verify it, and be the first real test of the
auto-updater** — none of this has run on actual macOS hardware yet.

## 1. Get the code

```bash
git clone https://github.com/pedroivorocholi/aurantium.git
cd aurantium/app
```

Everything below happens inside `app/`.

## 2. Set up the environment and build

Follow `BUILD.md` § 1b exactly:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pyinstaller aurantium.spec --noconfirm --clean
```

Result: `dist/aurantium.app`. Since you built it locally from source (not
downloaded through a browser/Mail/AirDrop), it should launch without any
Gatekeeper "unidentified developer" prompt — no quarantine attribute gets
attached to files created on disk locally. If macOS *does* block the first
launch, right-click ▸ Open once (see BUILD.md's note on this), or run
`xattr -cr dist/aurantium.app`.

## 3. Smoke-test it like a real user

- Launch it. Confirm the window opens, theme renders correctly (amber-on-
  black, monospaced tabular numbers — check `aurantium/theme.py`, which now
  uses Helvetica Neue / Menlo on macOS instead of the Windows-only Segoe
  UI / Consolas. If the numbers don't look monospaced/aligned, that's the
  first thing to check).
- Open a few panels, confirm data loads (yfinance/gnews are keyless, should
  work out of the box).
- Try the cross-panel symbol linking (click a symbol in one panel, confirm
  linked panels re-center) — this is the core differentiator, worth
  explicitly confirming.
- Try shortcuts: Cmd+F (symbol search), Cmd+W (close panel), Cmd+Z (undo),
  Cmd+S (save layout), Cmd+Q (quit). All should already work via Qt's
  automatic Ctrl→Cmd remapping — flag it to me if any don't.
- Check the system tray icon (menu bar item) behaves reasonably — it's
  gated behind `QSystemTrayIcon.isSystemTrayAvailable()`, should just work,
  but the visual placement is obviously different from Windows.
- General pass: anything that looks like a leftover Windows assumption
  (odd fallback fonts, a menu item that doesn't fit the mac menu bar
  convention, a dialog that looks wrong) — fix it or flag it back to me.

## 4. Test the macOS auto-updater end-to-end

This is the part that most needs your hands-on verification — it's new code
that's only been unit-tested for its pure-Python parts (appcast parsing,
Ed25519 signature verification), never run against a real `.app` bundle.

Read `aurantium/updater_mac.py`'s module docstring and `RELEASING.md`
(§ "4b. macOS: add its own appcast item") for the full mechanism. Short
version: it reads the same `appcast.xml` the Windows WinSparkle path uses,
looking for the `<item>` whose `<enclosure sparkle:os="macos">`. On finding
a newer version, it downloads the zip, **independently re-verifies** the
Ed25519 signature against the public key already embedded in
`aurantium/updater.py` (`EDDSA_PUBLIC_KEY`), extracts it, and hands off to a
detached shell helper that waits for the app to fully quit before swapping
the `.app` bundle and reopening it — so it never touches files the running
process still has open.

To test the round trip:

1. Bump `aurantium/__init__.py`'s `__version__` down temporarily (e.g. to
   `"1.5.0"`) in your local build only — this simulates "an older version
   checking against a newer appcast entry." Don't commit this.
2. Build `dist/aurantium.app` per step 2.
3. Zip it: `cd dist && zip -r aurantium-mac.zip aurantium.app && cd ..`
4. Sign it: `.venv/bin/python tools/sign_update.py dist/aurantium-mac.zip`
   — this needs `tools/eddsa_private.key`, which is **not in git** (see
   RELEASING.md § one-time setup, step 4). Ask the repo owner (Pedro) to
   send you that file directly (e.g. over a private channel), or have him
   run the signing step himself and send you the printed `length:` /
   `sparkle:edSignature="…"` values.
5. Temporarily point `appcast.xml` at a **local file URL** or a throwaway
   public host for the zip (don't push a fake macOS release to the real
   `appcast.xml` on `main` — that would offer this test build to nobody
   yet, since no one else runs macOS today, but keep it clean anyway; use a
   scratch branch or just edit `appcast.xml` locally without committing).
   Add an `<item>` with `sparkle:os="macos"`, the `sparkle:version` you're
   testing against, the URL, `length`, and `sparkle:edSignature` from step 4.
6. Point `APPCAST_URL` in `aurantium/updater.py` at your local/test appcast
   (temporarily), or serve the edited `appcast.xml` locally
   (`python3 -m http.server` from the repo root works fine) and point
   `APPCAST_URL` at `http://127.0.0.1:8000/appcast.xml`.
7. Run `dist/aurantium.app`, use **Help ▸ Check for Updates…**. Confirm:
   it finds the "newer" version, prompts to install, and on accepting,
   quits and relaunches as the new build (check the About dialog shows the
   real version again, i.e. `1.5.3` or whatever `main` currently has —
   not your temporarily-lowered `1.5.0`).
8. Revert every temporary change (`__version__`, `appcast.xml`,
   `APPCAST_URL`) before committing anything.

If anything in that flow breaks — wrong bundle path detected, Gatekeeper
blocks the relaunch, the swap leaves a stray `aurantium.app.old` behind,
whatever — that's exactly the kind of bug only real macOS can surface. Fix
it in `aurantium/updater_mac.py` (it's a single self-contained file) or
report back what happened.

## 5. Send it back

Push your fixes as a branch and open a PR against
`https://github.com/pedroivorocholi/aurantium` (you'll need to fork it
first, or ask Pedro for collaborator access). Don't push directly to `main`.

In the PR description, note explicitly:
- What you tested (panels, shortcuts, tray icon, the updater round-trip).
- Anything you had to change and why (font substitutions, layout tweaks,
  anything Gatekeeper-related).
- Whether the auto-update swap-and-relaunch worked cleanly end-to-end.

## 6. Getting future updates (once this is merged and released)

Once this is merged, Pedro cuts a normal release per `RELEASING.md`
(§ "4b. macOS" covers the macOS-specific half — building the `.app`,
zipping it, signing it with the same key used for Windows, adding the
`sparkle:os="macos"` appcast item, and publishing both platform's assets
under one GitHub release). After that, your locally-built `aurantium.app`
checks for updates once a day automatically (silent, only prompts if it
finds something newer), and you can also trigger it manually any time via
**Help ▸ Check for Updates…**. No further action needed on your end — it's
the same appcast feed and signing key as the Windows build, just a
different (pure-Python) download-and-swap mechanism under the hood.
