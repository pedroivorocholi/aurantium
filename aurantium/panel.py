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
Panels ▸ Add Panel menu. See docs/guides/PANELS.md.
"""

from __future__ import annotations

import importlib.util
import math
import pkgutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Type

from PySide6.QtCore import (
    Property,
    QEvent,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import motion
from .datahub import DataHub
from .guide import GuideSection, PanelGuide  # noqa: F401 (re-exported for panels)
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
    HEADER_BLUE,
    RADIUS_SM,
    SPACE_LG,
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
    #: retired id → (current id, settings that reproduce the retired panel)
    _aliases: dict[str, tuple[str, dict]] = {}

    @classmethod
    def add(cls, meta: PanelMeta) -> None:
        cls._panels[meta.id] = meta

    @classmethod
    def get(cls, panel_id: str) -> Optional[PanelMeta]:
        return cls._panels.get(panel_id)

    @classmethod
    def add_alias(cls, old_id: str, new_id: str, settings: dict) -> None:
        """Keep a retired panel id openable: saved layouts, presets and
        ``/add <old id>`` get ``new_id`` restored with ``settings``."""
        cls._aliases[old_id] = (new_id, dict(settings))

    @classmethod
    def resolve(cls, panel_id: str) -> tuple[str, dict]:
        """``(panel_id, extra settings)`` — a retired id mapped to its
        replacement, anything else returned as-is."""
        if panel_id not in cls._panels and panel_id in cls._aliases:
            new_id, settings = cls._aliases[panel_id]
            return new_id, dict(settings)
        return panel_id, {}

    @classmethod
    def known_ids(cls) -> set[str]:
        """Every id a layout may name: registered panels plus retired aliases."""
        return set(cls._panels) | set(cls._aliases)

    @classmethod
    def all(cls) -> list[PanelMeta]:
        return sorted(cls._panels.values(), key=lambda m: (m.category, m.title))


def register_panel(id: str, title: str, category: str = "General", listed: bool = True):
    """Class decorator registering a Panel subclass under ``id``.

    ``category="Examples"`` is reserved for tutorial files: the class is
    annotated but NOT registered, so a stray copy of a template can never
    leak into the Panels menu. To publish a copied template, change its
    category (and id/title) along with the filename.

    ``listed=False`` annotates without registering too: for a panel that only
    lives as a tab inside another (the Company panel's Profile, Key Metrics,
    Financials and Earnings)."""

    def deco(cls: Type["Panel"]) -> Type["Panel"]:
        cls.panel_id = id
        cls.panel_title = title
        cls.panel_category = category
        if category != "Examples" and listed:
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


def owning_panel(widget) -> Optional["Panel"]:
    """The innermost Panel a widget lives in, or None."""
    w = widget
    while w is not None:
        if isinstance(w, Panel):
            return w
        w = w.parentWidget()
    return None


def add_guide_action(menu: QMenu, widget) -> None:
    """Shared components (MarketTable, …) call this on their context menus:
    the panel they sit in appends its "Quick guide" entry."""
    panel = owning_panel(widget)
    if panel is not None:
        panel.add_guide_action(menu)


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


class _LinkBadge(QToolButton):
    """The link-group badge: a chain glyph, plus the group letter when linked.

    Painted rather than styled, for two reasons. A stylesheet ``:hover`` swaps
    colours in one frame, and the badge should fill under the pointer the way
    the rest of the chrome answers it. And Qt only uses a tool button's
    hover-mode icon when the button is auto-raise, so the glyph stayed in the
    group colour on top of a fill of that same colour and vanished. One
    ``hover`` value (0 = resting, 1 = under the pointer) now drives the fill,
    the glyph and the letter together, so they cannot disagree.

    Resting, it is the group's colour as an outline and label on the panel
    surface: the least important thing on screen should not be the loudest.
    Hovered, it fills with that colour and the content turns to ``BG``. It
    stays filled while its menu is open, which is what the pointer went there
    for.
    """

    ICON = 11
    GAP = 3

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("groupBadge")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._group = DEFAULT_GROUP
        self._hover = 0.0
        self._glyphs: dict[str, Any] = {}
        # one animation for the badge's life, retargeted from the value on
        # screen, so a pointer flicking across it never snaps
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setEasingCurve(motion.EASE_OUT)

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hover = Property(float, get_hover, set_hover)

    def set_group(self, group: str) -> None:
        self._group = group
        self.updateGeometry()
        self.update()

    def _ease_to(self, target: float) -> None:
        if self._anim.state() == QPropertyAnimation.State.Running and self._anim.endValue() == target:
            return
        self._anim.stop()
        # in at the press speed, out a beat slower: the same asymmetry as press
        # feedback — the arrival is watched, the departure is not
        ms = motion.duration(motion.PRESS_IN_MS * 4 // 3 if target > self._hover else motion.PRESS_OUT_MS)
        if ms == 0 or self._hover == target:
            self.set_hover(target)
            return
        self._anim.setDuration(ms)
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def menu_closed(self) -> None:
        if not self.underMouse():
            self._ease_to(0.0)

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().enterEvent(event)
        if self.isEnabled():
            self._ease_to(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        menu = self.menu()
        if menu is None or not menu.isVisible():
            self._ease_to(0.0)

    def colors(self) -> dict:
        """(fill, border, content) for the current state; the fill may be
        transparent."""
        from .color import mix_oklab

        t = self._hover if self.isEnabled() else 0.0
        if self._group != UNLINKED:
            tone = GROUP_COLORS.get(self._group, FG_MUTED)
            fill = QColor(tone)
            fill.setAlphaF(t)
            border, content = QColor(tone), QColor(mix_oklab(tone, BG, t))
        else:
            fill = QColor(0, 0, 0, 0)
            border = QColor(mix_oklab(BORDER_STRONG, CHROME_TEXT_DIM, t))
            content = QColor(mix_oklab(FG_MUTED, CHROME_TEXT_DIM, t))
        if not self.isEnabled():
            border, content = QColor(BORDER), QColor(FG_MUTED)
        if self.property("kbFocus"):
            border = QColor(FOCUS)
        return {"fill": fill, "border": border, "content": content}

    def _font(self):
        f = QFont(self.font())
        f.setPixelSize(FONT_XS)
        f.setWeight(QFont.Weight.Bold)
        return f

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        fm = QFontMetrics(self._font())
        w = 4 + self.ICON + 4
        if self._group != UNLINKED:
            w += self.GAP + fm.horizontalAdvance(self._group) + 1
        return QSize(w, max(self.ICON, fm.height()) + 4)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return self.sizeHint()

    def _glyph(self, color: QColor):
        from . import icons

        key = color.name()
        pm = self._glyphs.get(key)
        if pm is None:
            if len(self._glyphs) > 48:  # one per animation frame colour
                self._glyphs.clear()
            pm = icons.pixmap("link", key, icons.device_pixel_ratio(self), self.ICON)
            self._glyphs[key] = pm
        return pm

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        c = self.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(c["border"], 1))
        p.setBrush(c["fill"] if c["fill"].alpha() else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_SM, RADIUS_SM)
        x = 4
        p.drawPixmap(x, (self.height() - self.ICON) // 2, self._glyph(c["content"]))
        if self._group != UNLINKED:
            p.setFont(self._font())
            p.setPen(c["content"])
            text = QRectF(x + self.ICON + self.GAP, 0, self.width(), self.height())
            p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._group)
        p.end()


# --------------------------------------------------------------------------
# Workspace skeleton
# --------------------------------------------------------------------------
#
# A restored workspace used to come up as a dozen blank panels that filled in
# one by one. Each panel still loading now shows its own outline — a table's
# header band and rows, a plot's gridlines — at its real place in the layout,
# breathing gently (motion.py, "Loading pulse") until its data arrives, then
# fading into the real thing.

#: Bar lengths for a table skeleton's rows, as fractions of the width. Uneven
#: on purpose — identical bars read as a progress meter, not as rows of text.
_SKELETON_ROW_FRACTIONS = (0.62, 0.44, 0.71, 0.53, 0.66, 0.4, 0.58)
#: Row pitch when the panel has no table to measure one from.
_SKELETON_ROW_PX = 22
#: Horizontal gridlines on a plot skeleton.
_SKELETON_GRIDLINES = 4


class _SkeletonPulse:
    """The one animation every skeleton breathes on. Runs only while at least
    one skeleton is up, so a loaded workspace costs no timer at all."""

    def __init__(self) -> None:
        self.anim = QVariantAnimation()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._tick)
        self.value = 1.0
        self._live: dict[int, QWidget] = {}

    def running(self) -> bool:
        return self.anim.state() == QVariantAnimation.State.Running

    def add(self, skeleton: QWidget) -> None:
        key = id(skeleton)
        self._live[key] = skeleton
        # a skeleton destroyed with its panel never gets to say goodbye
        skeleton.destroyed.connect(lambda *_, k=key: self._drop(k))
        if not self.running() and motion.duration(motion.SKELETON_PULSE_MS):
            self.anim.setDuration(motion.SKELETON_PULSE_MS)
            self.anim.start()

    def remove(self, skeleton: QWidget) -> None:
        self._drop(id(skeleton))

    def _drop(self, key: int) -> None:
        self._live.pop(key, None)
        if not self._live:
            try:
                self.anim.stop()
            except RuntimeError:
                pass  # shutdown: the animation went before its last skeleton
            self.value = 1.0

    def _tick(self, t) -> None:
        # one full breath per loop: 1.0 → LOW → 1.0 on a cosine, which eases
        # at both turns (a linear ramp reads as a blink at the bottom)
        low = motion.SKELETON_PULSE_LOW
        self.value = low + (1.0 - low) * (0.5 + 0.5 * math.cos(2 * math.pi * float(t)))
        for key, skeleton in list(self._live.items()):
            try:
                skeleton.update()
            except RuntimeError:
                self._drop(key)


_pulse: Optional[_SkeletonPulse] = None
#: Skeletons fading out; the tests drive their fades to the end.
_retiring: dict[int, QWidget] = {}


def _skeleton_pulse() -> _SkeletonPulse:
    global _pulse
    if _pulse is None:
        _pulse = _SkeletonPulse()
    return _pulse


def _skeleton_anims_for_tests() -> list:
    anims = []
    for skeleton in list(_retiring.values()):
        try:
            anim = getattr(skeleton, "_aurantium_fade", None)
        except RuntimeError:
            continue
        if anim is not None:
            anims.append(anim)
    return anims


class _Skeleton(QWidget):
    """A panel body's outline, laid over it until its data arrives."""

    def __init__(
        self, body: QWidget, on_cap: Callable[[], None], table: Optional[bool] = None
    ) -> None:
        super().__init__(body)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view = next(iter(body.findChildren(QAbstractItemView)), None)
        # a table's outline when the body holds one, unless told outright
        # (a placeholder has no body yet to look into)
        self._table = view is not None if table is None else table
        self._row_px = _SKELETON_ROW_PX
        header = getattr(view, "verticalHeader", None)
        if callable(header) and header() is not None:
            self._row_px = max(header().defaultSectionSize(), 1)
        self.setGeometry(body.rect())
        body.installEventFilter(self)
        # the hard stop: a panel whose data never comes is not left pulsing
        self._cap = QTimer(self)
        self._cap.setSingleShot(True)
        self._cap.setInterval(motion.SKELETON_MAX_MS)
        self._cap.timeout.connect(on_cap)
        self._cap.start()
        self.show()
        self.raise_()
        _skeleton_pulse().add(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Resize:
            try:
                self.setGeometry(obj.rect())
            except RuntimeError:
                pass
        return False

    def retire(self) -> None:
        """Fade into the real panel underneath, then go."""
        _skeleton_pulse().remove(self)
        self._cap.stop()
        if motion.duration(motion.OVERLAY_MS) == 0:
            self.hide()
            self.deleteLater()
            return
        key = id(self)
        _retiring[key] = self
        self.destroyed.connect(lambda *_, k=key: _retiring.pop(k, None))
        anim = motion.fade(self, 0.0, hide_when_done=True)
        anim.finished.connect(self.deleteLater)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        try:
            # opaque ground, so the empty widgets underneath never show through
            p.fillRect(self.rect(), QColor(BG))
            pulse = _skeleton_pulse()
            p.setOpacity(pulse.value if pulse.running() else 1.0)
            w, h, row = self.width(), self.height(), self._row_px
            if self._table:
                p.fillRect(0, 0, w, row, QColor(HEADER_BLUE))
                bar_h = max(row // 3, 2)
                y, i = row, 0
                while y + row <= h:
                    frac = _SKELETON_ROW_FRACTIONS[i % len(_SKELETON_ROW_FRACTIONS)]
                    p.fillRect(
                        SPACE_LG, y + (row - bar_h) // 2,
                        int((w - 2 * SPACE_LG) * frac), bar_h, QColor(CHROME_LOW),
                    )
                    y += row
                    i += 1
            else:
                pen = QPen(QColor(BORDER_STRONG))
                pen.setWidth(1)
                p.setPen(pen)
                for k in range(1, _SKELETON_GRIDLINES + 1):
                    y = int(h * k / (_SKELETON_GRIDLINES + 1))
                    p.drawLine(SPACE_LG, y, w - SPACE_LG, y)
        finally:
            p.end()


class LoadingPlaceholder(QWidget):
    """What a dock holds while a workspace is restored, until its panel is
    built: the panel's outline, breathing.

    A workspace used to be restored by building every panel, docking each one
    as a tab of the same area, and only then applying the saved arrangement —
    so the window showed the panels piling up one over the other. Now each
    dock is created holding one of these, the arrangement is applied at once,
    and the real panels replace them one by one (MainWindow.apply_layout).
    """

    def __init__(self, panel_cls: type) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table = getattr(panel_cls, "SKELETON", "table") != "plot"
        self._skeleton: Optional[_Skeleton] = _Skeleton(self, self._stop, table=table)

    def _stop(self) -> None:
        """The hard stop: a panel that takes this long still stops pulsing."""
        skeleton, self._skeleton = self._skeleton, None
        if skeleton is not None:
            _skeleton_pulse().remove(skeleton)
            skeleton.update()


class _PanelLayout(QVBoxLayout):
    """The panel's outermost layout: fills whatever height the dock gives.

    Wrapping rows (``ResponsiveRow``, chip bars) make a layout report
    height-for-width. QtAds wraps every panel in a ``QScrollArea``, and a
    scroll area sizes height-for-width content to its *preferred* height for
    that width rather than to the viewport — so a chart, whose plot prefers
    to be large, grew taller than its dock and was cut off. Hiding
    height-for-width here, at the top, makes the panel take the dock's height
    while every layout inside still wraps and shares that height as before.
    """

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return False


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

    #: Is this panel read-and-close, opened as a window parked at the screen edge?
    #:
    #: Every panel can be dragged out into a window of its own (that is how
    #: panels reach a second monitor; see MainWindow._settle_new_window). A
    #: FLOATABLE panel additionally opens parked against the right edge of the
    #: screen rather than centred over the workspace -- the Day Brief is the
    #: case that earned it: it opens in response to a click on the chart, and
    #: landing on top of the chart would hide what the user is asking about.
    FLOATABLE: bool = False

    #: The outline the panel loads behind: ``"table"`` (header and rows) or
    #: ``"plot"`` (gridlines). A panel with a table in its body is drawn as one
    #: whatever this says; it decides the placeholder a dock holds before the
    #: panel is built (see ``LoadingPlaceholder``).
    SKELETON: str = "table"

    #: This panel's quick guide — purpose, linking, key interactions, fields
    #: and shortcuts (see ``guide.PanelGuide``). Required for every registered
    #: panel: the F1 window builds the panel's page from it, the header's ``?``
    #: opens that page, and ``tests/panels/test_panel_guides.py`` fails without it.
    GUIDE: Optional[PanelGuide] = None

    #: ``(key, tab label, panel class)`` for a host whose tabs are themselves
    #: panels (the Company panel). Each hosted class's GUIDE becomes a page
    #: nested under the host's in the F1 window.
    HOSTED_TABS: tuple = ()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hub = DataHub.instance()
        self._ctx = SymbolContext.instance()
        self._link_group = DEFAULT_GROUP
        self._current_symbol = ""
        self._topics: set[str] = set()
        self._size_class = size_class_for(self.width())
        #: the panel this one is a tab inside, if any (see ``panels/company.py``).
        #: A hosted panel hides its own header; its status, state and symbol
        #: clicks go through the host's instead.
        self._host: Optional["Panel"] = None

        outer = _PanelLayout(self)
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
        # info and state text clip rather than set the panel's width: a long
        # status must never make a narrow dock scroll sideways
        # (an explicit minimum keeps the natural width whenever there's room)
        for lbl in (self._status_lbl, self._state_lbl):
            lbl.setMinimumWidth(1)
        self._badge = _LinkBadge(header)
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
        menu.aboutToHide.connect(self._badge.menu_closed)
        # The panel's own quick guide, one click from the panel itself. Beside
        # the badge because both are about the panel rather than its data.
        self._guide_btn = QToolButton(header)
        self._guide_btn.setObjectName("guideButton")
        from . import icons
        from .theme import CHROME_TEXT, FG_MUTED

        icons.set_icon(self._guide_btn, "help", FG_MUTED, hover_color=CHROME_TEXT)
        self._guide_btn.setAccessibleName("Quick guide")
        self._guide_btn.setToolTip("Quick guide to this panel (Shift+F1)")
        self._guide_btn.clicked.connect(self.open_guide)
        hl.addWidget(self._symbol_lbl)
        hl.addWidget(self._status_lbl)
        hl.addWidget(self._state_lbl)
        hl.addStretch(1)
        hl.addWidget(self._guide_btn)
        hl.addWidget(self._badge)
        outer.addWidget(header)

        # -- content area for subclasses
        content = QWidget(self)
        self._body = content
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        outer.addWidget(content, 1)
        self._skeleton: Optional[_Skeleton] = None

        self._ctx.symbol_changed.connect(self._on_ctx_changed)
        self._update_badge()

    # -- quick guide -----------------------------------------------------------

    def guide_section(self) -> str:
        """The F1 page for this panel. A hosted tab answers with its host's
        page for that tab; a host may override to point at its current tab."""
        if self._host is not None:
            return f"{self._host.panel_id}:{self.panel_id}"
        return self.panel_id

    def open_guide(self) -> None:
        """Open the F1 window on this panel's page."""
        from . import guide

        section = self.guide_section()
        if guide.open_guide_hook is not None:
            guide.open_guide_hook(section)
            return
        from .onboarding_dialog import OnboardingDialog

        OnboardingDialog(self.window(), section).exec()

    def add_guide_action(self, menu: QMenu) -> None:
        """Append the "Quick guide" entry to a context menu this panel builds.
        Every panel menu ends with it, so help is one right-click away."""
        title = self.panel_title or "this panel"
        if self._host is not None:
            title = self._host.panel_title or title
        if menu.actions():
            menu.addSeparator()
        act = menu.addAction(f"Quick guide: {title}")
        act.triggered.connect(self.open_guide)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Right-click anywhere a child doesn't claim: the panel's own menu.
        Widgets with their own menus (tables, charts, tiles) add the same
        guide entry through :func:`add_guide_action`."""
        menu = QMenu(self)
        self.add_guide_action(menu)
        menu.exec(event.globalPos())

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
        if self._host is not None:
            self._host.set_symbol(symbol)
            return
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
        if self._host is not None:
            self._host._child_header_changed(self)

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
        if severity != "loading":
            self._skeleton_done()  # data, an error, stale, or nothing to say
        self._state_lbl.setText(text)
        self._state_lbl.setVisible(bool(text))
        self._state_lbl.setProperty("severity", severity or None)
        # A dynamic property does not re-run the stylesheet on its own.
        style = self._state_lbl.style()
        if style is not None:
            style.unpolish(self._state_lbl)
            style.polish(self._state_lbl)
        if self._host is not None:
            self._host._child_header_changed(self)

    def _child_header_changed(self, child: "Panel") -> None:
        """A hosted child wrote its status or state. Hosts override."""

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

    def show_skeleton(self) -> None:
        """Stand this panel's outline in for it while its first data loads.

        Called by the window after restoring a workspace, once every panel is
        at its real place and size. A panel not loading — no fetch, or served
        from the cache already — gets nothing: its content is the outline.
        """
        if self._skeleton is not None or self._host is not None:
            return
        if self._state_lbl.text() != LOADING:
            return
        self._skeleton = _Skeleton(self._body, self._skeleton_done)

    def _skeleton_done(self) -> None:
        skeleton, self._skeleton = getattr(self, "_skeleton", None), None
        if skeleton is not None:
            try:
                skeleton.retire()
            except RuntimeError:
                pass  # destroyed with the panel

    def set_empty(self, title: str, hint: str = "") -> None:
        """Say what is missing, and what to do about it."""
        self._skeleton_done()
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
        """Point the link-group badge at the current group. ``_LinkBadge``
        paints every state itself (see its docstring): the outline, the
        animated hover fill, keyboard focus and disabled all derive from the
        palette and the group's colour."""
        self._badge.set_group(self._link_group)
        if self._link_group != UNLINKED:
            self._badge.setToolTip(
                f"Link group {self._link_group} — panels in the same group follow the same symbol"
            )
        else:
            self._badge.setToolTip("Not linked — choose a link group to follow a symbol")
