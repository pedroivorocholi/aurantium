# Building findash as a standalone app

The goal: a standalone app you can hand to anyone — no Python, no venv, no install
steps. Builds on **Windows** (`findash.exe`) and **macOS** (`findash.app`) from the
same spec. You must run PyInstaller **on the target OS** — you can't cross-build a Mac
app from Windows or vice-versa.

## 1a. Windows — one-file executable

From the `app/` folder:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\pyinstaller findash.spec --noconfirm --clean
```

Result: **`dist\findash\`** — a one-folder app (`findash.exe` + `_internal\`). It
runs directly from its folder with no per-launch temp unpack, so startup is fast
and it relaunches reliably after an auto-update. Distribute it via the installer
(step 2), not as a loose folder.

## 1b. macOS — .app bundle

On a Mac, from the `app/` folder:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pyinstaller findash.spec --noconfirm --clean
```

Result: **`dist/findash.app`** — a normal Mac app. Drag it to `/Applications`, or zip
it and send it. First launch: right-click ▸ Open (an unsigned app needs Gatekeeper's
one-time approval; code-sign + notarize to skip this for others).

To distribute as a disk image: `hdiutil create -volname findash -srcfolder dist/findash.app -ov -format UDZO dist/findash.dmg`.

The macOS icon is `findash.icns` (already in the repo, generated from `findash.ico`).
To regenerate it: `python -c "from PIL import Image; Image.open('findash.ico').convert('RGBA').resize((1024,1024)).save('findash.icns')"` (needs `pillow`).

Notes (both platforms):
- A fresh install opens to an **empty workspace** — no panels. Open panels from the
  **Panels** menu, or load/save arrangements from the **Layout** menu.
- Saved layouts live per-user, so they persist across updates and are never
  overwritten by a new build:
  - Windows: `%LOCALAPPDATA%\findash\findash\layouts.json`
  - macOS: `~/Library/Application Support/findash/findash/layouts.json`
- First launch takes a few seconds (a one-file build unpacks to a temp dir).
- Optional API keys: put a `.env` file next to the app (see `.env.example`).

## 2. Windows installer (optional, nicer to distribute)

Produces a `Setup.exe` that installs findash, adds Start-Menu and desktop
shortcuts (with the correct icon + taskbar identity), and registers an
uninstaller.

1. Build `dist\findash.exe` (step 1 above).
2. Install [Inno Setup](https://jrsoftware.org/isdl.php) (free).
3. Open `installer.iss` in Inno Setup and click **Build ▸ Compile** (or run
   `iscc installer.iss`).

Result: **`dist\findash-setup.exe`** — the installer to share.

## Troubleshooting

- **A panel is missing in the built app**: panels are collected via
  `collect_submodules("findash.panels")` in `findash.spec`. A brand-new built-in
  panel is picked up automatically; a third-party import it needs may have to be
  added to `hiddenimports`.
- **Antivirus flags the exe**: unsigned one-file PyInstaller apps sometimes trip
  heuristics. Code-signing the exe (or shipping the Inno Setup installer) avoids
  most of this.
