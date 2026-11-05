"""FxService: the preferred-currency setting plus live and dated rates."""

import calendar
from datetime import datetime

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy, QTest

from aurantium import fx
from aurantium.datahub import DataHub
from aurantium.fx import FxService


@pytest.fixture
def svc(isolated_settings):
    """A fresh service on a fresh, cache-less hub, never the process-wide ones.

    A warm hub would hand a previous test's rate to the next one on subscribe.
    """
    saved_hub = DataHub._inst
    hub = DataHub()
    hub._cache = None
    DataHub._inst = hub
    s = FxService()
    FxService.reset_instance(s)
    yield s
    FxService.reset_instance(None)
    hub._scheduler.stop()
    hub.deleteLater()
    DataHub._inst = saved_hub


class _Burst:
    """Wait for the (coalesced) ``changed`` after a block of publishes."""

    def __init__(self, svc):
        self._spy = QSignalSpy(svc.changed)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            assert self._spy.wait(2000), "changed never fired"


def _epoch(day: str) -> int:
    d = datetime.fromisoformat(day)
    return calendar.timegm(d.timetuple())


def test_default_is_usd_and_setting_persists(svc):
    assert svc.preferred() == "USD"
    with _Burst(svc):
        svc.set_preferred("BRL")
    assert svc.preferred() == "BRL"
    assert QSettings().value("ui/currency") == "BRL"
    assert FxService().preferred() == "BRL"


def test_unknown_currency_is_ignored(svc):
    svc.set_preferred("XXX")
    assert svc.preferred() == "USD"


def test_rate_arrives_from_quote_topic(svc):
    assert svc.rate("BRL", "USD") is None
    with _Burst(svc):
        DataHub.instance().publish("quote:BRLUSD=X", {"price": 0.18})
    assert svc.rate("BRL", "USD") == 0.18


def test_rate_same_currency_needs_no_subscription(svc):
    assert svc.rate("USD", "USD") == 1.0
    assert svc.rate(None, "USD") is None
    assert DataHub.instance()._subs == {}


def test_repeated_asks_subscribe_once(svc):
    svc.rate("BRL", "USD")
    svc.rate("BRL", "USD")
    assert len(DataHub.instance()._subs["quote:BRLUSD=X"]) == 1
    svc.rate_on("BRL", "2026-11-01", "USD")
    svc.rate_on("BRL", "2026-11-02", "USD")
    assert len(DataHub.instance()._subs["history:BRLUSD=X:2y:1d"]) == 1


def test_setting_current_value_is_a_no_op(svc, isolated_settings):
    spy = QSignalSpy(svc.changed)
    svc.set_preferred("USD")
    QTest.qWait(300)
    assert spy.count() == 0
    assert QSettings().value("ui/currency") is None


def test_hub_error_leaves_rate_missing(svc):
    svc.rate("BRL", "USD")
    DataHub.instance().publish_error("quote:BRLUSD=X", "boom")
    QTest.qWait(50)
    assert svc.rate("BRL", "USD") is None


def test_rate_folds_minor_units_to_base_pair(svc):
    svc.rate("GBp", "USD")
    with _Burst(svc):
        DataHub.instance().publish("quote:GBPUSD=X", {"price": 1.25})
    assert svc.rate("GBP", "USD") == 1.25


def test_rate_on_uses_last_prior_day(svc):
    assert svc.rate_on("BRL", "2026-11-01", "USD") is None
    hist = {
        "t": [_epoch("2026-10-30"), _epoch("2026-11-02")],
        "c": [0.18, 0.19],
    }
    with _Burst(svc):
        DataHub.instance().publish("history:BRLUSD=X:2y:1d", hist)
    assert svc.rate_on("BRL", "2026-11-01", "USD") == 0.18
    assert svc.rate_on("BRL", "2026-11-02", "USD") == 0.19
    assert svc.rate_on("BRL", "2026-10-01", "USD") is None


def test_rate_on_skips_missing_closes(svc):
    svc.rate_on("BRL", "2026-11-03", "USD")
    hist = {"t": [_epoch("2026-10-30"), _epoch("2026-11-02")], "c": [0.18, None]}
    with _Burst(svc):
        DataHub.instance().publish("history:BRLUSD=X:2y:1d", hist)
    assert svc.rate_on("BRL", "2026-11-03", "USD") == 0.18


def test_currency_remembered_between_quotes(svc):
    assert svc.currency_of("PETR4.SA") is None
    svc.note_quote("PETR4.SA", {"currency": "BRL"})
    assert svc.currency_of("PETR4.SA") == "BRL"
    assert svc.currency_of("AAPL") == "USD"


def test_value_uses_buy_date_rate_when_given(svc):
    svc.note_quote("PETR4.SA", {"currency": "BRL"})
    assert svc.value("PETR4.SA", 10.0) is None  # no rate yet: fails closed
    svc.value("PETR4.SA", 10.0, "2026-11-01")
    hub = DataHub.instance()
    with _Burst(svc):
        hub.publish("quote:BRLUSD=X", {"price": 0.20})
    with _Burst(svc):
        hub.publish(
            "history:BRLUSD=X:2y:1d",
            {"t": [_epoch("2026-10-30")], "c": [0.18]},
        )
    assert svc.value("PETR4.SA", 10.0) == pytest.approx(2.0)
    assert svc.value("PETR4.SA", 10.0, "2026-11-01") == pytest.approx(1.8)


def test_value_same_currency_and_unknown_listing(svc):
    assert svc.value("AAPL", 5.0) == 5.0
    assert svc.value("UNKNOWN.XX", 5.0) is None


def test_changed_is_coalesced(svc):
    seen = []
    svc.changed.connect(lambda: seen.append(1))
    hub = DataHub.instance()
    svc.rate("BRL", "USD")
    svc.rate("EUR", "USD")
    hub.publish("quote:BRLUSD=X", {"price": 0.18})
    hub.publish("quote:EURUSD=X", {"price": 1.1})
    QTest.qWait(400)
    assert len(seen) == 1
