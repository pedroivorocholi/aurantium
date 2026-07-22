"""Sector Heatmap panel — a grid of colored tiles, one per S&P sector ETF.
Tile background is interpolated between DOWN (red, <= -2%) through neutral
BG_ALT (0%) to UP (green, >= +2%). Click a tile to navigate linked panels.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent
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
from ..panel import Panel, register_panel
from ..undo import UndoStack
from ..theme import BG_ALT, DOWN, FG_DIM, UP

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


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def _tile_color(change_pct: Any) -> QColor:
    neutral = QColor(BG_ALT)
    if change_pct is None:
        return neutral
    try:
        pct = float(change_pct)
    except (TypeError, ValueError):
        return neutral
    if pct >= 0:
        return _lerp_color(neutral, QColor(UP), pct / PCT_CLAMP)
    return _lerp_color(neutral, QColor(DOWN), -pct / PCT_CLAMP)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "-"


class _Tile(QFrame):
    """A single clickable sector tile."""

    clicked = Signal(str)
    menu_requested = Signal(str, object)  # symbol, global pos

    def __init__(self, label: str, symbol: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._symbol = symbol
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(70, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        self.name_lbl = QLabel(label, self)
        self.name_lbl.setStyleSheet("color: #f2f2f2; font-weight: bold; font-size: 11px;")
        self.name_lbl.setWordWrap(True)

        self.symbol_lbl = QLabel(symbol, self)
        self.symbol_lbl.setStyleSheet("color: #e0e0e0; font-size: 10px;")

        self.pct_lbl = QLabel("-", self)
        self.pct_lbl.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 20px;")

        layout.addWidget(self.name_lbl)
        layout.addWidget(self.symbol_lbl)
        layout.addStretch(1)
        layout.addWidget(self.pct_lbl)

        self.set_change_pct(None)

    def set_change_pct(self, change_pct: Any) -> None:
        color = _tile_color(change_pct)
        # Applied directly on this QFrame instance (no children are
        # QFrames), so a bare "QFrame" selector scopes to just this tile.
        self.setStyleSheet(f"QFrame {{ background: {color.name()}; border-radius: 4px; }}")
        self.pct_lbl.setText(_fmt_pct(change_pct))

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
