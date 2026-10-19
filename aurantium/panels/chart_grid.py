"""Chart Grid panel — Bloomberg "chart grid" clone: an N-column grid of
small line charts, one per symbol, each with a title label showing last
price and change%, colored by sign. Click a cell to drive linked panels.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..guide import LIST_EDITOR, PanelGuide
from ..components.fmt import num as _fmt_num
from ..components import (
    FX_ENTRIES,
    INDEX_ENTRIES,
    EditorColumn,
    EditorSection,
    commodity_entries,
    open_add_picker,
    open_list_editor,
)
from ..panel import NULL_GLYPH, Panel, register_panel
from ..undo import UndoStack
from ..components.fmt import signed_pct
from ..components.plot_utils import CompactAxis
from ..theme import ACCENT, BG, BORDER_STRONG, DOWN, FG, FG_DIM, SPACE_MD, SPACE_XS, UP, LABEL
from ..components.responsive_row import ResponsiveRow

DEFAULT_SYMBOLS = [
    "^NDX", "^GSPC", "^DJI", "^BVSP", "^FCHI",
    "GC=F", "CL=F", "NG=F", "BTC-USD",
]

COLUMNS = 3
HISTORY_PERIOD = "6mo"
HISTORY_INTERVAL = "1d"
MA_MUTED = "#c9a24a"  # muted gold moving-average line on the mini charts


def _sma(values: list, window: int) -> Optional[list]:
    """Simple moving average aligned to ``values[window-1:]`` (pure Python —
    the mini charts don't otherwise need numpy)."""
    if window <= 0 or len(values) < window:
        return None
    total = sum(values[:window])
    out = [total / window]
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        out.append(total / window)
    return out


class _ChartCell(QWidget):
    """One grid cell: title label + mini line chart. Clicking anywhere on
    the cell publishes its symbol via ``on_click``."""

    def __init__(
        self, symbol: str, on_click, on_menu=None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.symbol = symbol
        self._on_click = on_click
        self._on_menu = on_menu
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(120)
        self.setObjectName("chartCell")
        self.setStyleSheet(
            f"QWidget#chartCell {{ background: {BG}; border: 1px solid {BORDER_STRONG}; }}"
            f"QWidget#chartCell:hover {{ border-color: {ACCENT}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        layout.setSpacing(SPACE_XS)

        self.title_lbl = QLabel(symbol, self)
        self.title_lbl.setObjectName("statValue")
        self.title_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.title_lbl)

        # compact labels (30K, 7,200): full figures did not fit the 34px axis
        # and pyqtgraph dropped them, so most cells had ticks with no values
        self.plot_widget = pg.PlotWidget(self, axisItems={"left": CompactAxis(orientation="left")})
        self.plot_widget.setBackground(BG)
        self.plot_widget.setMinimumHeight(90)
        self.plot_widget.showGrid(x=False, y=False)
        self.plot_widget.getPlotItem().hideAxis("bottom")
        left_axis = self.plot_widget.getAxis("left")
        left_axis.setTextPen(FG_DIM)
        left_axis.setWidth(34)
        self.plot_widget.getPlotItem().setMenuEnabled(False)
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.hideButtons()
        layout.addWidget(self.plot_widget, 1)

        self._curve: Optional[pg.PlotDataItem] = None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self.symbol)
        elif event.button() == Qt.MouseButton.RightButton and self._on_menu:
            self._on_menu(self.symbol, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def set_history(self, t: list, c: list) -> None:
        pairs = [(ti, ci) for ti, ci in zip(t, c) if ti is not None and ci is not None]
        if not pairs:
            return
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        color = UP if ys[-1] >= ys[0] else DOWN
        self.plot_widget.clear()
        pen = pg.mkPen(color, width=1.5)
        fill_color = QColor(color)
        fill_color.setAlpha(48)
        brush = pg.mkBrush(fill_color)  # translucent fill
        self._curve = self.plot_widget.plot(
            xs, ys, pen=pen, fillLevel=min(ys), brush=brush
        )
        # muted moving-average line for structure (50-bar, or 20 if data is short)
        window = 50 if len(ys) >= 50 else (20 if len(ys) >= 20 else 0)
        ma = _sma(ys, window) if window else None
        if ma is not None:
            self.plot_widget.plot(
                xs[window - 1:], ma, pen=pg.mkPen(MA_MUTED, width=1)
            )
        self.plot_widget.enableAutoRange()

    def set_quote(self, data: dict) -> None:
        price = data.get("price")
        change_pct = data.get("change_pct")
        last = _fmt_num(price)
        if change_pct is None:
            chg_txt = NULL_GLYPH
            color = FG_DIM
        else:
            chg_txt = signed_pct(change_pct)
            color = UP if change_pct >= 0 else DOWN
        # The app's colour roles: the ticker amber, the price neutral, only the
        # move in the up/down colour. Painting the whole line by the day's sign
        # made a red ticker and a red price over a green (period-up) chart.
        self.title_lbl.setText(
            f"<span style='color:{LABEL}'>{self.symbol}</span>&nbsp;&nbsp;"
            f"<span style='color:{FG}'>{last}</span>&nbsp;&nbsp;"
            f"<span style='color:{color}'>{chg_txt}</span>"
        )


@register_panel(id="chart_grid", title="Chart Grid", category="Markets")
class ChartGridPanel(Panel):
    GUIDE = PanelGuide(
        purpose="A grid of small line charts, one per symbol, to watch many markets at a glance.",
        links="Click a cell to send its symbol to every panel in this link group.",
        interactions=(
            "Each title shows the symbol, last price and today's change. The line is "
            "green or red by its move across the chart, with a muted moving average.",
            "Right-click a cell to remove it, **Add chart…**, or **Edit panel…**.",
        ),
        sections=(LIST_EDITOR,),
    )

    def build(self) -> None:
        self._symbols: list[str] = list(DEFAULT_SYMBOLS)
        self._cells: dict[str, _ChartCell] = {}

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        # vertical only: the grid drops columns to fit instead
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cols = COLUMNS
        self._grid_host = QWidget(self._scroll)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(SPACE_MD)
        self._scroll.setWidget(self._grid_host)
        self._scroll.viewport().installEventFilter(self)
        self.content_layout.addWidget(self._scroll, 1)

        config_row = ResponsiveRow()
        config_row.addStretch(1)
        edit_btn = QPushButton("Edit…", self)
        edit_btn.clicked.connect(self._open_edit_dialog)
        config_row.addWidget(edit_btn)
        self.content_layout.addLayout(config_row)

        self._rebuild_grid()

    #: narrowest a chart cell may get before the grid drops a column
    CELL_MIN = 170

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        # Reflow on the scroll viewport's own resize. The panel's resizeEvent
        # fires before its layouts have resized the viewport, so reading the
        # width there saw the old size: a grid first shown at full width
        # stayed one column wide (the Sector Heatmap had the same bug).
        if (
            hasattr(self, "_scroll")
            and obj is self._scroll.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._reflow()
        return super().eventFilter(obj, event)

    def _reflow(self) -> None:
        width = self._scroll.viewport().width()
        cols = max(1, min(COLUMNS, width // self.CELL_MIN))
        if cols != self._cols:
            self._cols = cols
            for i, cell in enumerate(self._cells.values()):
                self._grid.removeWidget(cell)
                r, c = divmod(i, cols)
                self._grid.addWidget(cell, r, c)

    # -- grid (re)construction -------------------------------------------------

    def _rebuild_grid(self) -> None:
        """Tear down all cells/subscriptions and rebuild from
        ``self._symbols`` — mirrors watchlist.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cells.clear()

        for i, sym in enumerate(self._symbols):
            cell = _ChartCell(
                sym, self._on_cell_click, self._show_cell_menu, self._grid_host
            )
            cell.setMinimumWidth(0)
            row, col = divmod(i, self._cols)
            self._grid.addWidget(cell, row, col)
            self._cells[sym] = cell

        for sym in self._symbols:
            self.subscribe(
                f"history:{sym}:{HISTORY_PERIOD}:{HISTORY_INTERVAL}",
                lambda data, s=sym: self._on_history(s, data),
            )
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    # -- data callbacks ----------------------------------------------------------

    def _on_history(self, symbol: str, data: Any) -> None:
        cell = self._cells.get(symbol)
        if cell is None or not isinstance(data, dict):
            return
        t = data.get("t") or []
        c = data.get("c") or []
        cell.set_history(t, c)

    def _on_quote(self, symbol: str, data: Any) -> None:
        cell = self._cells.get(symbol)
        if cell is None or not isinstance(data, dict):
            return
        cell.set_quote(data)

    # -- interaction -----------------------------------------------------------

    def _on_cell_click(self, symbol: str) -> None:
        self.set_symbol(symbol)

    def _apply_edit(self, symbols: list[str]) -> None:
        """Apply a symbol-set change behind one undo snapshot — shared by
        the Edit dialog and the cell right-click menu."""
        snap = list(self._symbols)

        def _undo() -> None:
            self._symbols = list(snap)
            self._rebuild_grid()
            self.set_status("undo · edit chart grid")

        UndoStack.instance().push("edit chart grid", _undo)
        self._symbols = symbols
        self._rebuild_grid()

    def _open_edit_dialog(self) -> None:
        result = open_list_editor(
            self,
            "Edit Chart Grid",
            [
                EditorSection(
                    "symbols",
                    "Symbols",
                    [EditorColumn("Symbol", kind="symbol")],
                    [[s] for s in self._symbols],
                    description="One mini chart per row — any Yahoo Finance "
                    "symbol (^GSPC, GC=F, BTC-USD, AAPL…).",
                    catalog=INDEX_ENTRIES + commodity_entries() + FX_ENTRIES,
                )
            ],
        )
        if result is None or not result["symbols"]:
            return
        self._apply_edit([row[0] for row in result["symbols"]])

    def _show_cell_menu(self, symbol: str, global_pos) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        remove_act = menu.addAction(f'Remove "{symbol}"')
        add_act = menu.addAction("Add chart…")
        edit_act = menu.addAction("Edit panel…")
        self.add_guide_action(menu)
        chosen = menu.exec(global_pos)
        if chosen is remove_act:
            self._apply_edit([s for s in self._symbols if s != symbol])
        elif chosen is add_act:
            entry = open_add_picker(
                self,
                INDEX_ENTRIES + commodity_entries() + FX_ENTRIES,
                title="Add Chart",
            )
            if entry is not None and entry.code not in self._symbols:
                self._apply_edit(list(self._symbols) + [entry.code])
        elif chosen is edit_act:
            self._open_edit_dialog()

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"symbols": list(self._symbols)}

    def restore(self, settings: dict) -> None:
        symbols = settings.get("symbols") if isinstance(settings, dict) else None
        if isinstance(symbols, list) and symbols:
            cleaned = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if cleaned:
                self._symbols = cleaned
                self._rebuild_grid()
