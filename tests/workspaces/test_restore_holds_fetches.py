"""A workspace restore holds DataHub fetches until every panel is built (see
DataHub.hold), and always lets them go — a hold left behind would leave every
panel waiting on data forever."""

from PySide6.QtWidgets import QApplication


def _win():
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    return MainWindow()


def test_panels_are_built_while_fetches_are_held(qapp, monkeypatch):
    from aurantium.datahub import DataHub

    hub = DataHub.instance()
    win = _win()
    try:
        win.add_panel("chart")
        doc = win.serialize_layout()
        held_during_build = []
        orig = win._build_placeholder

        def spy(instance_id):
            held_during_build.append(hub._holds > 0)
            return orig(instance_id)

        monkeypatch.setattr(win, "_build_placeholder", spy)
        assert win.apply_layout(doc)
        assert held_during_build and all(held_during_build)
        assert hub._holds == 0
    finally:
        win.close()
        win.deleteLater()
        QApplication.processEvents()


def test_a_failing_restore_still_releases_the_hold(qapp, monkeypatch):
    from aurantium.datahub import DataHub

    hub = DataHub.instance()
    win = _win()
    try:
        win.add_panel("chart")
        doc = win.serialize_layout()

        def boom(_instance_id):
            raise RuntimeError("panel build blew up")

        monkeypatch.setattr(win, "_build_placeholder", boom)
        try:
            win.apply_layout(doc)
        except RuntimeError:
            pass
        assert hub._holds == 0
    finally:
        win.close()
        win.deleteLater()
        QApplication.processEvents()
