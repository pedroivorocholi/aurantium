"""Commodities panel — Bloomberg GLCO clone: a grouped monitor table for
energy and metals futures, with bold group-header rows. Row click drives
linked panels; group headers are not selectable.
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
    EditorColumn,
    EditorSection,
    MarketTable,
    commodity_entries,
    make_filter_edit,
    open_add_picker,
    open_list_editor,
)
from ..panel import Panel, register_panel
from ..undo import UndoStack
from ..theme import ACCENT, BG_HEADER, FG_DIM, apply_tick

#: display groups in table order — keys are the settings keys, titles match
#: the catalog categories so a picked commodity files into the right group
GROUP_KEYS = [
    ("energy", "Energy"),
    ("metals", "Metals"),
    ("agriculture", "Agriculture"),
    ("livestock", "Livestock"),
]

DEFAULT_GROUPS = {
    "energy": [
        ["WTI", "CL=F"],
        ["Brent", "BZ=F"],
        ["Gasoline", "RB=F"],
        ["Heating Oil", "HO=F"],
        ["NatGas", "NG=F"],
    ],
    "metals": [
        ["Gold", "GC=F"],
        ["Silver", "SI=F"],
        ["Copper", "HG=F"],
        ["Platinum", "PL=F"],
        ["Aluminum", "ALI=F"],
    ],
    # empty by default — the groups appear as soon as rows are added
    "agriculture": [],
    "livestock": [],
}

COL_NAME, COL_LAST, COL_CHG, COL_CHGPCT, COL_RANGE = range(5)
HEADERS = ["Commodity", "Last", "Chg", "Chg%", "Range (1D)"]

ROW_KIND_HEADER = "header"
ROW_KIND_DATA = "data"


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_range(low: Any, high: Any) -> str:
    if low is None and high is None:
        return "-"
    return f"{_fmt_num(low)} – {_fmt_num(high)}"


@register_panel(id="commodities", title="Commodities", category="Markets")
class CommoditiesPanel(Panel):
    def build(self) -> None:
        self._groups: dict[str, list] = {
            key: [list(row) for row in DEFAULT_GROUPS[key]] for key, _t in GROUP_KEYS
        }
        # row -> ("header", None) | ("data", symbol)
        self._row_kind: dict[int, tuple[str, str | None]] = {}
        self._row_of_symbol: dict[str, int] = {}

        self.table = MarketTable(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.set_empty_text(
            "No commodity data",
            "Right-click to edit the list, or press F5 to refresh",
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_RANGE):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        # Narrow panels drop columns instead of eliding the price:
        # a row crushed to "4,432.…" has lost the number it exists for.
        self.table.set_column_priority(
            keep=[COL_NAME, COL_LAST],
            droppable=[COL_RANGE, COL_CHG, COL_CHGPCT],
        )
        self.table.enable_column_menu()
        self.table.set_row_actions(self._row_actions)

        self._filter = make_filter_edit(self.table, "Filter commodities…")
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
        quote topics — mirrors watchlist.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self._row_kind.clear()
        self._row_of_symbol.clear()

        for key, title in GROUP_KEYS:
            rows = self._groups[key]
            if not rows:
                continue  # empty groups don't render a lonely header
            self._append_group_header(title)
            for label, sym in rows:
                self._append_data_row(label, sym)

        if hasattr(self, "_filter"):
            self.table.apply_filter(self._filter.text())

        for key, _title in GROUP_KEYS:
            for _label, sym in self._groups[key]:
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
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_RANGE):
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
        day_low = data.get("day_low")
        day_high = data.get("day_high")

        last_item = self.table.item(row, COL_LAST)
        chg_item = self.table.item(row, COL_CHG)
        pct_item = self.table.item(row, COL_CHGPCT)
        range_item = self.table.item(row, COL_RANGE)
        if not (last_item and chg_item and pct_item and range_item):
            return

        last_item.setText(_fmt_num(price))
        chg_item.setText(_fmt_num(change))
        pct_item.setText(f"{_fmt_num(change_pct)}%" if change_pct is not None else "-")
        range_item.setText(_fmt_range(day_low, day_high))

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

    def _apply_edit(self, changes: dict[str, list]) -> None:
        """Apply per-group config changes (missing key = keep) behind one
        undo snapshot — shared by the Edit dialog and the right-click quick
        actions."""
        snap = {key: [list(r) for r in rows] for key, rows in self._groups.items()}

        def _undo() -> None:
            self._groups = {key: [list(r) for r in rows] for key, rows in snap.items()}
            self._rebuild_table()
            self.set_status("undo · edit commodities")

        UndoStack.instance().push("edit commodities", _undo)
        for key, rows in changes.items():
            if key in self._groups:
                self._groups[key] = rows
        self._rebuild_table()

    _GROUP_BLURB = {
        "energy": "Live futures quotes in the Energy group (CL=F, BZ=F…).",
        "metals": "Live futures quotes in the Metals group (GC=F, SI=F…).",
        "agriculture": "Live futures quotes in the Agriculture group — grains "
        "and softs (ZC=F, KC=F…).",
        "livestock": "Live futures quotes in the Livestock group (LE=F, HE=F…). "
        "Any Yahoo Finance symbol works in every group.",
    }
    _GROUP_PRESETS = {
        "energy": [("Oil complex", [["WTI", "CL=F"], ["Brent", "BZ=F"]])],
        "metals": [("Precious", [["Gold", "GC=F"], ["Silver", "SI=F"]])],
        "agriculture": [
            ("Grains", [["Corn", "ZC=F"], ["Wheat (SRW)", "ZW=F"], ["Soybeans", "ZS=F"]]),
            ("Softs", [["Coffee", "KC=F"], ["Sugar", "SB=F"], ["Cocoa", "CC=F"]]),
        ],
        "livestock": [
            ("All livestock", [["Live Cattle", "LE=F"], ["Feeder Cattle", "GF=F"], ["Lean Hogs", "HE=F"]]),
        ],
    }

    def _open_edit_dialog(self) -> None:
        columns = [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")]
        result = open_list_editor(
            self,
            "Edit Commodities",
            [
                EditorSection(
                    key,
                    title,
                    columns,
                    self._groups[key],
                    description=self._GROUP_BLURB[key],
                    # tab-specific slice first, everything else still findable
                    catalog=sorted(
                        commodity_entries(), key=lambda e: e.category != title
                    ),
                    presets=self._GROUP_PRESETS[key],
                )
                for key, title in GROUP_KEYS
            ],
        )
        if result is None:
            return
        if any(result.get(key) for key, _t in GROUP_KEYS):
            self._apply_edit({key: result[key] for key, _t in GROUP_KEYS})

    def _group_of_symbol(self, symbol: str) -> str | None:
        for key, _title in GROUP_KEYS:
            if any(s == symbol for _l, s in self._groups[key]):
                return key
        return None

    def _row_actions(self, row: int) -> list:
        actions = []
        kind, symbol = self._row_kind.get(row, (None, None))
        hovered_key = self._group_of_symbol(symbol) if symbol else None
        if kind == ROW_KIND_DATA and symbol and hovered_key:
            rows = self._groups[hovered_key]
            label = next((l for l, s in rows if s == symbol), symbol)

            def _remove() -> None:
                remaining = [list(r) for r in self._groups[hovered_key] if r[1] != symbol]
                self._apply_edit({hovered_key: remaining})

            actions.append((f'Remove "{label}"', _remove))

        def _add() -> None:
            entry = open_add_picker(self, commodity_entries(), title="Add Commodity")
            if entry is None:
                return
            # file the pick into the group matching its catalog category;
            # free-text symbols land in the group that was right-clicked
            # (or Energy when the click wasn't on a row)
            by_category = {title: key for key, title in GROUP_KEYS}
            key = by_category.get(entry.category) or hovered_key or "energy"
            self._apply_edit(
                {key: [list(r) for r in self._groups[key]] + [[entry.label, entry.code]]}
            )

        actions.append(("Add commodity…", _add))
        actions.append(("Edit panel…", self._open_edit_dialog))
        return actions

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        out = {key: [list(r) for r in self._groups[key]] for key, _t in GROUP_KEYS}
        out["hidden_cols"] = self.table.hidden_columns()
        return out

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        changed = False
        for key, _title in GROUP_KEYS:
            rows = settings.get(key)
            if not isinstance(rows, list):
                continue
            cleaned = [
                [str(r[0]), str(r[1]).upper()]
                for r in rows
                if isinstance(r, list) and len(r) == 2
            ]
            # legacy layouts stored only energy/metals and never empty lists;
            # an explicit empty list from a newer layout is honored
            if cleaned or rows == []:
                self._groups[key] = cleaned
                changed = True
        if changed:
            self._rebuild_table()
        self.table.set_hidden_columns(settings.get("hidden_cols", []))
