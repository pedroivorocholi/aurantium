"""Company panel — one place for single-name research: Profile, Key Metrics,
Financials and Earnings as tabs.

These used to be four panels, which meant four docks to open, arrange and keep
in the same link group just to read one company. Each tab is still the full
panel class (so each keeps its own fetches, settings and tests); the host owns
the header, the link group and the symbol, and hands the symbol only to the
tab on screen — a tab you never open never fetches, and is never even built:
building all four up front held the UI thread for seconds while a workspace
restored, long enough for Windows to mark the app "Not responding".

The four retired panel ids stay openable: they are aliases that open this
panel on the matching tab, and ``migrate_layout`` folds a saved layout's
separate docks into one Company panel per link group.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ..guide import PanelGuide
from ..components.fit_width import fit_tabs
from ..panel import UNLINKED, Panel, PanelRegistry, register_panel
from .earnings import EarningsPanel
from .fundamentals import FundamentalsPanel
from .key_metrics import KeyMetricsPanel
from .profile import ProfilePanel

#: (key — also the retired panel id, tab label, class), in tab order
TABS = [
    ("profile", "Profile", ProfilePanel),
    ("key_metrics", "Key Metrics", KeyMetricsPanel),
    ("fundamentals", "Financials", FundamentalsPanel),
    ("earnings", "Earnings", EarningsPanel),
]
KEYS = [k for k, _l, _c in TABS]

#: left, top, right, bottom padding inside every tab
TAB_INSET = (8, 8, 8, 8)


@register_panel(id="company", title="Company", category="Research")
class CompanyPanel(Panel):
    GUIDE = PanelGuide(
        purpose="Single-name research in four tabs: Profile, Key Metrics, Financials and Earnings.",
        links="Follows the link group's symbol, and fetches only the tab on screen. "
        "Layouts that had separate Profile, Key Metrics, Financials or Earnings "
        "panels open them as one Company panel.",
        interactions=(
            "Click a stat tile on **Profile** to jump to the tab that explains it.",
            "The panel's **?** opens the guide for the tab you're on.",
        ),
    )

    HOSTED_TABS = tuple(TABS)

    def build(self) -> None:
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        # Not document mode, and no tab-bar base: under the native style both
        # paint a light 1px line along the bar that the dark theme never
        # overrides. The pane's own top hairline (theme.py) separates the bar.
        self.tabs.setDocumentMode(False)
        self.tabs.tabBar().setDrawBase(False)
        fit_tabs(self.tabs)  # labels elide in a narrow panel; no scroll arrows
        self.tabs.tabBar().setMinimumWidth(1)  # below its floor, clip — never widen
        self.tabs.setMinimumWidth(1)
        #: built tabs only; a tab is built the first time it is needed
        self.children_by_key: dict[str, Panel] = {}
        #: saved settings of tabs not built yet, applied when they are
        self._pending_settings: dict[str, dict] = {}
        self._pages: dict[str, QWidget] = {}
        for key, label, _cls in TABS:
            page = QWidget(self.tabs)
            QVBoxLayout(page).setContentsMargins(0, 0, 0, 0)
            self.tabs.addTab(page, label)
            self._pages[key] = page
        self.tabs.currentChanged.connect(self._on_tab)
        self.content_layout.addWidget(self.tabs, 1)

    def _ensure(self, key: str) -> Panel:
        """The tab's panel, built into its page on first call."""
        child = self.children_by_key.get(key)
        if child is not None:
            return child
        cls = next(c for k, _l, c in TABS if k == key)
        page = self._pages[key]
        child = cls(page)
        child._host = self
        child.set_link_group(UNLINKED)  # the host drives its symbol
        child._header.hide()
        child.build()
        # one inset for every tab, so content lines up as you switch
        child.content_layout.setContentsMargins(*TAB_INSET)
        page.layout().addWidget(child)
        self.children_by_key[key] = child
        pending = self._pending_settings.pop(key, None)
        if pending is not None:
            try:
                child.restore(pending)
            except Exception:
                pass
        return child

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._ensure(self.current_key)
        super().showEvent(event)

    # -- tabs ---------------------------------------------------------------

    @property
    def current_key(self) -> str:
        return KEYS[max(self.tabs.currentIndex(), 0)]

    def child(self, key: str) -> Panel:
        return self._ensure(key)

    def show_tab(self, key: str) -> Optional[Panel]:
        """Switch to a tab by key; returns that tab's panel."""
        if key not in KEYS:
            return None
        child = self._ensure(key)
        self.tabs.setCurrentIndex(KEYS.index(key))
        return child

    def _on_tab(self, _i: int) -> None:
        child = self._ensure(self.current_key)
        if self.current_symbol:
            child._apply_symbol(self.current_symbol)  # lazy: fetch on first view
        self._child_header_changed(child)

    def _child_header_changed(self, child: Panel) -> None:
        if child is not self.children_by_key.get(self.current_key):
            return
        self._status_lbl.setText(child._status_lbl.text())
        self._set_state(child._state_lbl.text(), child._state_lbl.property("severity") or "")

    def guide_section(self) -> str:
        """The ``?`` opens the page for the tab on screen."""
        return f"{self.panel_id}:{self.current_key}"

    # -- symbol -------------------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self._ensure(self.current_key)._apply_symbol(symbol)

    # -- persistence ----------------------------------------------------------

    def settings(self) -> dict:
        return {
            "tab": self.current_key,
            "tabs": {
                **self._pending_settings,
                **{k: c.settings() for k, c in self.children_by_key.items()},
            },
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        per_tab = settings.get("tabs")
        if isinstance(per_tab, dict):
            for key, child_settings in per_tab.items():
                if key not in KEYS or not isinstance(child_settings, dict):
                    continue
                child = self.children_by_key.get(key)
                if child is None:
                    self._pending_settings[key] = child_settings
                    continue
                try:
                    child.restore(child_settings)
                except Exception:
                    pass
        if settings.get("tab") in KEYS:
            self.show_tab(settings["tab"])

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        for child in self.children_by_key.values():
            child.close()
        super().closeEvent(event)


for _key in KEYS:
    PanelRegistry.add_alias(_key, "company", {"tab": _key})


def migrate_layout(doc: dict) -> dict:
    """Fold a layout's retired Profile / Key Metrics / Financials / Earnings
    docks into one Company panel per link group.

    The first retired dock in each group becomes the Company panel and keeps
    its instance id — so the saved dock arrangement still puts it where that
    dock was — opening on its tab, carrying every folded dock's settings. The
    rest are dropped; QtAds skips arrangement entries for docks that no longer
    exist. Returns a new dict; ``doc`` is not modified."""
    panels = doc.get("panels") if isinstance(doc, dict) else None
    if not isinstance(panels, list) or not any(
        isinstance(s, dict) and s.get("panel_id") in KEYS for s in panels
    ):
        return doc
    out: list = []
    hosts: dict[str, dict] = {}
    for spec in panels:
        if not isinstance(spec, dict) or spec.get("panel_id") not in KEYS:
            out.append(spec)
            continue
        key = spec["panel_id"]
        group = spec.get("link_group") or ""
        host = hosts.get(group)
        if host is None:
            host = dict(spec)
            host["panel_id"] = "company"
            host["settings"] = {"tab": key, "tabs": {}}
            hosts[group] = host
            out.append(host)
        if isinstance(spec.get("settings"), dict):
            host["settings"]["tabs"].setdefault(key, spec["settings"])
    new = dict(doc)
    new["panels"] = out
    return new
