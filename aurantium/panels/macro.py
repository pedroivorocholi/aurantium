"""Macro / Rates panel — the weekly macro-check panel: a configurable US
Treasury yield curve, a configurable macro instrument monitor (dollar index,
optional FRED series such as real yields), and CFTC positioning for a
configurable market set defaulting to the five Koji commodities.

Everything is editable via the Edit… dialog and persisted with the layout
(settings/restore), mirroring commodities.py. Instrument and CFTC rows are
clickable and drive the panel's link group.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidgetItem,
)

from ..commodities_meta import COMMODITIES, by_cftc_market, by_symbol
from ..components import (
    FRED_ENTRIES,
    FX_ENTRIES,
    INDEX_ENTRIES,
    TENOR_ENTRIES,
    CatalogEntry,
    EditorColumn,
    EditorSection,
    MarketTable,
    attach_hover,
    clamp_view,
    open_add_picker,
    open_list_editor,
)
from ..panel import Panel, register_panel
from ..undo import UndoStack
from ..theme import ACCENT, BG, DOWN, FG, FG_DIM, UP, apply_tick

# (maturity years, curve tick label, quote ticker) — price field IS the yield
DEFAULT_TENORS = [
    [0.25, "3M", "^IRX"],
    [5.0, "5Y", "^FVX"],
    [10.0, "10Y", "^TNX"],
    [30.0, "30Y", "^TYX"],
]

# (label, target) — target is a quote SYMBOL, or "fred:SERIES_ID" for a FRED
# data series (needs a free FRED key under Settings ▸ API Keys…)
DEFAULT_INSTRUMENTS = [
    ["Dollar Index", "DX-Y.NYB"],
]

# (label, CFTC market key) — the five Koji commodities by default
DEFAULT_CFTC = [
    ["Gold", "gold"],
    ["Silver", "silver"],
    ["Copper", "copper"],
    ["Brent Crude", "brent"],
    ["Henry Hub NatGas", "natgas"],
]

INST_COL_NAME, INST_COL_LAST, INST_COL_CHG, INST_COL_CHGPCT = range(4)
INST_HEADERS = ["Instrument", "Last", "Chg", "Chg%"]

CFTC_COL_MARKET, CFTC_COL_NETSPEC, CFTC_COL_WOW, CFTC_COL_BIAS = range(4)
CFTC_HEADERS = ["Market", "Net Spec", "W/W Chg", "Bias"]


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


#: choices for the positioning Market dropdown: every commodity the app knows
#: plus the financial COT markets the provider also serves
_MARKET_CHOICES = [
    (c.cftc_market, c.label) for c in COMMODITIES if c.cftc_market
] + [
    ("sp500", "S&P 500 (E-mini)"),
    ("bitcoin", "Bitcoin"),
    ("euro_fx", "Euro FX"),
]

#: choices for the instrument Source dropdown
_SOURCE_CHOICES = [
    ("quote", "Quote symbol"),
    ("fred", "FRED series"),
]

#: picker entries for the positioning quick-add (code = CFTC market key)
_MARKET_ENTRIES = [
    CatalogEntry(label, value, "quote", "CFTC") for value, label in _MARKET_CHOICES
]

#: known tenor symbols → (maturity years, curve tick label)
_TENOR_DETAILS = {
    "^IRX": (0.25, "3M"),
    "^FVX": (5.0, "5Y"),
    "^TNX": (10.0, "10Y"),
    "^TYX": (30.0, "30Y"),
}


def _tenor_row_from_entry(entry: CatalogEntry) -> list:
    years, label = _TENOR_DETAILS.get(entry.code, (0.0, entry.label))
    return [years, label, entry.code]


@register_panel(id="macro", title="Macro / Rates", category="Analytics")
class MacroPanel(Panel):
    def build(self) -> None:
        self._tenors: list = [list(r) for r in DEFAULT_TENORS]
        self._instruments: list = [list(r) for r in DEFAULT_INSTRUMENTS]
        self._cftc: list = [list(r) for r in DEFAULT_CFTC]

        self._yields: dict[str, Optional[float]] = {}
        self._cftc_loaded: set[str] = set()
        self._inst_row_of_target: dict[str, int] = {}
        self._cftc_row_of_market: dict[str, int] = {}

        # -- (a) US Treasury yield curve ------------------------------------------
        curve_title = QLabel("US Treasury Yield Curve", self)
        curve_title.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.content_layout.addWidget(curve_title)

        self.curve_widget = pg.PlotWidget()
        self.curve_widget.setBackground(BG)
        self.curve_widget.showGrid(x=True, y=True, alpha=0.15)
        self.curve_widget.setLabel("left", "Yield (%)")
        self.curve_widget.getAxis("bottom").setTextPen(FG_DIM)
        self.curve_widget.getAxis("left").setTextPen(FG_DIM)
        self.yield_curve = pg.PlotDataItem(
            pen=pg.mkPen(ACCENT, width=2),
            symbol="o",
            symbolBrush=ACCENT,
            symbolPen=ACCENT,
            symbolSize=8,
        )
        self.curve_widget.addItem(self.yield_curve)
        attach_hover(self.curve_widget, self._curve_hover_text)
        self.content_layout.addWidget(self.curve_widget, 2)

        self.spread_lbl = QLabel("", self)
        self.spread_lbl.setStyleSheet(f"color: {FG_DIM};")
        self.content_layout.addWidget(self.spread_lbl)

        # -- (b) macro instrument monitor -------------------------------------------
        inst_title = QLabel("Macro Monitor", self)
        inst_title.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.content_layout.addWidget(inst_title)

        self.inst_table = MarketTable(0, len(INST_HEADERS), self)
        self.inst_table.setHorizontalHeaderLabels(INST_HEADERS)
        header = self.inst_table.horizontalHeader()
        header.setSectionResizeMode(INST_COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        for col in (INST_COL_LAST, INST_COL_CHG, INST_COL_CHGPCT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.inst_table.itemSelectionChanged.connect(self._on_inst_selected)
        self.inst_table.set_row_actions(self._inst_row_actions)
        self.content_layout.addWidget(self.inst_table, 1)

        # -- (c) CFTC positioning ---------------------------------------------------
        cftc_title = QLabel("Positioning (CFTC)", self)
        cftc_title.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        cftc_title.setToolTip(
            "Net futures position of large speculative traders — weekly CFTC\n"
            "Commitments of Traders data. Click a row to drive linked panels."
        )
        self.content_layout.addWidget(cftc_title)

        self.cftc_table = MarketTable(0, len(CFTC_HEADERS), self)
        self.cftc_table.setHorizontalHeaderLabels(CFTC_HEADERS)
        header = self.cftc_table.horizontalHeader()
        header.setSectionResizeMode(CFTC_COL_MARKET, QHeaderView.ResizeMode.ResizeToContents)
        for col in (CFTC_COL_NETSPEC, CFTC_COL_WOW, CFTC_COL_BIAS):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.cftc_table.itemSelectionChanged.connect(self._on_cftc_selected)
        self.cftc_table.set_row_actions(self._cftc_row_actions)
        self.content_layout.addWidget(self.cftc_table, 1)

        edit_row = QHBoxLayout()
        edit_row.addStretch(1)
        edit_btn = QPushButton("Edit…", self)
        edit_btn.clicked.connect(self._open_edit_dialog)
        edit_row.addWidget(edit_btn)
        self.content_layout.addLayout(edit_row)

        self.set_status("loading…")
        self._rebuild()

    # -- (re)construction: rows, curve axis, subscriptions ------------------------

    def _rebuild(self) -> None:
        """Rebuild both tables and the curve axis, and resubscribe every
        topic — mirrors commodities.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        self._yields = {sym: None for _y, _label, sym in self._tenors}
        self._cftc_loaded.clear()

        axis_bottom = self.curve_widget.getAxis("bottom")
        axis_bottom.setTicks([[(y, label) for y, label, _s in self._tenors]])
        self._redraw_curve()

        self.inst_table.setRowCount(0)
        self._inst_row_of_target.clear()
        for label, target in self._instruments:
            row = self.inst_table.rowCount()
            self.inst_table.insertRow(row)
            name_item = QTableWidgetItem(label)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.inst_table.setItem(row, INST_COL_NAME, name_item)
            for col in (INST_COL_LAST, INST_COL_CHG, INST_COL_CHGPCT):
                item = QTableWidgetItem("-")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.inst_table.setItem(row, col, item)
            self._inst_row_of_target[target] = row

        self.cftc_table.setRowCount(0)
        self._cftc_row_of_market.clear()
        for label, market in self._cftc:
            row = self.cftc_table.rowCount()
            self.cftc_table.insertRow(row)
            name_item = QTableWidgetItem(label)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.cftc_table.setItem(row, CFTC_COL_MARKET, name_item)
            for col in (CFTC_COL_NETSPEC, CFTC_COL_WOW, CFTC_COL_BIAS):
                item = QTableWidgetItem("-")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.cftc_table.setItem(row, col, item)
            self._cftc_row_of_market[market] = row
        self._highlight_cftc_row(self.current_symbol)

        for _y, _label, sym in self._tenors:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_yield_quote(s, data))
        for _label, target in self._instruments:
            if target.lower().startswith("fred:"):
                series = target.split(":", 1)[1]
                self.subscribe(
                    f"fred:{series}",
                    lambda data, t=target: self._on_inst_fred(t, data),
                    on_error=lambda err, t=target: self._on_inst_error(t, err),
                )
            else:
                self.subscribe(
                    f"quote:{target}",
                    lambda data, t=target: self._on_inst_quote(t, data),
                    on_error=lambda err, t=target: self._on_inst_error(t, err),
                )
        for _label, market in self._cftc:
            self.subscribe(f"cftc:{market}", lambda data, m=market: self._on_cftc(m, data))
        self._update_status()

    # -- yield curve -------------------------------------------------------------

    def _on_yield_quote(self, ticker: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._yields[ticker] = data.get("price")
        self._redraw_curve()
        self._update_status()

    def _redraw_curve(self) -> None:
        xs: list[float] = []
        ys: list[float] = []
        for years, _label, sym in self._tenors:
            y = self._yields.get(sym)
            if y is None:
                continue
            xs.append(float(years))
            ys.append(float(y))
        self.yield_curve.setData(xs, ys)
        clamp_view(self.curve_widget, xs, ys, lock=True)
        self._update_spread()

    def _curve_hover_text(self, x: float, _y: float) -> Optional[str]:
        """Readout for the tenor nearest the cursor — '10Y · 4.21%'."""
        best = None
        for years, label, sym in self._tenors:
            value = self._yields.get(sym)
            if value is None:
                continue
            distance = abs(float(years) - x)
            if best is None or distance < best[0]:
                best = (distance, label, float(value))
        if best is None:
            return None
        return f"{best[1]} · {best[2]:.2f}%"

    def _update_spread(self) -> None:
        """Short-vs-10Y spread when the tenor set has both ends: the shortest
        tenor (≤ 0.5y) and the tenor nearest 10y (within 9–11y)."""
        short = min((t for t in self._tenors if t[0] <= 0.5), default=None, key=lambda t: t[0])
        long = min(
            (t for t in self._tenors if 9.0 <= t[0] <= 11.0),
            default=None,
            key=lambda t: abs(t[0] - 10.0),
        )
        if short is None or long is None:
            self.spread_lbl.setVisible(False)
            return
        self.spread_lbl.setVisible(True)
        sy = self._yields.get(short[2])
        ly = self._yields.get(long[2])
        name = f"{long[1]}–{short[1]} spread"
        if sy is None or ly is None:
            self.spread_lbl.setText(f"{name}: —")
            self.spread_lbl.setStyleSheet(f"color: {FG_DIM};")
            return
        spread_bp = (float(ly) - float(sy)) * 100.0
        color = DOWN if spread_bp < 0 else UP
        self.spread_lbl.setText(f"{name}: {spread_bp:+.0f} bp")
        self.spread_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # -- macro instrument monitor ----------------------------------------------------

    def _inst_items(self, target: str):
        row = self._inst_row_of_target.get(target)
        if row is None:
            return None
        items = tuple(
            self.inst_table.item(row, col)
            for col in (INST_COL_LAST, INST_COL_CHG, INST_COL_CHGPCT)
        )
        return items if all(items) else None

    def _on_inst_quote(self, target: str, data: Any) -> None:
        items = self._inst_items(target)
        if items is None or not isinstance(data, dict):
            return
        last_item, chg_item, pct_item = items
        change = data.get("change")
        last_item.setText(_fmt_num(data.get("price")))
        last_item.setToolTip("")
        chg_item.setText(_fmt_num(change))
        change_pct = data.get("change_pct")
        pct_item.setText(f"{_fmt_num(change_pct)}%" if change_pct is not None else "-")
        if change is not None:
            apply_tick(chg_item, change, glyph=False)
            apply_tick(pct_item, change)
        else:
            dim = QColor(FG_DIM)
            chg_item.setForeground(dim)
            pct_item.setForeground(dim)

    def _on_inst_fred(self, target: str, data: Any) -> None:
        """A FRED series row: latest observation vs. the previous one."""
        items = self._inst_items(target)
        if items is None or not isinstance(data, dict):
            return
        last_item, chg_item, pct_item = items
        values = [v for _d, v in data.get("points", []) if v is not None]
        last = values[-1] if values else None
        prev = values[-2] if len(values) > 1 else None
        last_item.setText(_fmt_num(last))
        last_item.setToolTip(str(data.get("title") or ""))
        change = (last - prev) if (last is not None and prev is not None) else None
        chg_item.setText(_fmt_num(change))
        pct = (change / prev * 100.0) if (change is not None and prev) else None
        pct_item.setText(f"{_fmt_num(pct)}%" if pct is not None else "-")
        if change is not None:
            apply_tick(chg_item, change, glyph=False)
            apply_tick(pct_item, change)

    def _on_inst_error(self, target: str, error: str) -> None:
        items = self._inst_items(target)
        if items is None:
            return
        last_item = items[0]
        if last_item.text() not in ("-", "no key"):
            return  # keep last-known data over an error message
        last_item.setText("no key" if "API_KEY" in error else "-")
        last_item.setForeground(QColor(FG_DIM))
        last_item.setToolTip(error)

    def _on_inst_selected(self) -> None:
        model = self.inst_table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        row = rows[0].row()
        for target, r in self._inst_row_of_target.items():
            if r == row and not target.lower().startswith("fred:"):
                self.set_symbol(target)
                return

    # -- CFTC positioning ----------------------------------------------------------

    def _on_cftc(self, market: str, data: Any) -> None:
        row = self._cftc_row_of_market.get(market)
        if row is None or not isinstance(data, dict):
            return
        self._cftc_loaded.add(market)
        net_item = self.cftc_table.item(row, CFTC_COL_NETSPEC)
        wow_item = self.cftc_table.item(row, CFTC_COL_WOW)
        bias_item = self.cftc_table.item(row, CFTC_COL_BIAS)
        if not (net_item and wow_item and bias_item):
            return
        net_spec = data.get("noncommercial_net")
        prev = data.get("noncommercial_net_prev")
        net_item.setText(_fmt_num(net_spec, 0))
        wow = (net_spec - prev) if (net_spec is not None and prev is not None) else None
        if wow is not None:
            apply_tick(wow_item, wow, text=f"{wow:+,.0f}")
        else:
            wow_item.setText("-")
            wow_item.setForeground(QColor(FG_DIM))
        bias = data.get("bias")
        bias_text = str(bias) if bias is not None else "-"
        bias_item.setText(bias_text)
        low = bias_text.lower()
        if "bull" in low:
            bias_item.setForeground(QColor(UP))
        elif "bear" in low:
            bias_item.setForeground(QColor(DOWN))
        else:
            bias_item.setForeground(QColor(FG_DIM))
        report_date = str(data.get("report_date") or "")[:10]
        if report_date:
            net_item.setToolTip(f"CFTC report dated {report_date}")
        self._update_status()

    def _on_cftc_selected(self) -> None:
        model = self.cftc_table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        row = rows[0].row()
        for market, r in self._cftc_row_of_market.items():
            if r == row:
                meta = by_cftc_market(market)
                if meta is not None:
                    self.set_symbol(meta.symbol)
                return

    # -- linked-symbol behavior -----------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        """Highlight the positioning row matching the linked symbol (when it
        maps to one of our markets) — the panel's data set doesn't change."""
        self._highlight_cftc_row(symbol)

    def _highlight_cftc_row(self, symbol: str) -> None:
        meta = by_symbol(symbol) if symbol else None
        active = meta.cftc_market if meta is not None else None
        for market, row in self._cftc_row_of_market.items():
            item = self.cftc_table.item(row, CFTC_COL_MARKET)
            if item is None:
                continue
            font = item.font()
            font.setBold(market == active)
            item.setFont(font)
            item.setForeground(QColor(ACCENT if market == active else FG))

    # -- status -----------------------------------------------------------------------

    def _update_status(self) -> None:
        yields_loaded = sum(1 for v in self._yields.values() if v is not None)
        cftc_loaded = len(self._cftc_loaded)
        if yields_loaded == len(self._tenors) and cftc_loaded == len(self._cftc):
            self.set_status("ready")
        else:
            self.set_status(
                f"yields {yields_loaded}/{len(self._tenors)} · CFTC {cftc_loaded}/{len(self._cftc)}"
            )

    # -- edit dialog & quick actions -----------------------------------------

    def _apply_edit(
        self,
        tenors: Optional[list] = None,
        instruments: Optional[list] = None,
        cftc: Optional[list] = None,
    ) -> None:
        """Apply a config change (None = keep) behind one undo snapshot —
        shared by the Edit dialog and the right-click quick actions."""
        snap_t = [list(r) for r in self._tenors]
        snap_i = [list(r) for r in self._instruments]
        snap_c = [list(r) for r in self._cftc]

        def _undo() -> None:
            self._tenors = [list(r) for r in snap_t]
            self._instruments = [list(r) for r in snap_i]
            self._cftc = [list(r) for r in snap_c]
            self._rebuild()
            self.set_status("undo · edit macro")

        UndoStack.instance().push("edit macro", _undo)
        if tenors is not None:
            self._tenors = tenors
        if instruments is not None:
            self._instruments = instruments
        if cftc is not None:
            self._cftc = cftc
        self._rebuild()

    def _open_edit_dialog(self) -> None:
        # instruments are stored as [label, "SYM"] or [label, "fred:SERIES"];
        # the editor shows them as Label / Source dropdown / Code
        inst_rows = []
        for label, target in self._instruments:
            if target.lower().startswith("fred:"):
                inst_rows.append([label, "fred", target.split(":", 1)[1]])
            else:
                inst_rows.append([label, "quote", target])

        result = open_list_editor(
            self,
            "Edit Macro / Rates",
            [
                EditorSection(
                    "tenors",
                    "Yield Curve",
                    [
                        EditorColumn("Maturity (yrs)", kind="number", maximum=100.0),
                        EditorColumn("Label"),
                        EditorColumn("Symbol", kind="symbol"),
                    ],
                    self._tenors,
                    description="The yield of each US Treasury maturity, drawn "
                    "left to right as the curve. The quoted price of a yield "
                    "index (^TNX…) is the yield in percent.",
                    catalog=TENOR_ENTRIES,
                    presets=[
                        (
                            "Full curve",
                            [
                                [0.25, "3M", "^IRX"],
                                [5.0, "5Y", "^FVX"],
                                [10.0, "10Y", "^TNX"],
                                [30.0, "30Y", "^TYX"],
                            ],
                        )
                    ],
                    row_factory=_tenor_row_from_entry,
                ),
                EditorSection(
                    "instruments",
                    "Macro Monitor",
                    [
                        EditorColumn("Label"),
                        EditorColumn("Source", kind="choice", choices=_SOURCE_CHOICES),
                        EditorColumn("Code", kind="symbol"),
                    ],
                    inst_rows,
                    description="Live instruments in the monitor table — any "
                    "Yahoo quote, or a FRED data series (needs a free key "
                    "under Settings ▸ API Keys…).",
                    catalog=FX_ENTRIES + INDEX_ENTRIES + FRED_ENTRIES,
                    presets=[
                        ("10Y real yield", [["10Y Real Yield", "fred", "DFII10"]]),
                        ("10Y breakeven", [["10Y Breakeven", "fred", "T10YIE"]]),
                        ("EUR/USD", [["EUR/USD", "quote", "EURUSD=X"]]),
                        ("VIX", [["VIX", "quote", "^VIX"]]),
                    ],
                ),
                EditorSection(
                    "cftc",
                    "Positioning",
                    [
                        EditorColumn("Label"),
                        EditorColumn("Market", kind="choice", choices=_MARKET_CHOICES),
                    ],
                    self._cftc,
                    description="Weekly CFTC report of how large speculators "
                    "(hedge funds, money managers) are positioned in each "
                    "futures market — pick markets to watch.",
                    presets=[("Koji five", [list(r) for r in DEFAULT_CFTC])],
                ),
            ],
        )
        if result is not None:
            tenors = sorted(result["tenors"], key=lambda r: r[0])
            instruments = [
                [label, code if source == "quote" else f"fred:{code}"]
                for label, source, code in result["instruments"]
            ]
            cftc = result["cftc"]
            if tenors or instruments or cftc:
                self._apply_edit(
                    tenors or None, instruments or None, cftc or None
                )

    def _inst_row_actions(self, row: int) -> list:
        actions = []
        if 0 <= row < len(self._instruments):
            label = self._instruments[row][0]

            def _remove(r=row) -> None:
                rows = [list(x) for x in self._instruments]
                del rows[r]
                self._apply_edit(instruments=rows)

            actions.append((f'Remove "{label}"', _remove))

        def _add() -> None:
            entry = open_add_picker(
                self, FX_ENTRIES + INDEX_ENTRIES + FRED_ENTRIES, title="Add Instrument"
            )
            if entry is None:
                return
            target = f"fred:{entry.code}" if entry.kind == "fred" else entry.code
            rows = [list(x) for x in self._instruments] + [[entry.label, target]]
            self._apply_edit(instruments=rows)

        actions.append(("Add instrument…", _add))
        actions.append(("Edit panel…", self._open_edit_dialog))
        return actions

    def _cftc_row_actions(self, row: int) -> list:
        actions = []
        if 0 <= row < len(self._cftc):
            label = self._cftc[row][0]

            def _remove(r=row) -> None:
                rows = [list(x) for x in self._cftc]
                del rows[r]
                self._apply_edit(cftc=rows)

            actions.append((f'Remove "{label}"', _remove))

        def _add() -> None:
            entry = open_add_picker(
                self, _MARKET_ENTRIES, allow_free_text=False, title="Add Market"
            )
            if entry is not None:
                rows = [list(x) for x in self._cftc] + [[entry.label, entry.code]]
                self._apply_edit(cftc=rows)

        actions.append(("Add market…", _add))
        actions.append(("Edit panel…", self._open_edit_dialog))
        return actions

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "tenors": [list(r) for r in self._tenors],
            "instruments": [list(r) for r in self._instruments],
            "cftc": [list(r) for r in self._cftc],
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        changed = False
        tenors = settings.get("tenors")
        if isinstance(tenors, list) and tenors:
            cleaned = []
            for r in tenors:
                if isinstance(r, list) and len(r) == 3:
                    try:
                        cleaned.append([float(r[0]), str(r[1]), str(r[2]).upper()])
                    except (TypeError, ValueError):
                        continue
            if cleaned:
                cleaned.sort(key=lambda r: r[0])
                self._tenors = cleaned
                changed = True
        instruments = settings.get("instruments")
        if isinstance(instruments, list) and instruments:
            cleaned = [
                [str(r[0]), str(r[1])]
                for r in instruments
                if isinstance(r, list) and len(r) == 2
            ]
            if cleaned:
                self._instruments = cleaned
                changed = True
        cftc = settings.get("cftc")
        if isinstance(cftc, list) and cftc:
            cleaned = [
                [str(r[0]), str(r[1]).lower()]
                for r in cftc
                if isinstance(r, list) and len(r) == 2
            ]
            if cleaned:
                self._cftc = cleaned
                changed = True
        if changed:
            self._rebuild()
