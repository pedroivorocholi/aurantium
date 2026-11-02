"""One app-wide event filter for the app's observers (focus rings, press
feedback, transitions, rounded popups).

Each app-level Python filter costs a Python call on *every* event of *every*
widget, paints included. Four of them made building a panel ~3x slower, and
during a workspace restore — with fetch threads contending for the GIL — that
held the UI thread long enough for Windows to mark the app "Not responding".
The router makes one call per event and only reaches a filter for the event
types it declared."""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget


class _Spy(QObject):
    def __init__(self, name, log, consume=False):
        super().__init__()
        self.name, self.log, self.consume = name, log, consume

    def eventFilter(self, obj, event):  # noqa: N802
        self.log.append((self.name, event.type()))
        return self.consume


def test_routed_filter_sees_only_its_event_types(qapp):
    from aurantium import events

    log = []
    spy = _Spy("a", log)
    events.route(qapp, spy, (QEvent.Type.FocusIn,))
    try:
        w = QWidget()
        qapp.sendEvent(w, QEvent(QEvent.Type.FocusIn))
        qapp.sendEvent(w, QEvent(QEvent.Type.Polish))
        assert log == [("a", QEvent.Type.FocusIn)]
    finally:
        events.unroute(spy)


def test_last_routed_runs_first_and_consuming_stops_the_rest(qapp):
    """The order Qt gives app filters: the most recently installed first."""
    from aurantium import events

    log = []
    first = _Spy("first", log)
    second = _Spy("second", log, consume=True)
    events.route(qapp, first, (QEvent.Type.Polish,))
    events.route(qapp, second, (QEvent.Type.Polish,))
    try:
        qapp.sendEvent(QWidget(), QEvent(QEvent.Type.Polish))
        assert [n for n, _t in log] == ["second"]
        second.consume = False
        log.clear()
        qapp.sendEvent(QWidget(), QEvent(QEvent.Type.Polish))
        assert [n for n, _t in log] == ["second", "first"]
    finally:
        events.unroute(first)
        events.unroute(second)


def test_unrouted_filter_stops_receiving(qapp):
    from aurantium import events

    log = []
    spy = _Spy("a", log)
    events.route(qapp, spy, (QEvent.Type.Polish,))
    events.unroute(spy)
    qapp.sendEvent(QWidget(), QEvent(QEvent.Type.Polish))
    assert log == []


def test_app_observers_share_the_one_router(qapp):
    from aurantium import events, focus, popups, press, transitions

    flts = [m.install(qapp) for m in (focus, popups, press, transitions)]
    try:
        for flt in flts:
            assert events.is_routed(flt)
    finally:
        for flt in flts:
            events.unroute(flt)
