"""Market Movers panel — top gainers / losers / most active.

Fed entirely by the movers:{kind} topic family (whole-list snapshots, not
per-symbol quotes), so each publish simply repopulates the table. The
provider may publish_error while under construction; the base Panel status
line (via the default on_error) surfaces that automatically.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import DOWN, UP

KINDS = [("Gainers", "gainers"), ("Losers", "losers"), ("Most Active", "actives")]
VALID_KINDS = {kind for _label, kind in KINDS}

COL_SYMBOL, COL_NAME, COL_LAST, COL_CHGPCT, COL_VOLUME = range(5)
HEADERS = ["Symbol", "Name", "Last", "Chg%", "Volume"]


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_volume(value: Any) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    for suffix, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{v / div:.1f}{suffix}"
    return f"{v:,.0f}"


@register_panel(id="movers", title="Market Movers", category="Markets")
class MoversPanel(Panel):
    def build(self) -> None:
        self._kind = "gainers"
        self._kind_buttons: dict[str, QPushButton] = {}

        kind_row = QHBoxLayout()
        self._kind_group = QButtonGroup(self)
        self._kind_group.setExclusive(True)
        for label, kind in KINDS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, k=kind: self._apply_kind(k))
            self._kind_group.addButton(btn)
            kind_row.addWidget(btn)
            self._kind_buttons[kind] = btn
        kind_row.addStretch(1)
        self.content_layout.addLayout(kind_row)

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_NAME, COL_LAST, COL_CHGPCT, COL_VOLUME):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.content_layout.addWidget(self.table, 1)

        self._apply_kind(self._kind, force=True)

    # -- kind selection --------------------------------------------------------

    def _apply_kind(self, kind: str, force: bool = False) -> None:
        if kind not in VALID_KINDS or (kind == self._kind and not force):
            return
        self._kind = kind
        for k, btn in self._kind_buttons.items():
            btn.setChecked(k == kind)
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self.set_status(f"loading {kind}…")
        self.subscribe(f"movers:{kind}", self._on_movers)

    # -- data callback -----------------------------------------------------------

    def _on_movers(self, data: Any) -> None:
        self.table.setRowCount(0)
        if not isinstance(data, list) or not data:
            self.set_status(f"{self._kind} · no data")
            return
        for entry in data:
            if not isinstance(entry, (list, tuple)) or len(entry) < 5:
                continue
            symbol, name, price, chg_pct, volume = entry[:5]
            row = self.table.rowCount()
            self.table.insertRow(row)

            sym_item = QTableWidgetItem(str(symbol) if symbol is not None else "-")
            sym_item.setFlags(sym_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, COL_SYMBOL, sym_item)

            name_item = QTableWidgetItem(str(name) if name is not None else "-")
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, COL_NAME, name_item)

            last_item = QTableWidgetItem(_fmt_num(price))
            chg_item = QTableWidgetItem(f"{_fmt_num(chg_pct)}%" if chg_pct is not None else "-")
            vol_item = QTableWidgetItem(_fmt_volume(volume))
            for item in (last_item, chg_item, vol_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if chg_pct is not None:
                color = QColor(UP) if chg_pct >= 0 else QColor(DOWN)
                chg_item.setForeground(color)
            self.table.setItem(row, COL_LAST, last_item)
            self.table.setItem(row, COL_CHGPCT, chg_item)
            self.table.setItem(row, COL_VOLUME, vol_item)
        self.set_status(f"{self._kind} · {self.table.rowCount()}")

    # -- selection -> navigation --------------------------------------------------

    def _on_row_selected(self) -> None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_SYMBOL)
        if item is None:
            return
        self.set_symbol(item.text())

    # -- persistence ---------------------------------------------------------------

    def settings(self) -> dict:
        return {"kind": self._kind}

    def restore(self, settings: dict) -> None:
        kind = settings.get("kind") if isinstance(settings, dict) else None
        if kind in VALID_KINDS:
            self._apply_kind(kind, force=True)
