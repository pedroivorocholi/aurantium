"""Global as-of date state per link group, for the Day Brief panel.

Deliberately separate from SymbolContext, for the same reason RatesContext is
(read its docstring first): SymbolContext carries free-text tickers and every
panel joins group "A" by default with no type discrimination, so publishing a
date into that channel would make the chart, news, fundamentals and options
panels all try to load it as a ticker.

Carries an **anchor date plus a window token**, not a free range. That is what
lets "look at an interval" work without a second date control fighting the
first: the stat block always describes the anchor day, while events and news
sweep the window around it. The window is a sweep *radius* in days -- "3D"
means anchor +/- 3 -- and consumers are expected to show the resolved range
back to the user rather than leave the token abstract.

Group vocabulary (A/B/C/D + unlinked) is reused from symbol_context so the
badge UI and the user's mental model stay identical.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .symbol_context import DEFAULT_GROUP, GROUPS, UNLINKED

__all__ = [
    "DateContext",
    "WINDOWS",
    "DEFAULT_WINDOW",
    "WINDOW_SPAN_DAYS",
    "window_radius",
    "parse_iso",
    "window_range",
    "format_range",
    "DEFAULT_GROUP",
    "GROUPS",
    "UNLINKED",
]

# TOTAL span in calendar days, centred on the anchor -- not a radius.
#
# These numbers are read off a button labelled "1W", so "1W" has to mean seven
# days and nothing else. An earlier version stored the radius, which quietly
# turned "1W" into fifteen days and "1M" into sixty-one: the chip and the
# resolved range printed underneath it contradicted each other, which is worse
# than either meaning would have been on its own.
#
# Spans are odd so the anchor sits exactly in the middle (span = 2*radius + 1).
WINDOW_SPAN_DAYS: dict[str, int] = {"1D": 1, "3D": 3, "1W": 7, "1M": 31}
WINDOWS: tuple[str, ...] = ("1D", "3D", "1W", "1M")

#: 3D, not 1D. News that explains a Monday gap routinely breaks over the
#: weekend, so the default sweep reaches one day either side; "1D" stays
#: available for the strict single session.
DEFAULT_WINDOW = "3D"


def window_radius(window: str) -> int:
    """Days to reach on each side of the anchor for a window token."""
    span = WINDOW_SPAN_DAYS.get(window, WINDOW_SPAN_DAYS[DEFAULT_WINDOW])
    return (span - 1) // 2


def parse_iso(value: str) -> Optional[date]:
    """``YYYY-MM-DD`` -> date, or None. Never raises -- this parses text that
    reaches us from layout files, the command bar and chart clicks alike."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def window_range(anchor: str, window: str) -> Optional[tuple[str, str]]:
    """Resolve an anchor + window token into an inclusive ``(start, end)`` pair
    of ISO dates. Pure; the panel and the provider both rely on it agreeing."""
    day = parse_iso(anchor)
    if day is None:
        return None
    radius = window_radius(window)
    return (
        (day - timedelta(days=radius)).isoformat(),
        (day + timedelta(days=radius)).isoformat(),
    )


def format_range(start: str, end: str) -> str:
    """Human range for the sub-line under the date field: "13 - 15 Mar" when
    the months match, "28 Feb - 3 Mar" when they don't."""
    a, b = parse_iso(start), parse_iso(end)
    if a is None or b is None:
        return ""
    if a == b:
        return f"{a.day} {a.strftime('%b')}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day} \u2013 {b.day} {b.strftime('%b')}"
    return f"{a.day} {a.strftime('%b')} \u2013 {b.day} {b.strftime('%b')}"


class DateContext(QObject):
    """Singleton. ``set_date()`` publishes; date-aware panels react to
    ``date_changed(group, anchor, window, source)``. ``source`` is the
    originating QObject so publishers can skip their own echo."""

    date_changed = Signal(str, str, str, object)  # group, anchor, window, source

    _inst: Optional["DateContext"] = None

    @classmethod
    def instance(cls) -> "DateContext":
        if cls._inst is None:
            cls._inst = DateContext()
        return cls._inst

    def __init__(self) -> None:
        super().__init__()
        self._dates: dict[str, str] = {}
        self._windows: dict[str, str] = {}

    def date(self, group: str) -> str:
        return self._dates.get(group, "")

    def window(self, group: str) -> str:
        return self._windows.get(group, DEFAULT_WINDOW)

    def set_date(
        self,
        group: str,
        anchor: str,
        window: str | None = None,
        source: QObject | None = None,
    ) -> None:
        if group == UNLINKED:
            return
        day = parse_iso(anchor)
        if day is None:
            return  # unparseable date: ignore rather than publish nonsense
        iso = day.isoformat()
        win = window if window in WINDOW_SPAN_DAYS else self.window(group)
        if self._dates.get(group) == iso and self._windows.get(group) == win:
            return  # no-op on same value, matching SymbolContext
        self._dates[group] = iso
        self._windows[group] = win
        self.date_changed.emit(group, iso, win, source)

    # -- layout persistence --------------------------------------------------

    def to_json(self) -> dict:
        return {
            group: {"anchor": iso, "window": self.window(group)}
            for group, iso in self._dates.items()
        }

    def from_json(self, data: dict) -> None:
        """Restore from layout JSON. Must never raise.

        Runs under apply_layout during startup restore. That IS wrapped
        (__main__.py:344), so a raise doesn't kill the app -- it silently costs
        the user their entire saved workspace and shows a startup error
        instead. Guard the shape, not just the contents: a hand-edited layout
        can hand us a string, a list, or a dict of dicts of junk."""
        if not isinstance(data, dict):
            return
        for group, entry in data.items():
            if not isinstance(group, str) or not isinstance(entry, dict):
                continue
            day = parse_iso(entry.get("anchor", ""))
            if day is None:
                continue
            win = entry.get("window")
            self._dates[group] = day.isoformat()
            self._windows[group] = win if win in WINDOW_SPAN_DAYS else DEFAULT_WINDOW
        for group, iso in self._dates.items():
            self.date_changed.emit(group, iso, self.window(group), None)
