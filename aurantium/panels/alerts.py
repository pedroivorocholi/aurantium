"""Price Alerts panel — create/list threshold alerts on quotes.

Rules live in the shared :class:`AlertEngine` (so they fire even when this panel
isn't open) and persist in QSettings. The tray shows a balloon when one fires.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ..alerts import AlertEngine
from ..components import attach_suggestions
from ..panel import Panel, register_panel

HEADERS = ["Symbol", "Field", "Cond", "Threshold", "On"]
_FIELD_ITEMS = [("Price", "price"), ("Change %", "change_pct")]
_OP_ITEMS = [("above", "gt"), ("below", "lt")]
_FIELD_TEXT = {"price": "Price", "change_pct": "Change %"}
_OP_TEXT = {"gt": "above", "lt": "below"}


@register_panel(id="alerts", title="Price Alerts", category="Analytics")
class AlertsPanel(Panel):
    def build(self) -> None:
        self._engine = AlertEngine.instance()

        # -- add-rule row ----------------------------------------------------
        add_row = QHBoxLayout()
        self.symbol_edit = QLineEdit(self)
        self.symbol_edit.setPlaceholderText("Symbol…")
        self.symbol_edit.setMaximumWidth(90)
        attach_suggestions(self.symbol_edit)
        self.field_combo = QComboBox(self)
        for label, _key in _FIELD_ITEMS:
            self.field_combo.addItem(label)
        self.op_combo = QComboBox(self)
        for label, _key in _OP_ITEMS:
            self.op_combo.addItem(label)
        self.threshold_spin = QDoubleSpinBox(self)
        self.threshold_spin.setRange(-1e9, 1e9)
        self.threshold_spin.setDecimals(2)
        add_btn = QPushButton("Add", self)
        add_btn.clicked.connect(self._add_rule)
        self.symbol_edit.returnPressed.connect(self._add_rule)
        add_row.addWidget(self.symbol_edit)
        add_row.addWidget(self.field_combo)
        add_row.addWidget(self.op_combo)
        add_row.addWidget(self.threshold_spin, 1)
        add_row.addWidget(add_btn)
        self.content_layout.addLayout(add_row)

        # -- rules table -----------------------------------------------------
        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.content_layout.addWidget(self.table, 1)

        remove_row = QHBoxLayout()
        remove_row.addStretch(1)
        remove_btn = QPushButton("Remove selected", self)
        remove_btn.clicked.connect(self._remove_selected)
        remove_row.addWidget(remove_btn)
        self.content_layout.addLayout(remove_row)

        self._engine.rules_changed.connect(self._refresh)
        self._refresh()

    # -- rendering ---------------------------------------------------------

    def _refresh(self) -> None:
        self.table.blockSignals(True)
        try:
            rules = self._engine.rules()
            self.table.setRowCount(0)
            for rule in rules:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self._set_cell(r, 0, rule["symbol"])
                self._set_cell(r, 1, _FIELD_TEXT.get(rule["field"], rule["field"]))
                self._set_cell(r, 2, _OP_TEXT.get(rule["op"], rule["op"]))
                self._set_cell(r, 3, f"{rule['threshold']:,.2f}")
                on_item = QTableWidgetItem()
                on_item.setFlags(
                    (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                )
                on_item.setCheckState(
                    Qt.CheckState.Checked if rule.get("enabled", True)
                    else Qt.CheckState.Unchecked
                )
                self.table.setItem(r, 4, on_item)
            n = len(rules)
            self.set_status(f"{n} alert rule{'s' if n != 1 else ''}")
        finally:
            self.table.blockSignals(False)

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, col, item)

    # -- interactions ------------------------------------------------------

    def _add_rule(self) -> None:
        symbol = self.symbol_edit.text().strip().upper()
        if not symbol:
            return
        rule = {
            "symbol": symbol,
            "field": _FIELD_ITEMS[self.field_combo.currentIndex()][1],
            "op": _OP_ITEMS[self.op_combo.currentIndex()][1],
            "threshold": self.threshold_spin.value(),
            "enabled": True,
        }
        if self._engine.add_rule(rule):
            self.symbol_edit.clear()

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self._engine.remove_rule(row)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 4:
            return
        self._engine.set_enabled(item.row(), item.checkState() == Qt.CheckState.Checked)
