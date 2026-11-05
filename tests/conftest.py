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


@pytest.fixture
def isolated_settings(tmp_path, qapp):
    """Point QSettings at an INI file under ``tmp_path`` for one test.

    Dev runs share the installed app's QSettings, so a test that writes a
    preference must never reach the real store. Organisation and application
    names are set too: with neither, ``QSettings()`` has nowhere to write.
    Only default-constructed ``QSettings()`` is isolated; the redirected search
    path is not restored afterwards (Qt cannot report the previous one).
    """
    from PySide6.QtCore import QCoreApplication, QSettings

    old_format = QSettings.defaultFormat()
    old_org = QCoreApplication.organizationName()
    old_app = QCoreApplication.applicationName()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    QCoreApplication.setOrganizationName("aurantium-test")
    QCoreApplication.setApplicationName("aurantium-test")
    try:
        yield tmp_path
    finally:
        QSettings.setDefaultFormat(old_format)
        QCoreApplication.setOrganizationName(old_org)
        QCoreApplication.setApplicationName(old_app)
