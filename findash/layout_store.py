"""Named layout storage kept in the per-user config dir — no folder picking.

Layouts (and the auto-saved last session) live in a single JSON file under the
OS's standard app-config location, so they persist across runs and travel with
the installed app rather than sitting next to the code.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths


class LayoutStore:
    def __init__(self) -> None:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        ) or str(Path.home() / ".findash")
        self._path = Path(base) / "layouts.json"
        self._data: dict = {"layouts": {}, "last": None}
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data = data
        except (OSError, json.JSONDecodeError):
            pass
        self._data.setdefault("layouts", {})
        self._data.setdefault("last", None)

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # -- named layouts -------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self._data["layouts"].keys(), key=str.lower)

    def get(self, name: str) -> dict | None:
        return self._data["layouts"].get(name)

    def put(self, name: str, doc: dict) -> None:
        self._data["layouts"][name] = doc
        self._flush()

    def delete(self, name: str) -> None:
        if self._data["layouts"].pop(name, None) is not None:
            self._flush()

    # -- auto-saved last session --------------------------------------------

    def get_last(self) -> dict | None:
        return self._data.get("last")

    def set_last(self, doc: dict) -> None:
        self._data["last"] = doc
        self._flush()

    @property
    def path(self) -> Path:
        return self._path
