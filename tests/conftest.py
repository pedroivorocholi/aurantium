"""Shared fixtures: one offscreen QApplication for widget tests."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_addoption(parser):
    # CI's Windows runner reports "Show animations" OFF and a dev machine
    # usually reports it ON, so the same suite silently tested two different
    # apps. Every motion change has to pass both ways: run with
    # ``--animations=on`` and ``--animations=off``. Tests that monkeypatch
    # ``motion.animations_enabled`` themselves still win inside their scope.
    parser.addoption(
        "--animations",
        choices=("system", "on", "off"),
        default="system",
        help="force motion.animations_enabled() on or off (default: follow the OS)",
    )


def pytest_configure(config):
    mode = config.getoption("--animations")
    if mode == "system":
        return
    from aurantium import motion

    forced = mode == "on"
    original = motion.animations_enabled

    def forced_animations_enabled() -> bool:
        return forced

    # tests of the OS query itself reach the real function through this
    forced_animations_enabled.__wrapped__ = original
    motion.animations_enabled = forced_animations_enabled


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
