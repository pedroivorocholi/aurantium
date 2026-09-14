"""Portfolio panel — user-entered positions marked to live quotes, plus
analytics tabs (Allocation / Performance / Risk).

Driver panel: clicking a position row publishes ``set_symbol`` like
watchlist.py, but this panel never *follows* the linked symbol (no
``on_symbol`` override) so navigating elsewhere never disturbs the list.

The analytics tabs are lazy: their ``profile:*`` (sector, beta) and
``history:*`` subscriptions only start once the tab is first opened.
"""

from __future__ import annotations

import bisect
from datetime import date, datetime
from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..components import attach_suggestions
from ..panel import NULL_GLYPH, Panel, register_panel
from ..theme import (
    ACCENT,
    ACCENT_DEEP,
    FONT_TITLE,
    BG_HEADER,
    FG,
    FG_DIM,
    FG_MUTED,
    apply_tick,
)
from ..undo import UndoStack

(COL_SYMBOL, COL_QTY, COL_COST, COL_DATE, COL_LAST, COL_MKTVAL, COL_PNL,
 COL_PNLPCT) = range(8)
HEADERS = ["Symbol", "Qty", "Cost", "Date", "Last", "Mkt Value", "P&L", "P&L%"]

_BENCH = "SPY"          # performance benchmark
_HIST_TOPIC = "1y:1d"   # history window used by the analytics tabs


def _fmt(value: Any, decimals: int = 2) -> str:
    if value is None:
        return NULL_GLYPH
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return NULL_GLYPH


# -- pure analytics helpers (unit-tested) -----------------------------------

def sector_weights(positions: list, last_price: dict, sectors: dict) -> dict:
    """{sector: market_value} = Σ qty·last per position, grouped by sector
    (missing sector → 'Unknown'). Positions without a live price are skipped."""
    out: dict[str, float] = {}
    for p in positions:
        last = last_price.get(p["symbol"])
        if last is None:
            continue
        sec = sectors.get(p["symbol"]) or "Unknown"
        out[sec] = out.get(sec, 0.0) + last * p["qty"]
    return out


def weighted_beta(positions: list, last_price: dict, betas: dict) -> Optional[float]:
    """Market-value-weighted portfolio beta over positions that have both a live
    price and a known beta."""
    total = 0.0
    acc = 0.0
    for p in positions:
        last = last_price.get(p["symbol"])
        beta = betas.get(p["symbol"])
        if last is None or beta is None:
            continue
        val = last * p["qty"]
        total += val
        acc += val * beta
    return acc / total if total else None


def concentration(positions: list, last_price: dict) -> Optional[float]:
    """Largest single position as a % of total market value (priced positions)."""
    vals = [
        last_price[p["symbol"]] * p["qty"]
        for p in positions
        if last_price.get(p["symbol"]) is not None
    ]
    total = sum(vals)
    return (max(vals) / total * 100.0) if (vals and total) else None


def iso_to_ts(value: Any) -> Optional[float]:
    """Epoch seconds for an ``YYYY-MM-DD`` (or ISO datetime) string, at local
    midnight. None if unparseable/empty."""
    if not value:
        return None
    try:
        d = date.fromisoformat(str(value)[:10])
        return datetime(d.year, d.month, d.day).timestamp()
    except (TypeError, ValueError):
        return None


def portfolio_series(positions: list, histories: dict) -> tuple:
    """(timestamps, values) of the portfolio's marked-to-market value over time,
    counting each position only while it was held.

    ``histories`` maps symbol → (ts_list, close_list), ascending. Each position
    may carry ``buy_ts`` / ``sell_ts`` (epoch or None); a position contributes
    ``qty · close`` at every timestamp ``buy_ts ≤ t < sell_ts`` for which its
    symbol has a bar at or before ``t`` (last close forward-filled). The series
    starts at the earliest buy so a freshly opened book doesn't fake a year of
    history."""
    held = [p for p in positions if p["symbol"] in histories and histories[p["symbol"]][0]]
    if not held:
        return ([], [])
    buys = [p.get("buy_ts") for p in held if p.get("buy_ts") is not None]
    start = min(buys) if buys else min(histories[p["symbol"]][0][0] for p in held)
    all_ts = sorted({t for p in held for t in histories[p["symbol"]][0] if t >= start})
    if not all_ts:
        return ([], [])
    values = []
    for t in all_ts:
        v = 0.0
        for p in held:
            bt, st = p.get("buy_ts"), p.get("sell_ts")
            if bt is not None and t < bt:
                continue
            if st is not None and t >= st:
                continue
            ts_list, close_list = histories[p["symbol"]]
            j = bisect.bisect_right(ts_list, t) - 1
            if j >= 0:
                v += p["qty"] * close_list[j]
        values.append(v)
    return (all_ts, values)


def max_drawdown(values: list) -> Optional[float]:
    """Worst peak-to-trough decline of a value series, as a (negative) percent."""
    if not values:
        return None
    peak = values[0]
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return mdd * 100.0


def _normalize_to_100(values: list) -> list:
    base = next((v for v in values if v), None)
    if not base:
        return values
    return [v / base * 100.0 for v in values]


# -- allocation pie widget --------------------------------------------------

class _AllocationBars(QWidget):
    """Sector allocation as a ranked horizontal bar list.

    This replaced a hand-painted pie of up to eleven slices, and the reason is
    not taste. A pie needs one distinguishable colour per slice, and eleven do
    not exist here: five hues are already reserved for price and direction, and
    the shared series palette tops out at four. The list it used was invented
    rather than derived, and it showed — ``#8bc34a`` and ``#7ed321`` sat ΔE 5.8
    apart under *normal* vision (indistinguishable), ``#ffca28`` and ``#7ed321``
    ΔE 2.1 under protanopia, and ``#f8e71c`` measured 1.28:1 on the light
    theme's white. Slices were also assigned by rank, so adding one position
    reordered the sectors and repainted every one of them; a reader who had
    learned "energy is the yellow one" was simply wrong after the next trade.

    A ranked bar needs **no categorical colour at all**: position encodes rank,
    length encodes magnitude, and each row carries its own name. That removes
    the constraint instead of trying to satisfy it, which is why the whole
    problem disappears rather than moving somewhere else.
    """

    ROW_H = 18
    ROW_GAP = 4
    PAD = 4
    #: Rows past this fold into a single "Other" row. Part-to-whole stops being
    #: readable at a glance well before eleven bars.
    MAX_ROWS = 6

    #: The label of the folded remainder. Kept last by convention even when it
    #: outweighs rows above it — it is a residual, not a sector — and drawn in a
    #: neutral so the broken descent reads as "this is a bucket" rather than as
    #: a sorting bug.
    OTHER = "Other"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, float]] = []
        self.setMinimumHeight(self.ROW_H * 3)

    def set_rows(self, rows: list[tuple[str, float]]) -> None:
        """``rows`` is (label, value), any order; sorted and folded here."""
        ordered = sorted(rows, key=lambda kv: kv[1], reverse=True)
        if len(ordered) > self.MAX_ROWS:
            head = ordered[: self.MAX_ROWS - 1]
            tail = sum(v for _l, v in ordered[self.MAX_ROWS - 1:])
            ordered = head + [(self.OTHER, tail)]
        self._rows = ordered
        self.updateGeometry()
        self.update()

    def sizeHint(self):  # noqa: N802 (Qt override)
        n = max(len(self._rows), 3)
        return QSize(240, n * (self.ROW_H + self.ROW_GAP) + self.PAD * 2)

    minimumSizeHint = sizeHint

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Name gutter, then bar, then value — no text on the fill.

        The first version of this drew the sector name *inside* the bar, which
        put ``FG`` on ``ACCENT`` at **1.24:1** on the dark theme: the exact
        failure being fixed everywhere else in this pass, reintroduced by the
        fix. Text on a mark inherits the mark's contrast, and a mark coloured
        for emphasis is the worst possible surface for it. Both label and value
        now sit on the panel background, where they measure 13.8:1 and the bar
        is free to be as saturated as it likes.
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows:
            p.setPen(QColor(FG_DIM))
            p.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No priced positions"
            )
            p.end()
            return

        total = sum(v for _l, v in self._rows) or 1.0
        biggest = max(v for _l, v in self._rows) or 1.0

        metrics = p.fontMetrics()
        # Gutter fits the longest name, but never eats the plot: past 45% the
        # bars stop being comparable, which is the only thing they are for.
        names_w = max(metrics.horizontalAdvance(l) for l, _v in self._rows)
        value_w = metrics.horizontalAdvance("100.0%")
        avail = self.width() - self.PAD * 2
        gutter = min(names_w + 10, int(avail * 0.45))
        track = max(10, avail - gutter - value_w - 10)

        y = self.PAD
        font = p.font()
        for i, (label, value) in enumerate(self._rows):
            pct = value / total * 100.0
            bar_w = max(2, round(track * (value / biggest)))

            font.setBold(i == 0)
            p.setFont(font)

            p.setPen(QColor(FG_DIM if label == self.OTHER else FG))
            p.drawText(
                self.PAD, y, gutter - 6, self.ROW_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                metrics.elidedText(label, Qt.TextElideMode.ElideRight, gutter - 6),
            )

            # The largest holding is the concentration-risk number and takes the
            # accent; the rest share one recessive shade. One hue, two weights —
            # emphasis, not identity, so no categorical palette is needed.
            residual = label == self.OTHER
            p.setBrush(
                QColor(FG_MUTED if residual else (ACCENT if i == 0 else ACCENT_DEEP))
            )
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(
                self.PAD + gutter, y + 3, bar_w, self.ROW_H - 6, 2, 2
            )

            p.setPen(QColor(FG if i == 0 else FG_DIM))
            p.drawText(
                self.PAD + gutter + track + 4, y, value_w + 6, self.ROW_H,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{pct:.1f}%",
            )
            y += self.ROW_H + self.ROW_GAP
        p.end()


@register_panel(id="portfolio", title="Portfolio", category="Analytics")
class PortfolioPanel(Panel):
    def build(self) -> None:
        self._positions: list[dict] = []  # [{"symbol", "qty", "cost"}, ...]
        self._last_price: dict[str, Optional[float]] = {}
        self._sectors: dict[str, Optional[str]] = {}
        self._betas: dict[str, Optional[float]] = {}
        self._histories: dict[str, tuple] = {}  # sym -> (ts, closes)
        self._data_row_count = 0
        self._profiles_inited = False
        self._histories_inited = False

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_positions_tab(), "Positions")
        self.tabs.addTab(self._build_allocation_tab(), "Allocation")
        self.tabs.addTab(self._build_performance_tab(), "Performance")
        self.tabs.addTab(self._build_risk_tab(), "Risk")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.content_layout.addWidget(self.tabs, 1)

        self._rebuild_table()

    # -- tab construction --------------------------------------------------

    def _build_positions_tab(self) -> QWidget:
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(HEADERS), w)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_QTY, COL_COST, COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        lay.addWidget(self.table, 1)

        add_row = QHBoxLayout()
        self.symbol_edit = QLineEdit(w)
        self.symbol_edit.setPlaceholderText("Symbol")
        self.symbol_edit.setMaximumWidth(84)
        self.symbol_edit.returnPressed.connect(self._add_position)
        attach_suggestions(self.symbol_edit)
        self.qty_spin = QDoubleSpinBox(w)
        self.qty_spin.setRange(0.0001, 1e9)
        self.qty_spin.setDecimals(4)
        self.qty_spin.setValue(1.0)
        self.qty_spin.setToolTip("Quantity — shares/contracts held")
        self.cost_spin = QDoubleSpinBox(w)
        self.cost_spin.setRange(0.0, 1e7)
        self.cost_spin.setDecimals(4)
        self.cost_spin.setPrefix("$")
        self.cost_spin.setSpecialValueText("market")  # 0 => use current price
        self.cost_spin.setToolTip(
            "Price paid per unit. Leave at 'market' to use the current price."
        )
        self.date_edit = QDateEdit(w)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setToolTip("Purchase date — performance counts the position from here")
        add_btn = QPushButton("Add", w)
        add_btn.clicked.connect(self._add_position)
        sold_btn = QPushButton("Mark sold", w)
        sold_btn.setToolTip("Close the selected position as of today")
        sold_btn.clicked.connect(self._mark_sold)
        remove_btn = QPushButton("Remove", w)
        remove_btn.clicked.connect(self._remove_selected)
        for lbl, widget in (
            (None, self.symbol_edit), ("Qty", self.qty_spin),
            ("Price", self.cost_spin), ("Buy date", self.date_edit),
        ):
            if lbl:
                add_row.addWidget(QLabel(lbl, w))
            add_row.addWidget(widget)
        add_row.addStretch(1)
        add_row.addWidget(add_btn)
        add_row.addWidget(sold_btn)
        add_row.addWidget(remove_btn)
        lay.addLayout(add_row)
        return w

    def _build_allocation_tab(self) -> QWidget:
        """The bars label their own rows, so the colour legend that used to sit
        beside the pie has nothing left to explain and is gone."""
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self._alloc_bars = _AllocationBars(w)
        lay.addWidget(self._alloc_bars)
        lay.addStretch(1)
        return w

    def _build_performance_tab(self) -> QWidget:
        w = QWidget(self)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self._perf_plot = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self._perf_plot.showGrid(x=True, y=True, alpha=0.15)
        self._perf_plot.addLegend()
        self._perf_port = self._perf_plot.plot(
            [], [], pen=pg.mkPen(ACCENT, width=1.8), name="Portfolio"
        )
        self._perf_bench = self._perf_plot.plot(
            [], [], pen=pg.mkPen(FG_DIM, width=1.2, style=Qt.PenStyle.DashLine),
            name=_BENCH,
        )
        lay.addWidget(self._perf_plot, 1)
        self._perf_note = QLabel("Rebased to 100 at the window start.", w)
        self._perf_note.setObjectName("secondary")
        lay.addWidget(self._perf_note)
        return w

    def _build_risk_tab(self) -> QWidget:
        w = QWidget(self)
        grid = QGridLayout(w)
        grid.setColumnStretch(1, 1)
        self._risk_labels: dict[str, QLabel] = {}
        rows = [
            ("beta", "Portfolio beta (weighted)"),
            ("concentration", "Largest position"),
            ("drawdown", "Max drawdown (1y)"),
        ]
        for i, (key, title) in enumerate(rows):
            k = QLabel(title, w)
            k.setObjectName("secondary")
            v = QLabel("—", w)
            v.setStyleSheet(f"color: {FG}; font-weight: 700; font-size: {FONT_TITLE}px;")
            grid.addWidget(k, i, 0)
            grid.addWidget(v, i, 1)
            self._risk_labels[key] = v
        grid.setRowStretch(len(rows), 1)
        return w

    # -- lazy subscription on tab open -------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        title = self.tabs.tabText(index)
        if title in ("Allocation", "Risk"):
            self._ensure_profiles()
        if title in ("Performance", "Risk"):
            self._ensure_histories()
        self._refresh_analytics()

    def _held_symbols(self) -> list:
        seen = []
        for p in self._positions:
            if p["symbol"] not in seen:
                seen.append(p["symbol"])
        return seen

    def _ensure_profiles(self) -> None:
        if self._profiles_inited:
            return
        self._profiles_inited = True
        for sym in self._held_symbols():
            self.subscribe(f"profile:{sym}", lambda d, s=sym: self._on_profile(s, d))

    def _ensure_histories(self) -> None:
        if self._histories_inited:
            return
        self._histories_inited = True
        self.subscribe(
            f"history:{_BENCH}:{_HIST_TOPIC}", lambda d: self._on_history(_BENCH, d)
        )
        for sym in self._held_symbols():
            self.subscribe(
                f"history:{sym}:{_HIST_TOPIC}", lambda d, s=sym: self._on_history(s, d)
            )

    # -- table (re)construction --------------------------------------------

    def _rebuild_table(self) -> None:
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self._last_price.clear()
        self._data_row_count = len(self._positions)

        for pos in self._positions:
            self._append_position_row(pos)
        self._append_totals_row()

        for sym in self._held_symbols():
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))
        # re-arm any analytics subscriptions for the new symbol set
        if self._profiles_inited:
            self._profiles_inited = False
            self._ensure_profiles()
        if self._histories_inited:
            self._histories_inited = False
            self._ensure_histories()
        self._recompute_totals()
        self._refresh_analytics()

    def _append_position_row(self, pos: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        sold = pos.get("sell_date") is not None
        sym_item = QTableWidgetItem(pos["symbol"])
        sym_item.setFlags(sym_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, COL_SYMBOL, sym_item)

        qty_item = QTableWidgetItem(_fmt(pos["qty"], 4))
        cost = pos.get("cost")
        cost_item = QTableWidgetItem(_fmt(cost, 4) if cost is not None else "mkt")
        for item in (qty_item, cost_item):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, COL_QTY, qty_item)
        self.table.setItem(row, COL_COST, cost_item)

        date_text = pos.get("date") or NULL_GLYPH
        if sold:
            date_text = f"{date_text} → {pos.get('sell_date')}"
        date_item = QTableWidgetItem(date_text)
        date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if sold:
            date_item.setForeground(QColor(FG_DIM))
        self.table.setItem(row, COL_DATE, date_item)

        for col in (COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT):
            item = QTableWidgetItem(NULL_GLYPH)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)

    def _append_totals_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        label_item = QTableWidgetItem("TOTAL")
        label_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        font = label_item.font()
        font.setBold(True)
        label_item.setFont(font)
        label_item.setForeground(QColor(ACCENT))
        label_item.setBackground(QColor(BG_HEADER))
        self.table.setItem(row, COL_SYMBOL, label_item)
        for col in (COL_QTY, COL_COST, COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT):
            item = QTableWidgetItem(NULL_GLYPH)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            bold_font = item.font()
            bold_font.setBold(True)
            item.setFont(bold_font)
            item.setBackground(QColor(BG_HEADER))
            self.table.setItem(row, col, item)

    # -- data callbacks ----------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        price = data.get("price")
        self._last_price[symbol] = price
        # a position added "at market" gets its cost basis filled from the first
        # live price we see for it
        if price is not None:
            for pos in self._positions:
                if pos["symbol"] == symbol and pos.get("cost") is None:
                    pos["cost"] = float(price)
        for row, pos in enumerate(self._positions):
            if pos["symbol"] == symbol:
                self._update_position_row(row, pos)
        self._recompute_totals()
        self._refresh_analytics()

    def _on_profile(self, symbol: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._sectors[symbol] = data.get("sector")
        self._betas[symbol] = data.get("beta")
        self._refresh_analytics()

    def _on_history(self, symbol: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        t = data.get("t") or []
        c = data.get("c") or []
        n = min(len(t), len(c))
        ts: list = []
        cs: list = []
        for i in range(n):
            if t[i] is None or c[i] is None:
                continue
            ts.append(float(t[i]))
            cs.append(float(c[i]))
        if ts:
            self._histories[symbol] = (ts, cs)
            self._refresh_analytics()

    def _position_price(self, pos: dict):
        """Effective mark: the frozen sale price for a closed position, else the
        live quote."""
        if pos.get("sell_date") is not None:
            return pos.get("sell_price")
        return self._last_price.get(pos["symbol"])

    def _update_position_row(self, row: int, pos: dict) -> None:
        cost = pos.get("cost")
        qty = pos["qty"]
        last = self._position_price(pos)
        mkt_val = last * qty if last is not None else None
        pnl = (last - cost) * qty if (last is not None and cost is not None) else None
        pnl_pct = ((last - cost) / cost * 100.0) if (last is not None and cost) else None

        cost_item = self.table.item(row, COL_COST)
        if cost_item is not None:
            cost_item.setText(_fmt(cost, 4) if cost is not None else "mkt")
        last_item = self.table.item(row, COL_LAST)
        mktval_item = self.table.item(row, COL_MKTVAL)
        pnl_item = self.table.item(row, COL_PNL)
        pnlpct_item = self.table.item(row, COL_PNLPCT)
        if not (last_item and mktval_item and pnl_item and pnlpct_item):
            return
        last_item.setText(_fmt(last))
        mktval_item.setText(_fmt(mkt_val))
        pnl_item.setText(_fmt(pnl))
        pnlpct_item.setText(f"{_fmt(pnl_pct)}%" if pnl_pct is not None else NULL_GLYPH)
        if pnl is not None:
            apply_tick(pnl_item, pnl, glyph=False)
            apply_tick(pnlpct_item, pnl)

    def _recompute_totals(self) -> None:
        total_row = self.table.rowCount() - 1
        if total_row < 0 or total_row != self._data_row_count:
            return  # table mid-rebuild
        total_mkt = 0.0
        total_pnl = 0.0
        total_cost = 0.0
        any_data = False
        for pos in self._positions:
            last = self._position_price(pos)
            cost = pos.get("cost")
            if last is None or cost is None:
                continue
            any_data = True
            total_mkt += last * pos["qty"]
            total_pnl += (last - cost) * pos["qty"]
            total_cost += cost * pos["qty"]

        mktval_item = self.table.item(total_row, COL_MKTVAL)
        pnl_item = self.table.item(total_row, COL_PNL)
        pnlpct_item = self.table.item(total_row, COL_PNLPCT)
        if not (mktval_item and pnl_item and pnlpct_item):
            return
        if not any_data:
            mktval_item.setText(NULL_GLYPH)
            pnl_item.setText(NULL_GLYPH)
            pnlpct_item.setText(NULL_GLYPH)
            return
        mktval_item.setText(_fmt(total_mkt))
        pnl_item.setText(_fmt(total_pnl))
        pnl_pct = (total_pnl / total_cost * 100.0) if total_cost else None
        pnlpct_item.setText(f"{_fmt(pnl_pct)}%" if pnl_pct is not None else NULL_GLYPH)
        apply_tick(pnl_item, total_pnl, glyph=False)
        apply_tick(pnlpct_item, total_pnl)

    # -- analytics rendering ------------------------------------------------

    def _refresh_analytics(self) -> None:
        title = self.tabs.tabText(self.tabs.currentIndex())
        if title == "Allocation":
            self._render_allocation()
        elif title == "Performance":
            self._render_performance()
        elif title == "Risk":
            self._render_risk()

    def _current_positions(self) -> list:
        """Positions still open (not sold) — the book as it stands now."""
        return [p for p in self._positions if p.get("sell_date") is None]

    def _perf_positions(self) -> list:
        """Positions annotated with buy/sell timestamps for the value series."""
        return [
            {**p, "buy_ts": iso_to_ts(p.get("date")), "sell_ts": iso_to_ts(p.get("sell_date"))}
            for p in self._positions
        ]

    def _render_allocation(self) -> None:
        weights = sector_weights(self._current_positions(), self._last_price, self._sectors)
        # Sorting and the "Other" fold both live in the widget, so every caller
        # gets the same treatment.
        self._alloc_bars.set_rows(list(weights.items()))

    def _render_performance(self) -> None:
        ts, values = portfolio_series(self._perf_positions(), self._histories)
        if ts and values:
            self._perf_port.setData(ts, _normalize_to_100(values))
        else:
            self._perf_port.setData([], [])
        bench = self._histories.get(_BENCH)
        if bench and bench[0]:
            self._perf_bench.setData(bench[0], _normalize_to_100(bench[1]))
        else:
            self._perf_bench.setData([], [])

    def _render_risk(self) -> None:
        current = self._current_positions()
        beta = weighted_beta(current, self._last_price, self._betas)
        conc = concentration(current, self._last_price)
        _ts, values = portfolio_series(self._perf_positions(), self._histories)
        dd = max_drawdown(values) if values else None
        self._risk_labels["beta"].setText(f"{beta:.2f}" if beta is not None else "—")
        self._risk_labels["concentration"].setText(
            f"{conc:.1f}%" if conc is not None else "—"
        )
        self._risk_labels["drawdown"].setText(f"{dd:.1f}%" if dd is not None else "—")

    # -- add / remove ------------------------------------------------------

    def _push_positions_undo(self, label: str) -> None:
        snap = [dict(p) for p in self._positions]

        def _undo() -> None:
            self._positions = [dict(p) for p in snap]
            self._rebuild_table()
            self.set_status(f"undo · {label}")

        UndoStack.instance().push(label, _undo)

    def _add_position(self) -> None:
        symbol = self.symbol_edit.text().strip().upper()
        if not symbol:
            return
        self._push_positions_undo("add position")
        cost_val = self.cost_spin.value()
        self._positions.append({
            "symbol": symbol,
            "qty": self.qty_spin.value(),
            "cost": cost_val if cost_val > 0 else None,  # None => fill at market
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "sell_date": None,
            "sell_price": None,
        })
        self.symbol_edit.clear()
        self.cost_spin.setValue(0.0)
        self._rebuild_table()

    def _mark_sold(self) -> None:
        rows = sorted(
            {idx.row() for idx in self.table.selectedIndexes() if idx.row() < self._data_row_count}
        )
        rows = [r for r in rows if self._positions[r].get("sell_date") is None]
        if not rows:
            return
        self._push_positions_undo("mark sold")
        today = date.today().isoformat()
        for r in rows:
            pos = self._positions[r]
            pos["sell_date"] = today
            pos["sell_price"] = self._last_price.get(pos["symbol"])
        self._rebuild_table()

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        to_remove = [r for r in rows if r < self._data_row_count]
        if not to_remove:
            return
        self._push_positions_undo("remove position")
        for row in to_remove:
            del self._positions[row]
        self._rebuild_table()

    # -- selection -> navigation -------------------------------------------

    def _on_row_selected(self) -> None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        row = rows[0].row()
        if row >= self._data_row_count:
            return  # totals row
        item = self.table.item(row, COL_SYMBOL)
        if item is None:
            return
        self.set_symbol(item.text())

    # -- persistence -------------------------------------------------------

    def settings(self) -> dict:
        return {
            "positions": [
                [p["symbol"], p["qty"], p.get("cost"), p.get("date"),
                 p.get("sell_date"), p.get("sell_price")]
                for p in self._positions
            ]
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        positions = settings.get("positions")
        if not isinstance(positions, list):
            return

        def _num_or_none(v):
            return float(v) if isinstance(v, (int, float)) else None

        cleaned = []
        for entry in positions:
            # accepts the legacy 3-field [symbol, qty, cost] and the extended form
            if not (isinstance(entry, list) and len(entry) >= 3):
                continue
            symbol = str(entry[0]).strip().upper()
            try:
                qty = float(entry[1])
            except (TypeError, ValueError):
                continue
            if not symbol:
                continue
            cleaned.append({
                "symbol": symbol,
                "qty": qty,
                "cost": _num_or_none(entry[2]),
                "date": entry[3] if len(entry) > 3 and entry[3] else None,
                "sell_date": entry[4] if len(entry) > 4 and entry[4] else None,
                "sell_price": _num_or_none(entry[5]) if len(entry) > 5 else None,
            })
        self._positions = cleaned
        self._rebuild_table()
