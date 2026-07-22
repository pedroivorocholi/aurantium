"""FX Monitor panel — Bloomberg FXC-lite clone: a grouped monitor table for
major and other currency pairs (plus a couple of majors-adjacent crypto
pairs), with bold group-header rows. Row click drives linked panels; group
headers are not selectable. Structured like commodities.py.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidgetItem,
)

from ..components import (
    FX_ENTRIES,
    EditorColumn,
    EditorSection,
    MarketTable,
    make_filter_edit,
    open_add_picker,
    open_list_editor,
)
from ..undo import UndoStack
from ..panel import Panel, register_panel
from ..theme import ACCENT, BG_HEADER, FG_DIM, apply_tick

DEFAULT_MAJORS = [
    ["EUR/USD", "EURUSD=X"],
    ["GBP/USD", "GBPUSD=X"],
    ["USD/JPY", "USDJPY=X"],
    ["USD/CHF", "USDCHF=X"],
    ["AUD/USD", "AUDUSD=X"],
    ["USD/CAD", "USDCAD=X"],
    ["NZD/USD", "NZDUSD=X"],
]
DEFAULT_OTHER = [
    ["Dollar Index", "DX-Y.NYB"],
    ["USD/BRL", "USDBRL=X"],
    ["USD/MXN", "USDMXN=X"],
    ["USD/CNY", "USDCNY=X"],
    ["Bitcoin", "BTC-USD"],
    ["Ethereum", "ETH-USD"],
]

COL_NAME, COL_LAST, COL_CHG, COL_CHGPCT = range(4)
HEADERS = ["Pair", "Last", "Chg", "Chg%"]

ROW_KIND_HEADER = "header"
ROW_KIND_DATA = "data"


def _fmt_price(value: Any) -> str:
    """FX rates need more precision than equity prices — use 4-5 decimals
    when the price is small (typical of pairs quoted as USD fractions),
    otherwise 2 decimals (indices, BTC, etc.)."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    decimals = 4 if abs(v) < 10 else 2
    return f"{v:,.{decimals}f}"


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="fx", title="FX Monitor", category="Markets")
class FXMonitorPanel(Panel):
    def build(self) -> None:
        self._majors: list = [list(row) for row in DEFAULT_MAJORS]
        self._other: list = [list(row) for row in DEFAULT_OTHER]
        # row -> ("header", None) | ("data", symbol)
        self._row_kind: dict[int, tuple[str, str | None]] = {}
        self._row_of_symbol: dict[str, int] = {}

        self.table = MarketTable(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.enable_column_menu()
        self.table.set_row_actions(self._row_actions)

        self._filter = make_filter_edit(self.table, "Filter pairs…")
        self.content_layout.addWidget(self._filter)
        self.content_layout.addWidget(self.table, 1)

        edit_row = QHBoxLayout()
        edit_row.addStretch(1)
        edit_btn = QPushButton("Edit…", self)
        edit_btn.clicked.connect(self._open_edit_dialog)
        edit_row.addWidget(edit_btn)
        self.content_layout.addLayout(edit_row)

        self._rebuild_table()

    # -- table (re)construction ----------------------------------------------

    def _rebuild_table(self) -> None:
        """Rebuild all rows (group headers + data rows) and resubscribe all
        quote topics — mirrors commodities.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self._row_kind.clear()
        self._row_of_symbol.clear()

        self._append_group_header("Majors")
        for label, sym in self._majors:
            self._append_data_row(label, sym)

        self._append_group_header("Other")
        for label, sym in self._other:
            self._append_data_row(label, sym)

        if hasattr(self, "_filter"):
            self.table.apply_filter(self._filter.text())

        for _label, sym in self._majors + self._other:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    def _append_group_header(self, text: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        item.setForeground(QColor(ACCENT))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setBackground(QColor(BG_HEADER))
        self.table.setItem(row, 0, item)
        self.table.setSpan(row, 0, 1, len(HEADERS))
        self._row_kind[row] = (ROW_KIND_HEADER, None)

    def _append_data_row(self, label: str, symbol: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem(f"  {label}")
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, COL_NAME, name_item)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT):
            item = QTableWidgetItem("-")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)
        self._row_kind[row] = (ROW_KIND_DATA, symbol)
        self._row_of_symbol[symbol] = row

    # -- data callbacks ----------------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        row = self._row_of_symbol.get(symbol)
        if row is None or not isinstance(data, dict):
            return
        price = data.get("price")
        change = data.get("change")
        change_pct = data.get("change_pct")

        last_item = self.table.item(row, COL_LAST)
        chg_item = self.table.item(row, COL_CHG)
        pct_item = self.table.item(row, COL_CHGPCT)
        if not (last_item and chg_item and pct_item):
            return

        last_item.setText(_fmt_price(price))
        chg_item.setText(_fmt_price(change))
        pct_item.setText(f"{_fmt_num(change_pct)}%" if change_pct is not None else "-")

        if change is not None:
            apply_tick(chg_item, change, glyph=False)
            apply_tick(pct_item, change)
        else:
            dim = QColor(FG_DIM)
            chg_item.setForeground(dim)
            pct_item.setForeground(dim)

    # -- selection -> navigation (skip group headers) ---------------------------

    def _on_row_selected(self) -> None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        row = rows[0].row()
        kind, symbol = self._row_kind.get(row, (None, None))
        if kind != ROW_KIND_DATA or not symbol:
            return
        self.set_symbol(symbol)

    # -- edit dialog ---------------------------------------------------------

    def _apply_edit(self, majors=None, other=None) -> None:
        """Apply a config change (None = keep) behind one undo snapshot —
        shared by the Edit dialog and the right-click quick actions."""
        snap_m = [list(r) for r in self._majors]
        snap_o = [list(r) for r in self._other]

        def _undo() -> None:
            self._majors = [list(r) for r in snap_m]
            self._other = [list(r) for r in snap_o]
            self._rebuild_table()
            self.set_status("undo · edit FX")

        UndoStack.instance().push("edit FX", _undo)
        if majors is not None:
            self._majors = majors
        if other is not None:
            self._other = other
        self._rebuild_table()

    def _open_edit_dialog(self) -> None:
        columns = [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")]
        result = open_list_editor(
            self,
            "Edit FX Monitor",
            [
                EditorSection(
                    "majors",
                    "Majors",
                    columns,
                    self._majors,
                    description="Live rates in the Majors group — Yahoo pairs "
                    "like EURUSD=X, indices like DX-Y.NYB, crypto like BTC-USD.",
                    catalog=FX_ENTRIES,
                    presets=[
                        ("G3 pairs", [["EUR/USD", "EURUSD=X"], ["USD/JPY", "USDJPY=X"], ["GBP/USD", "GBPUSD=X"]]),
                    ],
                ),
                EditorSection(
                    "other",
                    "Other",
                    columns,
                    self._other,
                    description="Live rates in the Other group — EM pairs, the "
                    "dollar index, crypto.",
                    catalog=FX_ENTRIES,
                    presets=[
                        ("LatAm", [["USD/BRL", "USDBRL=X"], ["USD/MXN", "USDMXN=X"]]),
                    ],
                ),
            ],
        )
        if result is None:
            return
        majors, other = result["majors"], result["other"]
        if majors or other:
            self._apply_edit(majors or None, other or None)

    def _row_actions(self, row: int) -> list:
        actions = []
        kind, symbol = self._row_kind.get(row, (None, None))
        if kind == ROW_KIND_DATA and symbol:
            in_majors = any(s == symbol for _l, s in self._majors)
            group = self._majors if in_majors else self._other
            label = next((l for l, s in group if s == symbol), symbol)

            def _remove() -> None:
                rows = [list(r) for r in group if r[1] != symbol]
                if in_majors:
                    self._apply_edit(majors=rows)
                else:
                    self._apply_edit(other=rows)

            actions.append((f'Remove "{label}"', _remove))

        def _add(to_majors: bool) -> None:
            entry = open_add_picker(
                self,
                FX_ENTRIES,
                title="Add to Majors" if to_majors else "Add to Other",
            )
            if entry is None:
                return
            new_row = [entry.label, entry.code]
            if to_majors:
                self._apply_edit(majors=[list(r) for r in self._majors] + [new_row])
            else:
                self._apply_edit(other=[list(r) for r in self._other] + [new_row])

        actions.append(("Add to Majors…", lambda: _add(True)))
        actions.append(("Add to Other…", lambda: _add(False)))
        actions.append(("Edit panel…", self._open_edit_dialog))
        return actions

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "majors": [list(r) for r in self._majors],
            "other": [list(r) for r in self._other],
            "hidden_cols": self.table.hidden_columns(),
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        majors = settings.get("majors")
        other = settings.get("other")
        changed = False
        if isinstance(majors, list) and majors:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in majors if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._majors = cleaned
                changed = True
        if isinstance(other, list) and other:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in other if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._other = cleaned
                changed = True
        if changed:
            self._rebuild_table()
        self.table.set_hidden_columns(settings.get("hidden_cols", []))
