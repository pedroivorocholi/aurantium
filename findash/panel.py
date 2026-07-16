"""Panel plugin API: the ONLY surface a custom panel needs.

A panel is one Python class in one file::

    from findash.panel import Panel, register_panel

    @register_panel(id="my_panel", title="My Panel", category="Custom")
    class MyPanel(Panel):
        def build(self):            # create widgets into self.content_layout
            ...
        def on_symbol(self, sym):   # active symbol of this panel's link group changed
            self.subscribe(f"quote:{sym}", self.on_quote)

Drop the file into ``user_panels/``, restart, and it appears in the
Panels ▸ Add Panel menu. See PANELS.md.
"""

from __future__ import annotations

import importlib.util
import pkgutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Type

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .datahub import DataHub
from .theme import BG
from .symbol_context import (
    DEFAULT_GROUP,
    GROUP_COLORS,
    GROUPS,
    UNLINKED,
    SymbolContext,
)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

@dataclass
class PanelMeta:
    id: str
    title: str
    category: str
    cls: Type["Panel"]


class PanelRegistry:
    """Global id → panel-class registry populated by @register_panel."""

    _panels: dict[str, PanelMeta] = {}

    @classmethod
    def add(cls, meta: PanelMeta) -> None:
        cls._panels[meta.id] = meta

    @classmethod
    def get(cls, panel_id: str) -> Optional[PanelMeta]:
        return cls._panels.get(panel_id)

    @classmethod
    def all(cls) -> list[PanelMeta]:
        return sorted(cls._panels.values(), key=lambda m: (m.category, m.title))


def register_panel(id: str, title: str, category: str = "General"):
    """Class decorator registering a Panel subclass under ``id``."""

    def deco(cls: Type["Panel"]) -> Type["Panel"]:
        cls.panel_id = id
        cls.panel_title = title
        cls.panel_category = category
        PanelRegistry.add(PanelMeta(id=id, title=title, category=category, cls=cls))
        return cls

    return deco


def _package_name(directory: Path) -> str | None:
    """Dotted module path if ``directory`` is a package on sys.path
    (e.g. findash/panels -> "findash.panels"), else None."""
    if not (directory / "__init__.py").exists():
        return None
    parts = [directory.name]
    parent = directory.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


def discover_package_panels(package: str) -> list[str]:
    """Import every submodule of an installed package (e.g. ``findash.panels``)
    so their ``@register_panel`` decorators run.

    Enumerates submodule names via several strategies so it works both in
    development (files on disk) and inside a frozen PyInstaller build (modules
    in an archive): ``pkgutil`` first, then a filesystem scan, then the
    package's explicit ``BUILTIN`` fallback list. Returns errors (empty = ok)."""
    errors: list[str] = []
    try:
        pkg = importlib.import_module(package)
    except Exception:
        return [f"{package}: {traceback.format_exc(limit=3)}"]

    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name and not name.startswith("_") and name not in seen:
            seen.add(name)
            names.append(name)

    try:
        for info in pkgutil.iter_modules(pkg.__path__):
            _add(info.name)
    except Exception:
        pass
    try:
        for entry in Path(list(pkg.__path__)[0]).glob("*.py"):
            _add(entry.stem)
    except Exception:
        pass
    for name in getattr(pkg, "BUILTIN", ()):  # frozen-safe fallback
        _add(name)

    for name in names:
        try:
            importlib.import_module(f"{package}.{name}")
        except Exception:
            errors.append(f"{package}.{name}: {traceback.format_exc(limit=3)}")
    return errors


def discover_panels(
    directories: list[Path], packages: tuple[str, ...] = ()
) -> list[str]:
    """Load panels from installed ``packages`` (frozen-safe) and from plain
    ``directories`` of ``*.py`` files (e.g. a user's ``user_panels`` folder,
    loaded by file path). Returns error strings (empty = ok)."""
    errors: list[str] = []
    for package in packages:
        errors.extend(discover_package_panels(package))
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                mod_name = f"findash_user_panels_{path.stem}"
                if mod_name in sys.modules:
                    continue
                spec = importlib.util.spec_from_file_location(mod_name, path)
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            except Exception:
                errors.append(f"{path}: {traceback.format_exc(limit=3)}")
    return errors


# --------------------------------------------------------------------------
# Panel base class
# --------------------------------------------------------------------------

class Panel(QWidget):
    """Base class for all panels.

    Provides: a header strip (title + link-group badge), a content area
    (``self.content_layout``), DataHub subscription helpers with automatic
    cleanup, and linked-symbol plumbing. Panels join link group "A" by
    default — selections propagate everywhere unless the user re-groups.
    """

    panel_id: str = ""
    panel_title: str = ""
    panel_category: str = "General"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hub = DataHub.instance()
        self._ctx = SymbolContext.instance()
        self._link_group = DEFAULT_GROUP
        self._current_symbol = ""
        self._topics: set[str] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- header strip: a slim context bar. The panel's NAME already lives
        # in the dock tab above, so we don't repeat it here — this bar carries
        # live status (left) and the link-group badge (right) only.
        header = QWidget(self)
        header.setObjectName("panelHeader")
        header.setFixedHeight(21)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(9, 0, 6, 0)
        hl.setSpacing(6)
        self._status_lbl = QLabel("", header)
        self._status_lbl.setObjectName("panelStatus")
        self._badge = QToolButton(header)
        self._badge.setObjectName("groupBadge")
        self._badge.setToolTip(
            "Link group — panels in the same group follow the same symbol"
        )
        self._badge.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._badge)
        for g in GROUPS + [UNLINKED]:
            act = QAction(f"Group {g}" if g != UNLINKED else "Unlinked", menu)
            act.triggered.connect(lambda _=False, g=g: self.set_link_group(g))
            menu.addAction(act)
        self._badge.setMenu(menu)
        hl.addWidget(self._status_lbl)
        hl.addStretch(1)
        hl.addWidget(self._badge)
        outer.addWidget(header)

        # -- content area for subclasses
        content = QWidget(self)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        outer.addWidget(content, 1)

        self._ctx.symbol_changed.connect(self._on_ctx_changed)
        self._update_badge()

    # -- lifecycle (subclass API) -------------------------------------------

    def build(self) -> None:
        """Create widgets. Called once, after construction."""
        raise NotImplementedError

    def on_symbol(self, symbol: str) -> None:
        """Active symbol for this panel's link group changed. Optional."""

    def settings(self) -> dict:
        """Per-panel state persisted into the layout file. Optional."""
        return {}

    def restore(self, settings: dict) -> None:
        """Restore state produced by ``settings()``. Optional."""

    # -- data helpers (subclass API) ------------------------------------------

    def subscribe(
        self,
        topic: str,
        callback: Callable[[Any], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Subscribe to a DataHub topic. Cached value arrives immediately if
        available; auto-unsubscribed when the panel closes."""
        self._topics.add(topic)
        self._hub.subscribe(self, topic, callback, on_error or self._show_error)

    def unsubscribe_all(self) -> None:
        self._hub.unsubscribe_all(self)
        self._topics.clear()

    def set_symbol(self, symbol: str) -> None:
        """Publish a symbol click to this panel's link group."""
        if self._link_group == UNLINKED:
            # unlinked panels still navigate themselves
            self._apply_symbol(symbol.strip().upper())
            return
        self._ctx.set_symbol(self._link_group, symbol, source=self)
        # SymbolContext suppresses same-value signals; still apply locally
        self._apply_symbol(symbol.strip().upper())

    def set_status(self, text: str) -> None:
        self._status_lbl.setText(text)

    @property
    def current_symbol(self) -> str:
        return self._current_symbol

    @property
    def link_group(self) -> str:
        return self._link_group

    def set_link_group(self, group: str) -> None:
        self._link_group = group
        self._update_badge()
        if group != UNLINKED:
            sym = self._ctx.symbol(group)
            if sym:
                self._apply_symbol(sym)

    # -- internals -------------------------------------------------------------

    def _on_ctx_changed(self, group: str, symbol: str, source: object) -> None:
        if group != self._link_group or source is self:
            return
        self._apply_symbol(symbol)

    def _apply_symbol(self, symbol: str) -> None:
        if not symbol or symbol == self._current_symbol:
            return
        self._current_symbol = symbol
        try:
            self.on_symbol(symbol)
        except Exception:
            traceback.print_exc()

    def _show_error(self, error: str) -> None:
        self.set_status(f"⚠ {error}")

    def _update_badge(self) -> None:
        color = GROUP_COLORS.get(self._link_group, "#666666")
        linked = self._link_group != UNLINKED
        self._badge.setText(self._link_group if linked else "—")
        if linked:
            self._badge.setStyleSheet(
                f"QToolButton#groupBadge {{ background: {QColor(color).name()};"
                f" color: {BG}; font-size: 9px; font-weight: 700;"
                " border: 0; border-radius: 3px; padding: 2px 7px; }"
            )
        else:
            self._badge.setStyleSheet(
                "QToolButton#groupBadge { background: transparent; color: #565d67;"
                " font-size: 9px; font-weight: 700; border: 1px solid #333a45;"
                " border-radius: 3px; padding: 1px 6px; }"
            )
