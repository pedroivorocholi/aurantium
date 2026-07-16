"""World Indices panel — Bloomberg WEI clone: a grouped monitor table of
major world equity indices by region, with bold group-header rows. Row
click drives linked panels; group headers are not selectable. Structured
like commodities.py.
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

DEFAULT_AMERICAS = [
    ["S&P 500", "^GSPC"],
    ["Nasdaq 100", "^NDX"],
    ["Dow", "^DJI"],
    ["Russell 2000", "^RUT"],
    ["TSX", "^GSPTSE"],
    ["Ibovespa", "^BVSP"],
    ["IPC Mexico", "^MXX"],
]
DEFAULT_EUROPE = [
    ["FTSE 100", "^FTSE"],
    ["DAX", "^GDAXI"],
    ["CAC 40", "^FCHI"],
    ["Euro Stoxx 50", "^STOXX50E"],
]
DEFAULT_ASIA = [
    ["Nikkei", "^N225"],
    ["Hang Seng", "^HSI"],
    ["Shanghai", "000001.SS"],
    ["ASX 200", "^AXJO"],
]

COL_NAME, COL_LAST, COL_CHG, COL_CHGPCT = range(4)
HEADERS = ["Index", "Last", "Chg", "Chg%"]

ROW_KIND_HEADER = "header"
ROW_KIND_DATA = "data"


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


class _EditDialog(QDialog):
    """Three-box editor: one QPlainTextEdit per region, lines of
    "Label,SYMBOL". OK rebuilds the table from the parsed text."""

    def __init__(self, americas: list, europe: list, asia: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit World Indices")
        self.resize(420, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Americas (one 'Label,SYMBOL' per line):", self))
        self.americas_edit = QPlainTextEdit(self)
        self.americas_edit.setPlainText(
            "\n".join(f"{label},{sym}" for label, sym in americas)
        )
        layout.addWidget(self.americas_edit, 1)

        layout.addWidget(QLabel("Europe (one 'Label,SYMBOL' per line):", self))
        self.europe_edit = QPlainTextEdit(self)
        self.europe_edit.setPlainText(
            "\n".join(f"{label},{sym}" for label, sym in europe)
        )
        layout.addWidget(self.europe_edit, 1)

        layout.addWidget(QLabel("Asia/Pacific (one 'Label,SYMBOL' per line):", self))
        self.asia_edit = QPlainTextEdit(self)
        self.asia_edit.setPlainText(
            "\n".join(f"{label},{sym}" for label, sym in asia)
        )
        layout.addWidget(self.asia_edit, 1)

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

    def result_americas(self) -> list:
        return self._parse(self.americas_edit.toPlainText())

    def result_europe(self) -> list:
        return self._parse(self.europe_edit.toPlainText())

    def result_asia(self) -> list:
        return self._parse(self.asia_edit.toPlainText())


@register_panel(id="world_indices", title="World Indices", category="Markets")
class WorldIndicesPanel(Panel):
    def build(self) -> None:
        self._americas: list = [list(row) for row in DEFAULT_AMERICAS]
        self._europe: list = [list(row) for row in DEFAULT_EUROPE]
        self._asia: list = [list(row) for row in DEFAULT_ASIA]
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

        self._append_group_header("Americas")
        for label, sym in self._americas:
            self._append_data_row(label, sym)

        self._append_group_header("Europe")
        for label, sym in self._europe:
            self._append_data_row(label, sym)

        self._append_group_header("Asia/Pacific")
        for label, sym in self._asia:
            self._append_data_row(label, sym)

        for _label, sym in self._americas + self._europe + self._asia:
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

        last_item.setText(_fmt_num(price))
        chg_item.setText(_fmt_num(change))
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
        dlg = _EditDialog(self._americas, self._europe, self._asia, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            americas = dlg.result_americas()
            europe = dlg.result_europe()
            asia = dlg.result_asia()
            if americas or europe or asia:
                self._americas = americas or self._americas
                self._europe = europe or self._europe
                self._asia = asia or self._asia
                self._rebuild_table()

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "americas": [list(r) for r in self._americas],
            "europe": [list(r) for r in self._europe],
            "asia": [list(r) for r in self._asia],
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        americas = settings.get("americas")
        europe = settings.get("europe")
        asia = settings.get("asia")
        changed = False
        if isinstance(americas, list) and americas:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in americas if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._americas = cleaned
                changed = True
        if isinstance(europe, list) and europe:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in europe if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._europe = cleaned
                changed = True
        if isinstance(asia, list) and asia:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in asia if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._asia = cleaned
                changed = True
        if changed:
            self._rebuild_table()
