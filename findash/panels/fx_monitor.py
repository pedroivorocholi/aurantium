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
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG_HEADER, DOWN, FG_DIM, UP

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


class _EditDialog(QDialog):
    """Two-box editor: one QPlainTextEdit per section, lines of
    "Label,SYMBOL". OK rebuilds the table from the parsed text."""

    def __init__(self, majors: list, other: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit FX Monitor")
        self.resize(420, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Majors (one 'Label,SYMBOL' per line):", self))
        self.majors_edit = QPlainTextEdit(self)
        self.majors_edit.setPlainText(
            "\n".join(f"{label},{sym}" for label, sym in majors)
        )
        layout.addWidget(self.majors_edit, 1)

        layout.addWidget(QLabel("Other (one 'Label,SYMBOL' per line):", self))
        self.other_edit = QPlainTextEdit(self)
        self.other_edit.setPlainText(
            "\n".join(f"{label},{sym}" for label, sym in other)
        )
        layout.addWidget(self.other_edit, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _parse(text: str) -> list:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line or "," not in line:
                continue
            label, sym = line.split(",", 1)
            label = label.strip()
            sym = sym.strip().upper()
            if label and sym:
                rows.append([label, sym])
        return rows

    def result_majors(self) -> list:
        return self._parse(self.majors_edit.toPlainText())

    def result_other(self) -> list:
        return self._parse(self.other_edit.toPlainText())


@register_panel(id="fx", title="FX Monitor", category="Markets")
class FXMonitorPanel(Panel):
    def build(self) -> None:
        self._majors: list = [list(row) for row in DEFAULT_MAJORS]
        self._other: list = [list(row) for row in DEFAULT_OTHER]
        # row -> ("header", None) | ("data", symbol)
        self._row_kind: dict[int, tuple[str, str | None]] = {}
        self._row_of_symbol: dict[str, int] = {}

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
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
            color = QColor(UP) if change >= 0 else QColor(DOWN)
            chg_item.setForeground(color)
            pct_item.setForeground(color)
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

    def _open_edit_dialog(self) -> None:
        dlg = _EditDialog(self._majors, self._other, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            majors = dlg.result_majors()
            other = dlg.result_other()
            if majors or other:
                self._majors = majors or self._majors
                self._other = other or self._other
                self._rebuild_table()

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"majors": [list(r) for r in self._majors], "other": [list(r) for r in self._other]}

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
