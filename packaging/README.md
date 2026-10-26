# packaging/

Everything that turns the source tree into a shippable app. Run the commands
from the repo root, so `dist/` and `build/` land there.

| Path | What |
|---|---|
| `aurantium.spec` | PyInstaller spec (Windows one-folder build and the macOS `.app`). Every first-party module reached by a lazy import must be listed in its `hiddenimports`. |
| `run_aurantium.py` | The PyInstaller entry point, a thin wrapper around `aurantium.__main__.main`. |
| `windows/installer.iss` | Inno Setup script that wraps `dist/aurantium/` into `dist/aurantium-setup.exe`. Its `AppVersion` is bumped with every release. |
| `windows/WinSparkle.dll` | The 64-bit WinSparkle auto-updater, bundled into the Windows build. |
| `windows/probe.iss` | Throwaway diagnostic installer that logs the environment Inno gives a silent `[Run]` child. |

```
pyinstaller packaging/aurantium.spec --noconfirm --clean
iscc packaging/windows/installer.iss
```

Full steps: `docs/guides/BUILD.md` and `docs/guides/RELEASING.md`.
