"""Global active-symbol state per link group, adapted from Fincept's
SymbolContext (Bloomberg Launchpad "link group" model) — with the default
inverted: every panel joins group "A" unless it opts out, so clicking a
ticker anywhere updates every open panel out of the box.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

#: Panels join this group unless the user re-assigns them.
DEFAULT_GROUP = "A"
#: Available link groups (Bloomberg-style colored groups) + unlinked.
GROUPS = ["A", "B", "C", "D"]
UNLINKED = "None"

#: Badge colors per group (Bloomberg Launchpad convention: colored chips).
GROUP_COLORS = {
    "A": "#f5a623",  # amber
    "B": "#4a90d9",  # blue
    "C": "#7ed321",  # green
    "D": "#d0021b",  # red
    UNLINKED: "#666666",
}


class SymbolContext(QObject):
    """Singleton. ``set_symbol()`` publishes; panels react to
    ``symbol_changed(group, symbol, source)``. ``source`` is the QObject that
    originated the change so publishers can skip their own echo."""

    symbol_changed = Signal(str, str, object)  # group, symbol, source

    _inst: Optional["SymbolContext"] = None

    @classmethod
    def instance(cls) -> "SymbolContext":
        if cls._inst is None:
            cls._inst = SymbolContext()
        return cls._inst

    def __init__(self) -> None:
        super().__init__()
        self._symbols: dict[str, str] = {}

    def symbol(self, group: str) -> str:
        return self._symbols.get(group, "")

    def set_symbol(self, group: str, symbol: str, source: QObject | None = None) -> None:
        symbol = symbol.strip().upper()
        if not symbol or group == UNLINKED:
            return
        if self._symbols.get(group) == symbol:
            return  # no-op on same value: suppress signal (Fincept behavior)
        self._symbols[group] = symbol
        self.symbol_changed.emit(group, symbol, source)

    # -- layout persistence --------------------------------------------------

    def to_json(self) -> dict:
        return dict(self._symbols)

    def from_json(self, data: dict) -> None:
        for group, symbol in (data or {}).items():
            if isinstance(symbol, str) and symbol:
                self._symbols[group] = symbol
        # replay so freshly-restored panels sync up
        for group, symbol in self._symbols.items():
            self.symbol_changed.emit(group, symbol, None)
