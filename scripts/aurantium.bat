@echo off
rem Launch aurantium from the dev tree without a console window.
rem Lives in scripts\, so the repo root (where .venv\ and aurantium\ are) is one level up.
start "" /D "%~dp0.." "%~dp0..\.venv\Scripts\pythonw.exe" -m aurantium
