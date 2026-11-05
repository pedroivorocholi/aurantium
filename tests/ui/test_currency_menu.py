"""Settings ▸ Currency: lists the supported currencies and sets the preference."""
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from aurantium.fx import CURRENCIES, FxService


class _Host(QWidget):
    """Just enough of MainWindow to build the Settings menu."""

    def __getattr__(self, name):  # handlers/rebuilders the menu only wires up
        from unittest.mock import MagicMock
        if name.startswith("_") and not name.startswith("__"):
            return MagicMock()
        raise AttributeError(name)


@pytest.fixture
def host(svc):
    from aurantium.app import MainWindow

    h = _Host()
    h._build_settings_menu = MainWindow._build_settings_menu.__get__(h)
    h._sync_currency_menu = MainWindow._sync_currency_menu.__get__(h)
    h._build_settings_menu()
    yield h
    h.deleteLater()


def _checked(h):
    return [c for c, a in h._currency_acts.items() if a.isChecked()]


def test_currency_menu_lists_and_checks_preferred(host, svc):
    assert tuple(host._currency_acts) == CURRENCIES
    assert _checked(host) == [svc.preferred()]
    assert host._currency_group.isExclusive()


def test_picking_currency_sets_preference(host, svc):
    target = CURRENCIES[1]
    host._currency_acts[target].trigger()
    assert svc.preferred() == target
    assert _checked(host) == [target]


def test_menu_follows_preference_changed_elsewhere(host, svc):
    svc.set_preferred(CURRENCIES[2])
    QTest.qWait(250)  # the service coalesces ``changed`` over 150 ms
    assert _checked(host) == [CURRENCIES[2]]
