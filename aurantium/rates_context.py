"""Global active-country state per link group, for the rates panels.

Deliberately separate from SymbolContext rather than sharing a base class.
SymbolContext carries free-text tickers and every panel joins group "A" by
default with no type discrimination, so publishing a country code there would
make the chart, news and fundamentals panels all try to load it as a symbol.
The two also validate differently — a country code is checked against
rates_meta, a ticker is not — and a shared base would couple every equity
panel to rates changes. ~50 lines of duplication is the cheaper mistake.

Group vocabulary (A/B/C/D + unlinked) is reused from symbol_context so the
badge UI and the user's mental model stay identical.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

from .rates_meta import by_code
from .symbol_context import DEFAULT_GROUP, GROUPS, UNLINKED

__all__ = ["RatesContext", "DEFAULT_GROUP", "GROUPS", "UNLINKED"]


class RatesContext(QObject):
    """Singleton. ``set_country()`` publishes; rates panels react to
    ``country_changed(group, code, source)``. ``source`` is the originating
    QObject so publishers can skip their own echo."""

    country_changed = Signal(str, str, object)  # group, code, source

    _inst: Optional["RatesContext"] = None

    @classmethod
    def instance(cls) -> "RatesContext":
        if cls._inst is None:
            cls._inst = RatesContext()
        return cls._inst

    def __init__(self) -> None:
        super().__init__()
        self._countries: dict[str, str] = {}

    def country(self, group: str) -> str:
        return self._countries.get(group, "")

    def set_country(
        self, group: str, code: str, source: QObject | None = None
    ) -> None:
        if not isinstance(code, str) or group == UNLINKED:
            return
        meta = by_code(code)
        if meta is None:
            return  # unknown country: ignore rather than publish nonsense
        if self._countries.get(group) == meta.code:
            return  # no-op on same value, matching SymbolContext
        self._countries[group] = meta.code
        self.country_changed.emit(group, meta.code, source)

    # -- layout persistence --------------------------------------------------

    def to_json(self) -> dict:
        return dict(self._countries)

    def from_json(self, data: dict) -> None:
        """Restore from layout JSON. Must never raise.

        This runs under apply_layout during startup restore, which IS wrapped
        (__main__.py:344) — so a raise doesn't kill the app, it silently costs
        the user their entire saved workspace and shows a startup error
        instead. `data` comes from doc.get("rates", {}), and .get returns the
        raw value when the key exists, so a hand-edited layout can hand us a
        string or a list. Guard the shape, not just the contents:
        symbol_context.py:66's `(data or {})` only substitutes on a FALSY
        value and raises AttributeError on any truthy non-dict."""
        if not isinstance(data, dict):
            return
        for group, code in data.items():
            if not isinstance(group, str) or not isinstance(code, str):
                continue
            meta = by_code(code)
            if meta is not None:
                self._countries[group] = meta.code
        for group, code in self._countries.items():
            self.country_changed.emit(group, code, None)
