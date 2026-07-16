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
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..panel import Panel, register_panel
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
        self.edit_line = QLineEdit(self)
        self.edit_line.setPlaceholderText("Label=SYM, Label=SYM, …")
        apply_btn = QPushButton("Apply", self)
        apply_btn.clicked.connect(self._apply_edit_line)
        edit_row.addWidget(self.edit_line, 1)
        edit_row.addWidget(apply_btn)
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
            self.grid.addWidget(tile, row, col)
            self._tile_of_symbol[symbol] = tile

        self.edit_line.setText(
            ", ".join(f"{label}={sym}" for label, sym in self._tiles_cfg)
        )

        for _label, sym in self._tiles_cfg:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    # -- data callbacks ----------------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        tile = self._tile_of_symbol.get(symbol)
        if tile is None or not isinstance(data, dict):
            return
        tile.set_change_pct(data.get("change_pct"))

    # -- edit line -------------------------------------------------------------

    def _apply_edit_line(self) -> None:
        text = self.edit_line.text().strip()
        if not text:
            return
        parsed = []
        for entry in text.split(","):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            label, sym = entry.split("=", 1)
            label = label.strip()
            sym = sym.strip().upper()
            if label and sym:
                parsed.append([label, sym])
        if parsed:
            self._tiles_cfg = parsed
            self._rebuild_grid()

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
