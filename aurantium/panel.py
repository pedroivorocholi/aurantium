"""Panel plugin API: the ONLY surface a custom panel needs.

A panel is one Python class in one file::

    from aurantium.panel import Panel, register_panel

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

from PySide6.QtCore import (
    Property,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QAction, QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import motion
from .datahub import DataHub
from .theme import (
    BG,
    BORDER,
    BORDER_STRONG,
    CHROME_EDGE,
    CHROME_LOW,
    CHROME_TEXT_DIM,
    FG_MUTED,
    FOCUS,
    FONT_XS,
    RADIUS_SM,
    WEIGHT_BOLD,
)
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
    """Class decorator registering a Panel subclass under ``id``.

    ``category="Examples"`` is reserved for tutorial files: the class is
    annotated but NOT registered, so a stray copy of a template can never
    leak into the Panels menu. To publish a copied template, change its
    category (and id/title) along with the filename."""

    def deco(cls: Type["Panel"]) -> Type["Panel"]:
        cls.panel_id = id
        cls.panel_title = title
        cls.panel_category = category
        if category != "Examples":
            PanelRegistry.add(PanelMeta(id=id, title=title, category=category, cls=cls))
        return cls

    return deco


def _package_name(directory: Path) -> str | None:
    """Dotted module path if ``directory`` is a package on sys.path
    (e.g. aurantium/panels -> "aurantium.panels"), else None."""
    if not (directory / "__init__.py").exists():
        return None
    parts = [directory.name]
    parent = directory.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


def discover_package_panels(package: str) -> list[str]:
    """Import every submodule of an installed package (e.g. ``aurantium.panels``)
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
                mod_name = f"aurantium_user_panels_{path.stem}"
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
# Size classes
# --------------------------------------------------------------------------
#
# A dock panel has no single size: the same Watchlist is a narrow sidebar strip
# in one arrangement and a maximized full-screen table in another, and the user
# moves between them by dragging a splitter. Content laid out for one is broken
# at the others — crushed and elided when narrow, sprawling when wide.
#
# Panels get told which of three regimes they are in and reorganize, rather
# than scaling one layout until it stops working.

COMPACT = "compact"   # a sidebar strip: show the essentials only
REGULAR = "regular"   # a normal column: the full working set
WIDE = "wide"         # half a screen or more: room for secondary detail

#: The COMPACT ceiling is measured, not round: it sits just above the widest
#: control row any panel builds (the chart's range row, ~446px), so a panel
#: that has left COMPACT can always lay that row out without clipping.
COMPACT_MAX = 460
REGULAR_MAX = 820


def size_class_for(width: int) -> str:
    """The size class for a panel content width."""
    if width <= COMPACT_MAX:
        return COMPACT
    if width <= REGULAR_MAX:
        return REGULAR
    return WIDE


# --------------------------------------------------------------------------
# Shared display vocabulary
# --------------------------------------------------------------------------

#: The one glyph for "no value here".
#:
#: An em dash, not the ASCII hyphen the panels grew into using in ~77 places.
#: In a right-aligned monospaced numeric column — which is most of this app — a
#: hyphen sits exactly where a minus sign would, at the same width, in the same
#: ink. "-" and "-1.2%" begin identically, and a reader scanning a column of
#: losses has to stop and check whether a row is negative or absent. The em dash
#: cannot be mistaken for an operator, which is the whole reason typographers
#: use it for elision.
#:
#: Panels are migrated onto this incrementally; ``"-"`` survives in files not
#: yet converted.
NULL_GLYPH = "—"

#: The one spelling of "fetching".
#:
#: There were six — ``"loading…"``, ``"Loading…"``, ``f"loading {kind}…"``,
#: ``"Waiting for contract prices…"``, an HTML-italic variant, and
#: ``"Loading preview…"``. ``day_brief`` managed two of them on adjacent lines.
LOADING = "loading…"

#: Prefix for an error in the state slot. Four panels hand-rolled this glyph.
WARN_GLYPH = "⚠"

#: Empty-state titles that more than one panel needs. Panel-specific ones stay
#: in their panel — the point is one phrasing per *condition*, not a registry of
#: every string. "No symbol selected" and "No symbol linked" were both in use
#: for the identical condition, in six and two places respectively.
EMPTY_NO_SYMBOL = "No symbol selected"
EMPTY_NO_SYMBOL_HINT = "Click a ticker in any linked panel"


# --------------------------------------------------------------------------
# Panel header strip
# --------------------------------------------------------------------------

#: How long the link flash takes to decay back to the panel background.
#: Lives in motion.py with the rest of the app's motion budget.
FLASH_MS = motion.FLASH_MS


class _HeaderStrip(QWidget):
    """The slim context bar at the top of every panel.

    Painted rather than styled so it can carry the **link flash**: when a
    symbol arrives from this panel's link group — i.e. the user clicked a
    ticker in a *different* panel — the strip tints toward that group's color
    and decays back over ``FLASH_MS``.

    That flash is the one piece of motion aurantium adds, and it earns its
    place: cross-panel symbol linking is the product's whole differentiator,
    and without it a propagated update is indistinguishable from a panel that
    simply refreshed on its own. The tint says *this changed because of
    something you did over there*. It never moves or resizes anything, so it
    can't disturb a value being read.

    **Depth.** The strip used to paint ``BG`` — the identical true black as the
    table directly beneath it — so a panel was one undifferentiated plane from
    its title bar all the way down, and the symbol, status and link badge read
    as the first row of the data rather than as the panel's own chrome. It now
    paints ``CHROME_LOW``: chrome, one step down from the dock title bar above
    it, so the two together form a single cap over the data. The lit top edge
    and dark bottom hairline are the same two-hairline raised treatment the
    command bar and the dock title bars use, painted here rather than styled
    because this widget already owns its own paint.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("panelHeader")
        self._flash = 0.0
        self._tint = QColor(GROUP_COLORS[DEFAULT_GROUP])
        # One animation for the strip's whole life, retargeted per pulse. A
        # throwaway animation per pulse started with DeleteWhenStopped, while a
        # Python reference to it was kept, meant the second propagated symbol
        # called stop() on an object Qt had already destroyed and raised
        # instead of flashing.
        self._anim = QPropertyAnimation(self, b"flash", self)

    def get_flash(self) -> float:
        return self._flash

    def set_flash(self, value: float) -> None:
        self._flash = float(value)
        self.update()

    #: 0 = panel background, 1 = fully tinted. Animated, not set directly.
    flash = Property(float, get_flash, set_flash)

    def pulse(self, color: str) -> None:
        """Tint to ``color`` and decay back.

        A QPropertyAnimation retargets from the value currently on screen, so a
        rapid series of symbol clicks reads as a series of flashes rather than
        one smeared fade — the same reason CSS transitions beat keyframes for
        anything a user can trigger twice in a second.

        Under reduced motion there is no flash at all. An earlier version left
        the tint at full strength on the theory that reduced motion should keep
        the information — but with no duration there is nothing to decay, so it
        simply stayed coloured forever: a stuck visual state that stops meaning
        anything, which is worse than no animation. No information is lost by
        dropping it, because the header prints the symbol that arrived, which
        is what the flash was pointing at.
        """
        self._tint = QColor(color)
        self._anim.stop()
        self._anim.setDuration(motion.duration(FLASH_MS))
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        # Strong ease-out: full tint on the first frame — the moment the user is
        # actually watching — then a fast falloff. Qt's OutCubic is the weak
        # equivalent; OutQuint is the curve UI motion wants.
        self._anim.setEasingCurve(motion.EASE_OUT)
        self._anim.start()
        if self._anim.duration() == 0:
            self.set_flash(0.0)  # reduced motion: no flash, and nothing stuck

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        base = QColor(CHROME_LOW)
        if self._flash > 0.0:
            # Cap the mix well below the tint's full strength — this sits under
            # live data all day, and a saturated band would read as an alert.
            t = self._flash * 0.30
            base = QColor(
                round(base.red() + (self._tint.red() - base.red()) * t),
                round(base.green() + (self._tint.green() - base.green()) * t),
                round(base.blue() + (self._tint.blue() - base.blue()) * t),
            )
        p.fillRect(self.rect(), base)
        # Lit top edge, dark bottom edge: the raised-surface treatment shared
        # with the command bar and the dock title bars. The top edge is skipped
        # while the strip is tinted, because a highlight over a coloured flash
        # reads as a second, competing signal.
        if self._flash <= 0.0:
            p.setPen(QColor(CHROME_EDGE))
            p.drawLine(0, 0, self.width(), 0)
        p.setPen(QColor(BORDER))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


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

    #: May this panel exist as a free-floating window?
    #:
    #: Aurantium's rule is that panels move and snap but never tear off
    #: (app.py sets DockWidgetFloatable False), because a workspace of
    #: overlapping windows stops being a terminal. A panel opts out of that
    #: rule only when docking would actively harm it -- the Day Brief is the
    #: case that earned it: auto-docking a brief into the side of a tuned
    #: layout squeezes every other panel to open something the user will read
    #: once and close.
    #:
    #: This is a class attribute rather than an add_panel() argument on
    #: purpose: the dock feature has to be set identically when a saved layout
    #: is restored, and the layout spec carries no such flag.
    FLOATABLE: bool = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hub = DataHub.instance()
        self._ctx = SymbolContext.instance()
        self._link_group = DEFAULT_GROUP
        self._current_symbol = ""
        self._topics: set[str] = set()
        self._size_class = size_class_for(self.width())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Don't let the content dictate a floor on the panel's size. By default
        # a layout pushes its minimum up into the widget, so a chart with a
        # wide control row reported a ~605px minimum and simply refused to be
        # any narrower — which contradicts the dock manager's explicit
        # "panels can be dragged small" setting, and meant the size-class hook
        # below could never see a compact width. Panels reorganize to fit
        # instead of refusing to shrink.
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)

        # -- header strip: a slim context bar. The panel's NAME already lives
        # in the dock tab above, so we don't repeat it here. The bar carries
        # the symbol this panel is currently showing (left), live status next
        # to it, and the link-group badge (right).
        #
        # The symbol is the load-bearing addition: with four or five panels
        # open, "which name am I looking at" was answerable only by reading
        # each panel's own contents, and a panel that had fallen out of the
        # link group looked identical to one that hadn't. Printing it here
        # makes the linking legible at a glance — and makes a mis-grouped
        # panel obvious instead of silently wrong.
        header = _HeaderStrip(self)
        self._header = header
        header.setFixedHeight(21)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(9, 0, 6, 0)
        hl.setSpacing(8)
        self._symbol_lbl = QLabel("", header)
        self._symbol_lbl.setObjectName("panelSymbol")
        self._status_lbl = QLabel("", header)
        self._status_lbl.setObjectName("panelStatus")
        # A second, separate slot for *state* (loading / stale / error).
        #
        # These used to share the status label, and sharing one un-prioritised
        # slot between four writers means a later write silently destroys an
        # earlier one. ``profile`` demonstrated it exactly: every successful
        # load ended with ``set_status(sector or "")``, which blanked any error
        # posted a moment before. Two slots with disjoint writers make that
        # structurally impossible, and leave ``set_status`` — and its ~70 call
        # sites — untouched.
        self._state_lbl = QLabel("", header)
        self._state_lbl.setObjectName("panelState")
        self._state_lbl.setVisible(False)
        self._state_targets: list = []
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
        hl.addWidget(self._symbol_lbl)
        hl.addWidget(self._status_lbl)
        hl.addWidget(self._state_lbl)
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

    def on_size_class(self, size_class: str) -> None:
        """The panel crossed into a different size regime — ``COMPACT``,
        ``REGULAR`` or ``WIDE``. Optional.

        Reorganize here: hide secondary controls and columns in ``COMPACT``,
        bring them back above it. Called only when the class actually changes,
        never on every pixel of a splitter drag, so it is safe to do real
        layout work in it.
        """

    @property
    def size_class(self) -> str:
        """Which size regime this panel is currently in.

        Derived from the live width rather than cached: Qt does not deliver a
        resize event to a hidden widget, so a cached value would be stale for
        any panel that was resized before it was shown — which is exactly what
        happens while a saved layout is being restored.
        """
        return size_class_for(self.width())

    def _sync_size_class(self) -> None:
        """Fire :meth:`on_size_class` if the regime changed. ``_size_class``
        tracks what the panel was last told, not what it currently is."""
        current = self.size_class
        if current != self._size_class:
            self._size_class = current
            try:
                self.on_size_class(current)
            except Exception:
                traceback.print_exc()  # a bad panel must not break the drag

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._sync_size_class()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Catches the restore path: a panel resized while hidden gets no resize
        # event, so without this it would first lay out for its real size only
        # after the user happened to drag it.
        super().showEvent(event)
        self._sync_size_class()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Defensive teardown when the panel's dock closes: release DataHub
        subscriptions and detach from the SymbolContext singleton so a
        lingering widget can't keep receiving updates. Docks are
        DeleteOnClose and Qt already auto-disconnects a bound-method slot when
        its receiver is destroyed — this is belt-and-braces insurance."""
        try:
            self.unsubscribe_all()
        except Exception:
            pass
        try:
            self._ctx.symbol_changed.disconnect(self._on_ctx_changed)
        except (RuntimeError, TypeError):
            pass  # already disconnected / never connected
        super().closeEvent(event)

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
        """The *info* slot: counts, "as of 16:02", undo receipts.

        Panel-owned and freely overwritten. Deliberately unchanged — every
        existing caller keeps working, and nothing it writes can clobber a
        loading or error state, because those live in their own label now.
        """
        self._status_lbl.setText(text)

    # -- panel state -----------------------------------------------------------
    #
    # ``Panel`` previously offered ``set_status`` and nothing else, so every
    # panel invented its own vocabulary: six spellings of "loading", seventeen
    # ways to say a table was empty, and nine panels that said nothing at all.
    # These five methods are the shared vocabulary; ``LOADING`` and friends
    # above are the shared strings.

    def register_state_target(self, widget) -> None:
        """Opt a widget into this panel's loading / empty state.

        Called once in ``build()``. Opt-in rather than ``findChildren`` magic:
        a panel with two tables should choose which one speaks for it, and a
        panel that registers nothing still gets header-level state instead of
        breaking.
        """
        if widget is not None and widget not in self._state_targets:
            self._state_targets.append(widget)

    def _set_state(self, text: str, severity: str = "") -> None:
        self._state_lbl.setText(text)
        self._state_lbl.setVisible(bool(text))
        self._state_lbl.setProperty("severity", severity or None)
        # A dynamic property does not re-run the stylesheet on its own.
        style = self._state_lbl.style()
        if style is not None:
            style.unpolish(self._state_lbl)
            style.polish(self._state_lbl)

    def set_loading(self, on: bool = True) -> None:
        """Mark the panel as fetching.

        Forwards to any registered target that can show a veil —
        ``MarketTable`` has had a built, motion-budgeted loading overlay since
        it was written and not one panel ever called it.
        """
        self._set_state(LOADING if on else "", "loading" if on else "")
        for target in self._state_targets:
            setter = getattr(target, "set_loading", None)
            if callable(setter):
                setter(on)

    def set_empty(self, title: str, hint: str = "") -> None:
        """Say what is missing, and what to do about it."""
        for target in self._state_targets:
            setter = getattr(target, "set_empty_text", None)
            if callable(setter):
                setter(title, hint)

    def set_stale(self, label: str = "") -> None:
        """Mark the shown data as older than it should be.

        ``datahub`` serves stale and disk-cached values deliberately
        ("stale-is-better-than-blank"), and until now said so nowhere: a price
        cached last Tuesday looked exactly like a live tick.
        """
        self._set_state(label, "stale" if label else "")

    def set_error(self, message: str) -> None:
        """Report a failure, and stop showing the fetch as in flight.

        Clearing the veil here is not cosmetic: an error is how a fetch most
        often ends, and a panel that failed while still dimmed under "loading…"
        tells the user to keep waiting for something that is never coming.
        """
        for target in self._state_targets:
            setter = getattr(target, "set_loading", None)
            if callable(setter):
                setter(False)
        self._set_state(f"{WARN_GLYPH} {message}", "error")

    def clear_state(self) -> None:
        """Back to nothing to report. Does not touch the info slot."""
        self._set_state("")
        for target in self._state_targets:
            setter = getattr(target, "set_loading", None)
            if callable(setter):
                setter(False)

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
        # Arrived from elsewhere in the link group — mark the propagation.
        # (A symbol the panel set itself doesn't flash: the user is already
        # looking at the click they just made.)
        if symbol and symbol != self._current_symbol:
            self._header.pulse(GROUP_COLORS.get(group, BG))
        self._apply_symbol(symbol)

    def _apply_symbol(self, symbol: str) -> None:
        if not symbol or symbol == self._current_symbol:
            return
        self._current_symbol = symbol
        self._symbol_lbl.setText(symbol)
        try:
            self.on_symbol(symbol)
        except Exception:
            traceback.print_exc()

    def _show_error(self, error: str) -> None:
        """Default ``on_error`` for every subscription (see :meth:`subscribe`).

        Writes to the state slot, not the status slot, so a panel reporting a
        successful row count a moment later cannot erase it.
        """
        self.set_error(error)

    def _update_badge(self) -> None:
        """Paint the link-group badge.

        It used to be a solid amber chip — which made the least important
        element on screen the loudest, and spent the accent color that
        elsewhere means "this is data". It is now the group's color as an
        outline and label on the panel surface: still unmistakable, no longer
        competing with the numbers. Hovering fills it, so it still reads as
        something you can click.

        Both states derive from the palette (the unlinked colors used to be
        hardcoded dark-theme greys, which were wrong on the light theme).

        A per-widget stylesheet outranks the app sheet on every property it
        names, and ``QToolButton#groupBadge`` is an id selector, so the global
        ``QToolButton[kbFocus="true"]`` focus rule lost to it and the badge was
        the one control in the app that could be tabbed to and show nothing.
        The full state set is therefore spelled out here: hover fills, pressed
        holds that fill while ``press.py`` eases the shadow over it, and
        keyboard focus takes the neutral ring — ON_ACCENT on the filled hover
        state, where the neutral ring would not be readable.

        Sizes and radii come from the scale in ``theme.py``; they used to be
        three inline literals that drifted out of step with it.
        """
        linked = self._link_group != UNLINKED
        color = QColor(GROUP_COLORS.get(self._link_group, FG_MUTED)).name()
        self._badge.setText(self._link_group if linked else "—")
        base = (
            f" font-size: {FONT_XS}px; font-weight: {WEIGHT_BOLD};"
            f" border-radius: {RADIUS_SM}px; padding: 1px 6px;"
        )
        if linked:
            self._badge.setStyleSheet(
                f"QToolButton#groupBadge {{ background: transparent; color: {color};"
                f"{base} border: 1px solid {color}; }}"
                f"QToolButton#groupBadge:hover, QToolButton#groupBadge:pressed"
                f" {{ background: {color}; color: {BG}; }}"
                f'QToolButton#groupBadge[kbFocus="true"]'
                f" {{ border-color: {FOCUS}; }}"
                f"QToolButton#groupBadge:disabled"
                f" {{ color: {FG_MUTED}; border-color: {BORDER}; }}"
            )
        else:
            self._badge.setStyleSheet(
                "QToolButton#groupBadge { background: transparent;"
                f" color: {FG_MUTED};{base}"
                f" border: 1px solid {BORDER_STRONG}; }}"
                f"QToolButton#groupBadge:hover, QToolButton#groupBadge:pressed"
                f" {{ color: {CHROME_TEXT_DIM}; border-color: {CHROME_TEXT_DIM}; }}"
                f'QToolButton#groupBadge[kbFocus="true"]'
                f" {{ border-color: {FOCUS}; }}"
                f"QToolButton#groupBadge:disabled"
                f" {{ color: {FG_MUTED}; border-color: {BORDER}; }}"
            )
