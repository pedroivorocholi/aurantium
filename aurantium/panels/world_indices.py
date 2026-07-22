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
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidgetItem,
)

from ..components import (
    INDEX_ENTRIES,
    EditorColumn,
    EditorSection,
    MarketTable,
    make_filter_edit,
    open_add_picker,
    open_list_editor,
)
from ..panel import Panel, register_panel
from ..undo import UndoStack
from ..theme import ACCENT, BG_HEADER, FG_DIM, apply_tick

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


@register_panel(id="world_indices", title="World Indices", category="Markets")
class WorldIndicesPanel(Panel):
    def build(self) -> None:
        self._americas: list = [list(row) for row in DEFAULT_AMERICAS]
        self._europe: list = [list(row) for row in DEFAULT_EUROPE]
        self._asia: list = [list(row) for row in DEFAULT_ASIA]
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

        self._filter = make_filter_edit(self.table, "Filter indices…")
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

        self._append_group_header("Americas")
        for label, sym in self._americas:
            self._append_data_row(label, sym)

        self._append_group_header("Europe")
        for label, sym in self._europe:
            self._append_data_row(label, sym)

        self._append_group_header("Asia/Pacific")
        for label, sym in self._asia:
            self._append_data_row(label, sym)

        if hasattr(self, "_filter"):
            self.table.apply_filter(self._filter.text())

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

    def _apply_edit(self, americas=None, europe=None, asia=None) -> None:
        """Apply a config change (None = keep) behind one undo snapshot —
        shared by the Edit dialog and the right-click quick actions."""
        snap_am = [list(r) for r in self._americas]
        snap_eu = [list(r) for r in self._europe]
        snap_as = [list(r) for r in self._asia]

        def _undo() -> None:
            self._americas = [list(r) for r in snap_am]
            self._europe = [list(r) for r in snap_eu]
            self._asia = [list(r) for r in snap_as]
            self._rebuild_table()
            self.set_status("undo · edit indices")

        UndoStack.instance().push("edit indices", _undo)
        if americas is not None:
            self._americas = americas
        if europe is not None:
            self._europe = europe
        if asia is not None:
            self._asia = asia
        self._rebuild_table()

    def _open_edit_dialog(self) -> None:
        columns = [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")]

        def section(key: str, title: str, rows: list, blurb: str) -> EditorSection:
            return EditorSection(
                key,
                title,
                columns,
                rows,
                description=blurb,
                catalog=INDEX_ENTRIES,
            )

        result = open_list_editor(
            self,
            "Edit World Indices",
            [
                section(
                    "americas",
                    "Americas",
                    self._americas,
                    "Live index quotes in the Americas group — any Yahoo "
                    "Finance index symbol works (^GSPC, ^BVSP…).",
                ),
                section(
                    "europe",
                    "Europe",
                    self._europe,
                    "Live index quotes in the Europe group (^FTSE, ^GDAXI…).",
                ),
                section(
                    "asia",
                    "Asia/Pacific",
                    self._asia,
                    "Live index quotes in the Asia/Pacific group (^N225, "
                    "^HSI, 000001.SS…).",
                ),
            ],
        )
        if result is not None:
            americas = result["americas"]
            europe = result["europe"]
            asia = result["asia"]
            if americas or europe or asia:
                self._apply_edit(americas or None, europe or None, asia or None)

    def _row_actions(self, row: int) -> list:
        groups = [
            ("Americas", self._americas, "americas"),
            ("Europe", self._europe, "europe"),
            ("Asia/Pacific", self._asia, "asia"),
        ]
        actions = []
        kind, symbol = self._row_kind.get(row, (None, None))
        if kind == ROW_KIND_DATA and symbol:
            for _title, rows, key in groups:
                label = next((l for l, s in rows if s == symbol), None)
                if label is None:
                    continue

                def _remove(k=key, sym=symbol, group_rows=rows) -> None:
                    remaining = [list(r) for r in group_rows if r[1] != sym]
                    self._apply_edit(**{k: remaining})

                actions.append((f'Remove "{label}"', _remove))
                break

        def _add(key: str, title: str, rows: list) -> None:
            entry = open_add_picker(self, INDEX_ENTRIES, title=f"Add to {title}")
            if entry is not None:
                self._apply_edit(
                    **{key: [list(r) for r in rows] + [[entry.label, entry.code]]}
                )

        for title, rows, key in groups:
            actions.append(
                (f"Add to {title}…", lambda t=title, r=rows, k=key: _add(k, t, r))
            )
        actions.append(("Edit panel…", self._open_edit_dialog))
        return actions

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "americas": [list(r) for r in self._americas],
            "europe": [list(r) for r in self._europe],
            "asia": [list(r) for r in self._asia],
            "hidden_cols": self.table.hidden_columns(),
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
        self.table.set_hidden_columns(settings.get("hidden_cols", []))
