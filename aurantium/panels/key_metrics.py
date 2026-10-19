"""Key Metrics panel — valuation, earnings, profitability, dividend and risk
ratios for the linked symbol, each set against its own five-year history.

Every row is clickable: it opens a history window charting the metric over
time with its average, ±1σ band and where today sits in the range. The window
follows the panel's linked symbol, so re-centering the desk on another name
re-draws the open chart too.

The numbers come from ``valuation.build_metrics`` via the ``metrics:<SYM>``
topic; the method notes there are shown as each row's tooltip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..guide import PanelGuide
from ..components import MarketTable, attach_hover, make_filter_edit
from ..components.chip_bar import ChipBar
from ..components.fmt import compact
from ..panel import NULL_GLYPH, Panel, register_panel
from ..theme import ACCENT, BG, BG_HEADER, FG_DIM, FG_MUTED, LABEL
from ..valuation import (
    CATALOGUE, DAYS, GROUPS, MONEY, NUM, PCT, RATIO, TEXT, history_rows, stats,
)
from ..components.responsive_row import ResponsiveRow

HEADERS = ["Metric", "Current", "5Y Avg", "5Y Low", "5Y High", "5Y %ile"]
COL_METRIC, COL_CUR, COL_AVG, COL_LOW, COL_HIGH, COL_PCT = range(6)

#: range buttons in the history window: (label, days; None = everything)
RANGES = [("1Y", 365), ("3Y", 3 * 365), ("5Y", 5 * 365), ("10Y", 10 * 365), ("Max", None)]

#: statement-cadence series have a handful of points — draw them as markers
_SPARSE = 16

_METRIC_ROLE = Qt.ItemDataRole.UserRole + 1

#: tab data for the user's own pick of metrics ("" = all groups)
CUSTOM = "__custom__"


def fmt_metric(value: Any, kind: str) -> str:
    if kind == TEXT:
        return str(value) if value else NULL_GLYPH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NULL_GLYPH
    if v != v:
        return NULL_GLYPH
    if kind == MONEY:
        return compact(v)
    if kind == PCT:
        return f"{v * 100:.2f}%"
    if kind == RATIO:
        return f"{v:.2f}x"
    if kind == DAYS:
        return f"{v:,.1f} d"
    return f"{v:,.2f}"


def _differs(a: Any, b: Any, tol: float = 0.02) -> bool:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) > tol * max(abs(a), abs(b), 1e-12)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


class MetricHistoryDialog(QDialog):
    """A metric's history: chart, range picker, statistics and method note.

    Non-modal, one per panel. ``show_metric`` swaps what it shows, so the
    panel can re-point it when its linked symbol changes.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(760, 460)
        self._symbol = ""
        self._metric: dict = {}
        self._days: Optional[int] = 5 * 365

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        top = ResponsiveRow()
        self.title_lbl = QLabel("", self)
        self.title_lbl.setObjectName("statValue")
        top.addWidget(self.title_lbl)
        top.addStretch(1)
        self._range_group = QButtonGroup(self)
        for i, (label, days) in enumerate(RANGES):
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setChecked(days == self._days)
            # the app's one selector style, sized by its label: a fixed 40px
            # clipped "10Y" and "Max"
            btn.setObjectName("chartChip")
            self._range_group.addButton(btn, i)
            top.addWidget(btn)
        self._range_group.idClicked.connect(self._on_range)
        root.addLayout(top)

        self.stats_lbl = QLabel("", self)
        self.stats_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.stats_lbl)

        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.plot.setBackground(BG)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.getAxis("bottom").setTextPen(FG_DIM)
        self.plot.getAxis("left").setTextPen(FG_DIM)
        self.plot.setMenuEnabled(False)
        self.series = pg.PlotDataItem(pen=pg.mkPen(ACCENT, width=2))
        self.plot.addItem(self.series)
        self.avg_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(FG_DIM, style=Qt.PenStyle.DashLine))
        self.hi_band = pg.InfiniteLine(angle=0, pen=pg.mkPen(FG_MUTED, style=Qt.PenStyle.DotLine))
        self.lo_band = pg.InfiniteLine(angle=0, pen=pg.mkPen(FG_MUTED, style=Qt.PenStyle.DotLine))
        for line in (self.avg_line, self.hi_band, self.lo_band):
            self.plot.addItem(line, ignoreBounds=True)
        self.now_marker = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(ACCENT), pen=pg.mkPen(ACCENT))
        self.plot.addItem(self.now_marker)
        attach_hover(self.plot, self._hover_text)
        root.addWidget(self.plot, 1)

        self.empty_lbl = QLabel("", self)
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setObjectName("secondary")
        self.empty_lbl.setWordWrap(True)
        root.addWidget(self.empty_lbl, 1)

        self.legend_lbl = QLabel(
            "Dashed: average over the range · dotted: ±1 standard deviation", self
        )
        self.legend_lbl.setObjectName("secondary")
        root.addWidget(self.legend_lbl)

        self.note_lbl = QLabel("", self)
        self.note_lbl.setObjectName("secondary")
        self.note_lbl.setWordWrap(True)
        root.addWidget(self.note_lbl)

        bottom = ResponsiveRow()
        bottom.addStretch(1)
        self.copy_btn = QPushButton("Copy", self)
        self.copy_btn.setToolTip("Copy the history (date, value) to the clipboard")
        self.copy_btn.clicked.connect(self._copy)
        bottom.addWidget(self.copy_btn)
        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._xs: list[float] = []
        self._ys: list[float] = []

    # -- content ----------------------------------------------------------

    @property
    def metric_id(self) -> str:
        return self._metric.get("id", "")

    def show_metric(self, symbol: str, metric: dict) -> None:
        self._symbol = symbol
        self._metric = metric or {}
        label = self._metric.get("label", "")
        self.setWindowTitle(f"{symbol} · {label} — history")
        self.title_lbl.setText(f"{symbol}  ·  {label}")
        note = self._metric.get("note", "")
        quote = self._metric.get("quote")
        if _differs(self._metric.get("value"), quote):
            note += f"  Yahoo quotes {fmt_metric(quote, self._metric.get('kind', NUM))} on its own basis."
        self.note_lbl.setText(note)
        self._redraw()

    def show_unavailable(self, symbol: str, label: str) -> None:
        """The linked symbol changed to one without this metric."""
        self._symbol = symbol
        self._metric = {"id": self.metric_id, "label": label, "history": {"t": [], "v": []}}
        self.setWindowTitle(f"{symbol} · {label} — history")
        self.title_lbl.setText(f"{symbol}  ·  {label}")
        self.note_lbl.setText("")
        self._redraw()

    def _on_range(self, idx: int) -> None:
        self._days = RANGES[idx][1]
        self._redraw()

    def _window(self) -> tuple[list[float], list[float]]:
        hist = self._metric.get("history") or {}
        t, v = list(hist.get("t") or []), list(hist.get("v") or [])
        if not t or self._days is None:
            return t, v
        cutoff = t[-1] - self._days * 86400
        keep = [i for i, ts in enumerate(t) if ts >= cutoff]
        # sparse (statement) series: keep the point just before the window so
        # the line enters from the left edge instead of starting mid-chart
        if keep and keep[0] > 0 and len(t) <= _SPARSE:
            keep.insert(0, keep[0] - 1)
        return [t[i] for i in keep], [v[i] for i in keep]

    def _redraw(self) -> None:
        kind = self._metric.get("kind", NUM)
        xs, ys = self._window()
        self._xs, self._ys = xs, ys
        has = len(xs) >= 2
        self.plot.setVisible(has)
        self.legend_lbl.setVisible(has)
        self.empty_lbl.setVisible(not has)
        self.copy_btn.setEnabled(bool(self._metric.get("history", {}).get("t")))
        if not has:
            value = self._metric.get("value")
            self.stats_lbl.setText(
                f"Current  {fmt_metric(value, kind)}" if value is not None else ""
            )
            self.empty_lbl.setText(
                "No history for this metric on this symbol — Yahoo doesn't publish"
                " the inputs needed to rebuild it."
            )
            return

        sparse = len(self._metric["history"]["t"]) <= _SPARSE
        scale = 100.0 if kind == PCT else 1.0
        # money axes read in the unit of the largest value (e.g. billions)
        unit, div = "", 1.0
        if kind == MONEY:
            peak = max(abs(y) for y in ys)
            for suffix, d in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
                if peak >= d:
                    unit, div = suffix, d
                    break
            scale = 1.0 / div
        plot_ys = [y * scale for y in ys]
        self.series.setData(
            xs, plot_ys,
            symbol="o" if sparse else None, symbolSize=6,
            symbolBrush=pg.mkBrush(ACCENT), symbolPen=pg.mkPen(ACCENT),
        )
        self.now_marker.setData([xs[-1]], [plot_ys[-1]])
        axis = {PCT: "%", RATIO: "x", MONEY: unit, DAYS: "days"}.get(kind, "")
        self.plot.setLabel("left", axis)

        st = stats(xs, ys, days=10**6)
        if st:
            self.avg_line.setPos(st["avg"] * scale)
            self.hi_band.setPos((st["avg"] + st["std"]) * scale)
            self.lo_band.setPos((st["avg"] - st["std"]) * scale)
            f = lambda x: fmt_metric(x, kind)  # noqa: E731
            self.stats_lbl.setText(
                f"Current <b style='color:{ACCENT}'>{f(ys[-1])}</b>"
                f"   ·   Avg {f(st['avg'])}   ·   Low {f(st['low'])}   ·   High {f(st['high'])}"
                f"   ·   σ {f(st['std'])}   ·   Percentile {st['pctile'] * 100:.0f}%"
                f"   <span style='color:{FG_DIM}'>({_iso(xs[0])} → {_iso(xs[-1])},"
                f" {len(xs)} points)</span>"
            )
        pad_y = (max(plot_ys) - min(plot_ys)) * 0.08 or 1.0
        pad_x = (xs[-1] - xs[0]) * 0.02 or 86400
        self.plot.setXRange(xs[0] - pad_x, xs[-1] + pad_x, padding=0)
        self.plot.setYRange(min(plot_ys) - pad_y, max(plot_ys) + pad_y, padding=0)

    def _hover_text(self, x: float, _y: float) -> Optional[str]:
        if not self._xs:
            return None
        i = min(range(len(self._xs)), key=lambda k: abs(self._xs[k] - x))
        return f"{_iso(self._xs[i])}  {fmt_metric(self._ys[i], self._metric.get('kind', NUM))}"

    def _copy(self) -> None:
        label = self._metric.get("label", "value")
        lines = [f"Date\t{self._symbol} {label}"]
        lines += [f"{d}\t{v}" for d, v in history_rows(self._metric)]
        QApplication.clipboard().setText("\n".join(lines) + "\n")


class CustomMetricsDialog(QDialog):
    """Tick the metrics the Custom tab shows, from the full catalogue.

    Already-chosen metrics keep their order; newly ticked ones are appended
    in catalogue order.
    """

    def __init__(self, chosen: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowTitle("Custom key metrics")
        self.resize(420, 560)
        self._initial = list(chosen)
        picked = set(chosen)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)
        hint = QLabel("Tick the metrics to show on the Custom tab.", self)
        hint.setObjectName("secondary")
        root.addWidget(hint)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find metric…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self._items: dict[str, QTreeWidgetItem] = {}
        groups: dict[str, QTreeWidgetItem] = {}
        for g in GROUPS:
            top = QTreeWidgetItem(self.tree, [g])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            top.setForeground(0, QColor(LABEL))
            groups[g] = top
        for mid, label, group, _kind, note in CATALOGUE:
            parent_item = groups.get(group)
            if parent_item is None:
                continue
            item = QTreeWidgetItem(parent_item, [label])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if mid in picked else Qt.CheckState.Unchecked)
            item.setToolTip(0, note)
            item.setData(0, _METRIC_ROLE, mid)
            self._items[mid] = item
        self.tree.expandAll()
        root.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        clear = buttons.addButton("Clear all", QDialogButtonBox.ButtonRole.ResetRole)
        clear.clicked.connect(self._clear)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            any_shown = False
            for j in range(top.childCount()):
                child = top.child(j)
                hide = bool(needle) and needle not in child.text(0).lower()
                child.setHidden(hide)
                any_shown |= not hide
            top.setHidden(not any_shown)

    def _clear(self) -> None:
        for item in self._items.values():
            item.setCheckState(0, Qt.CheckState.Unchecked)

    def chosen(self) -> list[str]:
        ticked = {mid for mid, it in self._items.items() if it.checkState(0) == Qt.CheckState.Checked}
        kept = [mid for mid in self._initial if mid in ticked]
        return kept + [mid for mid in self._items if mid in ticked and mid not in kept]


@register_panel(id="key_metrics", title="Key Metrics", category="Research", listed=False)
class KeyMetricsPanel(Panel):
    GUIDE = PanelGuide(
        purpose="Valuation, growth, profitability, balance-sheet, dividend and risk ratios, each set against its own five years.",
        links="Follows the Company panel's symbol; an open history chart redraws for a new symbol.",
        interactions=(
            "The chips across the top jump to one group; **All** shows every metric.",
            "**Click a row** for its history chart, with a range picker (1Y to Max), "
            "average and ±1σ lines, and **Copy**.",
            "**Custom** holds your own pick: right-click a row ▸ **Add to Custom**, or "
            "open Custom and press **Edit…**.",
            "Hover a metric's name to see how it is calculated.",
        ),
        fields=(
            ("TTM / NTM", "Trailing twelve months of results / the next twelve months' estimates"),
            ("5Y Avg / Low / High", "The metric's own five-year average and range"),
            ("5Y %ile", "Where today sits in that range: 100% is its highest"),
        ),
    )

    def build(self) -> None:
        self._data: dict = {}
        self._dialog: Optional[MetricHistoryDialog] = None
        self._group = ""  # "" = all groups, CUSTOM = the user's pick, else a group
        self._custom: list[str] = []  # metric ids on the Custom tab, in order

        self.table = MarketTable(0, len(HEADERS), self)
        self.register_state_target(self.table)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.set_column_priority(
            keep=[COL_METRIC, COL_CUR], droppable=[COL_PCT, COL_LOW, COL_HIGH, COL_AVG]
        )
        self.table.enable_column_menu()
        self.table.set_empty_text("No symbol linked", "Pick a stock to see its key metrics")
        self.table.set_row_actions(self._row_actions)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setMouseTracking(True)

        # one chip per group: jump straight to a section instead of scrolling.
        # One line, always — chips when they fit, a dropdown when they don't —
        # sharing it with the search box, so the table keeps the height.
        self.tabs = ChipBar(self)
        self.tabs.currentChanged.connect(self._on_tab)
        self._sync_tabs()

        self._filter = make_filter_edit(self.table, "Find metric…")
        self._filter.setMinimumWidth(70)
        self._filter.setMaximumWidth(240)
        self.edit_btn = QPushButton("Edit…", self)
        self.edit_btn.setToolTip("Choose the metrics on the Custom tab")
        self.edit_btn.clicked.connect(self.edit_custom)
        self.edit_btn.setVisible(False)
        top = ResponsiveRow()
        top.addWidget(self.tabs)
        top.addStretch(1)
        top.addWidget(self._filter)
        top.addWidget(self.edit_btn)
        self.content_layout.addLayout(top)

        # one line of context when the symbol isn't an operating company
        self.notice_lbl = QLabel("", self)
        self.notice_lbl.setObjectName("secondary")
        self.notice_lbl.setWordWrap(True)
        self.notice_lbl.setVisible(False)
        self.content_layout.addWidget(self.notice_lbl)
        self.content_layout.addWidget(self.table, 1)

    # -- linked symbol ------------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_loading(True)
        self._data = {}
        self.table.setRowCount(0)
        self.table.set_empty_text(f"No key metrics for {symbol}", "")
        self.unsubscribe_all()
        self.subscribe(f"metrics:{symbol}", self._on_metrics)

    def _on_metrics(self, data: Any) -> None:
        self.set_loading(False)
        self._data = data if isinstance(data, dict) else {}
        self._sync_tabs()
        self._render()
        self._sync_dialog()

    def _sync_tabs(self) -> None:
        """All · the groups this symbol has rows in · Custom, keeping the
        choice. A group the new symbol lacks falls back to All for now but is
        remembered, so it comes back with the next symbol that has it."""
        present = {m.get("group") for m in self._metrics() if self._shown(m)}
        groups = [g for g in (self._data.get("groups") or []) if g in present]
        tabs = [("All", "")] + [(g, g) for g in groups] + [("Custom", CUSTOM)]
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for label, data in tabs:
            i = self.tabs.addTab(label)
            self.tabs.setTabData(i, data)
        self.tabs.setTabToolTip(self.tabs.count() - 1, "Your own pick of metrics")
        datas = [d for _l, d in tabs]
        self.tabs.setCurrentIndex(datas.index(self._group) if self._group in datas else 0)
        self.tabs.blockSignals(False)

    def _on_tab(self, i: int) -> None:
        self._group = self.tabs.tabData(i) or ""
        self._render()

    def show_group(self, group: str) -> None:
        """Open one group's tab ("" = All, ``CUSTOM`` = the user's pick).
        A group this symbol has no rows in is remembered and shown once a
        symbol that has it arrives."""
        self._group = group
        self._select_tab(group)
        if self._data:
            self._render()

    def _select_tab(self, data: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == data:
                self.tabs.setCurrentIndex(i)
                return

    # -- custom tab ---------------------------------------------------------

    def _row_actions(self, row: int) -> list:
        item = self.table.item(row, COL_METRIC) if row >= 0 else None
        mid = item.data(_METRIC_ROLE) if item is not None else None
        actions = []
        if mid:
            if mid in self._custom:
                actions.append(("Remove from Custom", lambda: self.remove_custom(mid)))
            else:
                actions.append(("Add to Custom", lambda: self.add_custom(mid)))
        actions.append(("Edit Custom Metrics…", self.edit_custom))
        return actions

    def add_custom(self, mid: str) -> None:
        if mid not in self._custom:
            self._custom.append(mid)
            self._render()

    def remove_custom(self, mid: str) -> None:
        if mid in self._custom:
            self._custom.remove(mid)
            self._render()

    def edit_custom(self) -> None:
        dlg = CustomMetricsDialog(self._custom, self.window())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._custom = dlg.chosen()
            self._select_tab(CUSTOM)
            self._render()

    @staticmethod
    def _shown(m: dict) -> bool:
        has_hist = len((m.get("history") or {}).get("t") or []) >= 2
        return m.get("value") is not None or has_hist

    def _metrics(self) -> list[dict]:
        rows = self._data.get("metrics")
        return [m for m in rows if isinstance(m, dict)] if isinstance(rows, list) else []

    # -- table ------------------------------------------------------------

    def _render(self) -> None:
        self.table.setRowCount(0)
        # the tab actually showing — a remembered group this symbol lacks
        # leaves All selected while ``_group`` keeps the preference
        current = self.tabs.tabData(self.tabs.currentIndex()) or ""
        custom = current == CUSTOM
        self.edit_btn.setVisible(custom)
        sym = self._data.get("symbol") or self.current_symbol
        shown = 0
        if custom:
            # the user's own pick, in their order. Rows the payload has but
            # can't fill still show (as dashes) — they were asked for by name.
            by_id = {m.get("id"): m for m in self._metrics()}
            for mid in self._custom:
                m = by_id.get(mid)
                if m is None:
                    continue  # not offered for this kind of symbol (e.g. Fund rows on a stock)
                self._append_metric(m, len((m.get("history") or {}).get("t") or []) >= 2)
                shown += 1
            if self._custom:
                self.table.set_empty_text(
                    "None of your custom metrics apply here", f"{sym} doesn't report them"
                )
            else:
                self.table.set_empty_text(
                    "No custom metrics yet",
                    "Right-click a metric ▸ Add to Custom, or press Edit…",
                )
        else:
            group = None
            for m in self._metrics():
                has_hist = len((m.get("history") or {}).get("t") or []) >= 2
                if not self._shown(m):
                    continue  # not reported for this symbol (e.g. margins on an ETF)
                if current and m.get("group") != current:
                    continue
                if m.get("group") != group:
                    group = m.get("group")
                    self._append_header(str(group))
                self._append_metric(m, has_hist)
                shown += 1
            if sym:
                self.table.set_empty_text(f"No key metrics for {sym}", "")
        self.table.apply_filter(self._filter.text())
        cur = self._data.get("currency")
        qtype = (self._data.get("quote_type") or "").upper()
        notice = {
            "ETF": f"{sym} is an ETF. Funds don't file company financial statements, so "
                   "earnings, margin and balance-sheet metrics don't apply. Showing fund, "
                   "dividend, performance and risk metrics.",
            "MUTUALFUND": f"{sym} is a mutual fund. Company financial metrics don't apply; "
                          "showing fund, dividend, performance and risk metrics.",
            "INDEX": f"{sym} is an index — only price-based metrics apply.",
            "FUTURE": f"{sym} is a futures contract — only price-based metrics apply.",
            "CURRENCY": f"{sym} is a currency pair — only price-based metrics apply.",
            "CRYPTOCURRENCY": f"{sym} is a cryptocurrency — only price-based metrics apply.",
        }.get(qtype, "")
        self.notice_lbl.setText(notice)
        self.notice_lbl.setVisible(bool(notice))
        # (the header strip already shows the symbol)
        self.set_status(
            f"{shown} metrics" + (f" · {cur}" if cur else "") + " · click a row for its history"
        )

    def _append_header(self, text: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setForeground(QColor(LABEL))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setBackground(QColor(BG_HEADER))
        self.table.setItem(r, 0, item)
        self.table.setSpan(r, 0, 1, len(HEADERS))

    def _append_metric(self, m: dict, has_hist: bool) -> None:
        kind = m.get("kind", NUM)
        st = m.get("stats") or {}
        r = self.table.rowCount()
        self.table.insertRow(r)

        name = QTableWidgetItem(f"  {m.get('label', '')}")
        name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        name.setData(_METRIC_ROLE, m.get("id"))
        tip = m.get("note", "")
        tip += "\n\nClick for its history." if has_hist else "\n\nNo history available."
        name.setToolTip(tip)
        self.table.setItem(r, COL_METRIC, name)

        def cell(col: int, text: str, color: Optional[str] = None, tooltip: str = "") -> None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setData(_METRIC_ROLE, m.get("id"))
            if color:
                item.setForeground(QColor(color))
            if tooltip:
                item.setToolTip(tooltip)
            self.table.setItem(r, col, item)

        value, quote = m.get("value"), m.get("quote")
        cur_tip = ""
        if _differs(value, quote):
            cur_tip = f"Yahoo quotes {fmt_metric(quote, kind)} — see the method note on the metric name."
        cell(COL_CUR, fmt_metric(value, kind), LABEL, cur_tip)
        if kind == TEXT:
            for col in (COL_AVG, COL_LOW, COL_HIGH, COL_PCT):
                cell(col, "")
            return
        cell(COL_AVG, fmt_metric(st.get("avg"), kind))
        cell(COL_LOW, fmt_metric(st.get("low"), kind), FG_DIM)
        cell(COL_HIGH, fmt_metric(st.get("high"), kind), FG_DIM)
        pctile = st.get("pctile")
        span = st.get("span_days")
        pct_tip = ""
        if pctile is not None:
            pct_tip = (
                f"Today's value is higher than {pctile * 100:.0f}% of readings"
                f" over the last {span / 365:.1f} years."
            )
        cell(COL_PCT, NULL_GLYPH if pctile is None else f"{pctile * 100:.0f}%", None, pct_tip)

    # -- history window -----------------------------------------------------

    def _on_cell_clicked(self, row: int, col: int) -> None:
        item = self.table.item(row, col) or self.table.item(row, COL_METRIC)
        mid = item.data(_METRIC_ROLE) if item is not None else None
        if not mid:
            return  # a group header
        metric = next((m for m in self._metrics() if m.get("id") == mid), None)
        if metric is None:
            return
        self.open_history(metric)

    def open_history(self, metric: dict) -> MetricHistoryDialog:
        if self._dialog is None:
            self._dialog = MetricHistoryDialog(self.window())
        sym = self._data.get("symbol") or self.current_symbol
        self._dialog.show_metric(sym, metric)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
        return self._dialog

    def _sync_dialog(self) -> None:
        """Keep an open history window on the panel's current symbol."""
        dlg = self._dialog
        if dlg is None or not dlg.isVisible() or not dlg.metric_id:
            return
        sym = self._data.get("symbol") or self.current_symbol
        metric = next((m for m in self._metrics() if m.get("id") == dlg.metric_id), None)
        if metric is None:
            dlg.show_unavailable(sym, dlg._metric.get("label", ""))
        else:
            dlg.show_metric(sym, metric)

    # -- persistence ---------------------------------------------------------

    def settings(self) -> dict:
        return {"group": self._group, "custom": list(self._custom)}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        if isinstance(settings.get("custom"), list):
            known = {m[0] for m in CATALOGUE}
            self._custom = [m for m in settings["custom"] if isinstance(m, str) and m in known]
        if isinstance(settings.get("group"), str):
            self._group = settings["group"]
        self._sync_tabs()
        self.edit_btn.setVisible(self._group == CUSTOM)
        if self._data:
            self._render()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._dialog is not None:
            self._dialog.close()
        super().closeEvent(event)
