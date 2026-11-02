"""One app-wide event filter that hands each event to the observers that asked
for its type.

An app-level filter written in Python costs a Python call on every event of
every widget — paints, layouts, polishes included. Four of them (focus rings,
press feedback, transitions, rounded popups) made building a panel about three
times slower, and while a workspace restored with fetch threads contending for
the GIL, that held the UI thread long enough for Windows to mark the app "Not
responding". Routed, an event costs one call, and reaches a filter only when
its type is one the filter declared.

Order matches Qt's own for app filters: the most recently routed runs first,
and a filter returning True stops the event there.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEvent, QObject
from shiboken6 import isValid


class EventRouter(QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._routes: dict[QEvent.Type, tuple[QObject, ...]] = {}

    def add(self, flt: QObject, types: Iterable[QEvent.Type]) -> None:
        for t in types:
            # a new tuple each time: dispatch iterates without copying
            self._routes[t] = (flt, *(f for f in self._routes.get(t, ()) if f is not flt))

    def remove(self, flt: QObject) -> None:
        for t, flts in list(self._routes.items()):
            kept = tuple(f for f in flts if f is not flt)
            if kept:
                self._routes[t] = kept
            else:
                del self._routes[t]

    def has(self, flt: QObject) -> bool:
        return any(flt in flts for flts in self._routes.values())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        flts = self._routes.get(event.type())
        if flts:
            for flt in flts:
                if flt.eventFilter(obj, event):
                    return True
        return False


_router: EventRouter | None = None


def _instance(app) -> EventRouter:
    global _router
    if _router is None or not isValid(_router):
        _router = EventRouter(app)
        app.installEventFilter(_router)
    return _router


def route(app, flt: QObject, types: Iterable[QEvent.Type]) -> QObject:
    """Deliver ``app``'s events of ``types`` to ``flt.eventFilter``. Returns
    ``flt``; the caller keeps it alive, as with any event filter."""
    router = _instance(app)
    router.add(flt, types)
    flt.destroyed.connect(lambda *_: isValid(router) and router.remove(flt))
    return flt


def unroute(flt: QObject) -> None:
    if _router is not None and isValid(_router):
        _router.remove(flt)


def is_routed(flt: QObject) -> bool:
    return _router is not None and isValid(_router) and _router.has(flt)
