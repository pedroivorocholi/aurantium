# Building & verifying Aurantium on macOS — instructions for your Claude

Aurantium is a PySide6 (Qt) desktop financial terminal. It's Windows-shipped
today, but **the macOS port itself is already done and on `main`** —
`packaging/aurantium.spec` has a macOS `BUNDLE()` target, the app icon exists as
`.icns`, all Windows-only code paths (`AppUserModelID`, WinSparkle) are
already guarded behind `sys.platform` checks, keyboard shortcuts are defined
with portable `QKeySequence` text/`StandardKey` (Qt auto-remaps `"Ctrl+W"` to
Cmd+W on macOS — nothing to translate there), and a macOS-native auto-updater
(`aurantium/updater_mac.py`) plus mac-appropriate fonts have been added.

There is no separate "mac codebase" to write or merge — it's one shared
source tree. **Your job is QA, not porting**: build it, run it, and be the
first real test of the auto-updater, since none of this has run on actual
macOS hardware yet. Only if you hit an actual bug does anything need to go
back into the repo.

You've been added as a **collaborator** (write access) on
`pedroivorocholi/aurantium` — accept the invite at
https://github.com/pedroivorocholi/aurantium/invitations if you haven't.
That means you can push branches directly; no fork needed.

## 1. Get the code

```bash
git clone https://github.com/pedroivorocholi/aurantium.git
cd aurantium
```

Everything below happens at the repo root (there's no nested `app/` folder —
the files `BUILD.md`, `RELEASING.md`, `packaging/aurantium.spec` etc. describe live
right here).

## 2. Set up the environment and build

Follow `BUILD.md` § 1b exactly:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pyinstaller packaging/aurantium.spec --noconfirm --clean
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
4. Sign it: `.venv/bin/python tools/release/sign_update.py dist/aurantium-mac.zip`
   — this needs `tools/release/eddsa_private.key`, which is **not in git** (see
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

## 5. Send it back (only if you changed anything)

If everything in steps 3-4 worked with zero code changes, there's nothing to
send back — you're done, and you'll get real auto-updates per step 6 below.

If you *did* fix something (e.g. `updater_mac.py`'s bundle-path detection,
a Gatekeeper workaround, a font/layout tweak): you have push access, so —

```bash
git checkout -b macos-fixes
git add -A
git commit -m "…what you fixed and why…"
git push -u origin macos-fixes
gh pr create --title "macOS fixes" --body "…"
```

Don't push straight to `main` — open a PR so Pedro can review before it
ships to Windows users too (it's one shared codebase).

In the PR description, note explicitly:
- What you tested (panels, shortcuts, tray icon, the updater round-trip).
- Anything you had to change and why (font substitutions, layout tweaks,
  anything Gatekeeper-related).
- Whether the auto-update swap-and-relaunch worked cleanly end-to-end.

## 6. Getting future updates (once this is merged)

Releases are automated now (`.github/workflows/release.yml`, see
`RELEASING.md` § "Automated releases"): Pedro pushes a version tag, and CI
builds, signs, and publishes **both** Windows and macOS in one shot — no one
needs to build on a Mac by hand again after your initial verification pass.

Once a release goes out, your locally-built `aurantium.app` checks for
updates once a day automatically (silent, only prompts if it finds
something newer), and you can trigger it manually anytime via
**Help ▸ Check for Updates…**. Same appcast feed and signing key as the
Windows build, just a different (pure-Python) download-and-swap mechanism
under the hood.
