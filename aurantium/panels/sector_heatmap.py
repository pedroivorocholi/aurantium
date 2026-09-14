"""Sector Heatmap panel — a grid of colored tiles, one per S&P sector ETF.
Tile background is interpolated between DOWN (red, <= -2%) through neutral
BG_ALT (0%) to UP (green, >= +2%). Click a tile to navigate linked panels.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..components import (
    SECTOR_ETF_ENTRIES,
    EditorColumn,
    EditorSection,
    open_add_picker,
    open_list_editor,
)
from ..color import mix_oklab, readable_on
from ..panel import NULL_GLYPH, Panel, register_panel
from ..theme import BG, BG_ALT, BORDER_STRONG, DOWN, FG_DIM, FG_MUTED, UP
from ..undo import UndoStack

DEFAULT_TILES = [
    ["Technology", "XLK"],
    ["Financials", "XLF"],
    ["Health Care", "XLV"],
    ["Cons Discretionary", "XLY"],
    ["Cons Staples", "XLP"],
    ["Energy", "XLE"],
    ["Industrials", "XLI"],
    ["Materials", "XLB"],
    ["Real Estate", "XLRE"],
    ["Utilities", "XLU"],
    ["Comm Services", "XLC"],
]

GRID_COLS = 3
PCT_CLAMP = 2.0  # +/- 2% maps to full saturation


def _pct_of(change_pct: Any) -> float | None:
    """``change_pct`` as a float, or None when there is no reading at all."""
    if change_pct is None:
        return None
    try:
        return float(change_pct)
    except (TypeError, ValueError):
        return None


def _tile_color(change_pct: Any) -> QColor:
    """The tile fill for a reading, or the *no-data* fill for a missing one.

    Two changes from the original.

    **No data is not neutral.** Both a missing reading and a dead-flat 0.00%
    used to return ``BG_ALT``, so a total provider outage rendered as "every
    sector is exactly flat" — a wrong answer stated confidently, which is worse
    than an obviously broken one. The fill is the pre-attentive channel here;
    the figure underneath differs ("—" vs "+0.00%") but nobody reads eleven
    figures to discover the feed is down. No-data now takes the panel surface
    ``BG``, which is off the ramp by construction (the ramp starts at
    ``BG_ALT``). On the light theme that fill alone is not enough separation —
    see :func:`_has_reading` for why, and for the channels that carry it
    instead.

    **The blend is perceptual.** ``mix_oklab`` rather than averaging sRGB bytes.
    A modest gain on this ramp — see that function — but a ramp whose whole job
    is encoding magnitude should not have its evenness depend on which two
    colours it happens to run between.
    """
    pct = _pct_of(change_pct)
    if pct is None:
        return QColor(BG)
    end = UP if pct >= 0 else DOWN
    return QColor(mix_oklab(BG_ALT, end, abs(pct) / PCT_CLAMP))


def _tile_ink(change_pct: Any) -> str:
    """The label colour for a tile, chosen against that tile's own fill.

    The three labels were hardcoded ``#f2f2f2`` / ``#e0e0e0`` / ``#ffffff``.
    That is defensible at the saturated end of the dark ramp and indefensible
    everywhere else: on the light theme eight of eleven steps put white text at
    under 3:1, bottoming out at **1.09:1** on a flat tile — the figure was
    simply not there. Picking per tile takes the worst case to 4.67:1.
    """
    return readable_on(_tile_color(change_pct).name())


def _has_reading(change_pct: Any) -> bool:
    """Whether this tile has a value at all.

    The distinction cannot be carried by fill. On the dark theme "no data"
    (``BG``, near-black) sits ΔE 15.4 from a flat 0.00% (``BG_ALT``) and reads
    clearly. On the light theme the same two are ΔE **3.0** — and pushing them
    apart needs a fill about a third of the way to ``FG_MUTED``, which is a
    mid-grey that reads as *a value on the ramp*, not as an absence. The two
    themes want opposite directions and neither answer is honest.

    So absence is deliberately encoded off the colour channel: a dashed outline,
    the null glyph instead of a percentage, and muted ink. That is the same rule
    the up/down ticks already follow with ▲/▼ — when colour cannot carry a
    distinction on its own, it does not have to.
    """
    return _pct_of(change_pct) is not None


def _fmt_pct(value: Any) -> str:
    pct = _pct_of(value)
    return NULL_GLYPH if pct is None else f"{pct:+.2f}%"


class _Tile(QFrame):
    """A single clickable sector tile."""

    clicked = Signal(str)
    menu_requested = Signal(str, object)  # symbol, global pos

    #: Corner radius, matching the theme's existing 3px rather than the 4px this
    #: tile used to invent.
    RADIUS = 3

    def __init__(self, label: str, symbol: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbol = symbol
        self._fill = QColor(BG)
        self._has_data = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(70, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        # Backgrounds must be explicitly transparent: theme.py's blanket
        # ``QWidget`` rule would otherwise paint each label with a flat BG
        # rectangle punched out of the tile fill. Same reason the panel header
        # strip's children are transparent.
        self.name_lbl = QLabel(label, self)
        self.name_lbl.setWordWrap(True)
        self.symbol_lbl = QLabel(symbol, self)
        self.pct_lbl = QLabel(NULL_GLYPH, self)
        self._ink = ""
        self._apply_ink(readable_on(self._fill.name()))

        layout.addWidget(self.name_lbl)
        layout.addWidget(self.symbol_lbl)
        layout.addStretch(1)
        layout.addWidget(self.pct_lbl)

        self.set_change_pct(None)

    def _apply_ink(self, ink: str) -> None:
        """Re-colour the three labels. Guarded on change: a stylesheet write is
        a full reparse, and this used to run on every tick."""
        if ink == self._ink:
            return
        self._ink = ink
        self.name_lbl.setStyleSheet(
            f"background: transparent; color: {ink}; font-weight: 700; font-size: 11px;"
        )
        self.symbol_lbl.setStyleSheet(
            f"background: transparent; color: {ink}; font-size: 10px;"
        )
        self.pct_lbl.setStyleSheet(
            f"background: transparent; color: {ink}; font-weight: 700; font-size: 20px;"
        )

    def set_change_pct(self, change_pct: Any) -> None:
        self._fill = _tile_color(change_pct)
        self._has_data = _has_reading(change_pct)
        # A tile with no reading is greyed rather than contrast-maximised: it
        # should recede, not compete with the sectors that do have a number.
        self._apply_ink(_tile_ink(change_pct) if self._has_data else FG_MUTED)
        self.pct_lbl.setText(_fmt_pct(change_pct))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Paint the fill rather than restyling.

        This was ``setStyleSheet(f"QFrame {{ background: {c} }}")`` on every
        quote — one full stylesheet parse and polish per tile per tick, eleven
        tiles, for the life of the session. ``_HeaderStrip`` in ``panel.py``
        already established painting as the way to carry a computed colour in
        this codebase; this is the same job.

        A tile with no reading is left on the panel surface and given a
        *dashed* outline. Dashed rather than solid because the outline is doing
        real work here, not decoration: fill cannot separate "no data" from
        "flat" on the light theme (see :func:`_has_reading`), so the border is
        the channel that carries it, and a hairline solid rule is too quiet to
        be that. A dashed edge reads as "empty slot" at a glance, in both
        themes, without inventing a colour that looks like a value.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.setBrush(self._fill)
        if self._has_data:
            p.setPen(Qt.PenStyle.NoPen)
        else:
            pen = QPen(QColor(BORDER_STRONG))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(1)
            p.setPen(pen)
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._symbol)
        elif event.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(self._symbol, event.globalPosition().toPoint())
        super().mousePressEvent(event)


@register_panel(id="sectors", title="Sector Heatmap", category="Markets")
class SectorHeatmapPanel(Panel):
    def build(self) -> None:
        self._tiles_cfg: list = [list(row) for row in DEFAULT_TILES]
        self._tile_of_symbol: dict[str, _Tile] = {}

        self.grid_container = QWidget(self)
        self.grid = QGridLayout(self.grid_container)
        self.grid.setSpacing(6)
        self.content_layout.addWidget(self.grid_container, 1)

        edit_row = QHBoxLayout()
        edit_row.addStretch(1)
        edit_btn = QPushButton("Edit…", self)
        edit_btn.clicked.connect(self._open_edit_dialog)
        edit_row.addWidget(edit_btn)
        self.content_layout.addLayout(edit_row)

        self._rebuild_grid()

    # -- grid (re)construction ----------------------------------------------

    def _rebuild_grid(self) -> None:
        self.unsubscribe_all()
        self._tile_of_symbol.clear()

        # clear existing grid widgets
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for idx, (label, symbol) in enumerate(self._tiles_cfg):
            row, col = divmod(idx, GRID_COLS)
            tile = _Tile(label, symbol, self.grid_container)
            tile.clicked.connect(self.set_symbol)
            tile.menu_requested.connect(self._show_tile_menu)
            self.grid.addWidget(tile, row, col)
            self._tile_of_symbol[symbol] = tile

        for _label, sym in self._tiles_cfg:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    # -- data callbacks ----------------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        tile = self._tile_of_symbol.get(symbol)
        if tile is None or not isinstance(data, dict):
            return
        tile.set_change_pct(data.get("change_pct"))

    # -- edit dialog & quick actions -----------------------------------------

    def _apply_edit(self, tiles: list) -> None:
        """Apply a tile-set change behind one undo snapshot — shared by the
        Edit dialog and the tile right-click menu."""
        snap = [list(r) for r in self._tiles_cfg]

        def _undo() -> None:
            self._tiles_cfg = [list(r) for r in snap]
            self._rebuild_grid()
            self.set_status("undo · edit heatmap")

        UndoStack.instance().push("edit heatmap", _undo)
        self._tiles_cfg = tiles
        self._rebuild_grid()

    def _open_edit_dialog(self) -> None:
        result = open_list_editor(
            self,
            "Edit Sector Heatmap",
            [
                EditorSection(
                    "tiles",
                    "Tiles",
                    [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
                    self._tiles_cfg,
                    description="One colored tile per row — S&P sector ETFs by "
                    "default, but any Yahoo Finance symbol works.",
                    catalog=SECTOR_ETF_ENTRIES,
                    presets=[
                        ("All 11 sectors", [[e.label, e.code] for e in SECTOR_ETF_ENTRIES]),
                    ],
                )
            ],
        )
        if result is None or not result["tiles"]:
            return
        self._apply_edit(result["tiles"])

    def _show_tile_menu(self, symbol: str, global_pos) -> None:
        from PySide6.QtWidgets import QMenu

        label = next((l for l, s in self._tiles_cfg if s == symbol), symbol)
        menu = QMenu(self)
        remove_act = menu.addAction(f'Remove "{label}"')
        add_act = menu.addAction("Add tile…")
        edit_act = menu.addAction("Edit panel…")
        chosen = menu.exec(global_pos)
        if chosen is remove_act:
            self._apply_edit([list(r) for r in self._tiles_cfg if r[1] != symbol])
        elif chosen is add_act:
            entry = open_add_picker(self, SECTOR_ETF_ENTRIES, title="Add Tile")
            if entry is not None:
                self._apply_edit(
                    [list(r) for r in self._tiles_cfg] + [[entry.label, entry.code]]
                )
        elif chosen is edit_act:
            self._open_edit_dialog()

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"tiles": [list(r) for r in self._tiles_cfg]}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        tiles = settings.get("tiles")
        if isinstance(tiles, list) and tiles:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in tiles if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._tiles_cfg = cleaned
                self._rebuild_grid()
