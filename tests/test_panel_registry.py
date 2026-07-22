"""The Examples category must never reach the panel registry — tutorial
files are docs, not product panels (they leaked into the Add Panel menu
once; never again)."""

import pytest

pytestmark = pytest.mark.usefixtures("qapp")


def test_examples_category_never_registers():
    from aurantium.panel import Panel, PanelRegistry, register_panel

    @register_panel(id="_test_example", title="Test Example", category="Examples")
    class _TutorialPanel(Panel):
        def build(self) -> None:  # pragma: no cover - never built
            pass

    assert PanelRegistry.get("_test_example") is None
    assert all(m.category != "Examples" for m in PanelRegistry.all())


def test_normal_category_still_registers():
    from aurantium.panel import Panel, PanelRegistry, register_panel

    @register_panel(id="_test_normal", title="Test Normal", category="Analytics")
    class _NormalPanel(Panel):
        def build(self) -> None:  # pragma: no cover - never built
            pass

    try:
        assert PanelRegistry.get("_test_normal") is not None
    finally:
        PanelRegistry._panels.pop("_test_normal", None)
