"""Financials panel — Bloomberg FA style. Income / Balance / Cash Flow
statements, annual or quarterly, in a flipped table (line items as rows,
periods as columns)."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

import pandas as pd
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QToolButton,
    QTableWidgetItem,
)

from ..guide import GuideSection, PanelGuide
from .. import glossary
from ..components import MarketTable, make_filter_edit
from ..components.action_bar import ActionBar
from ..components.chip_bar import ChipBar
from ..panel import NULL_GLYPH, Panel, register_panel
from ..theme import DOWN, palette_colors
from ..components.responsive_row import ResponsiveRow

STATEMENTS = [
    ("income", "Income"),
    ("balance", "Balance"),
    ("cashflow", "Cash Flow"),
    ("debt", "Debt"),
]
PERIODS = [("annual", "Annual"), ("quarterly", "Quarterly")]

# Yahoo Finance's statement-specific URL slug, keyed by our internal ids.
_YF_SLUG = {
    "income": "financials",
    "balance": "balance-sheet",
    "cashflow": "cash-flow",
    "debt": "balance-sheet",
}

# Row value kinds: statement figures ("money"), percentages stored as
# fractions ("pct") and multiples ("ratio").
MONEY, PCT, RATIO = "money", "pct", "ratio"


def _xl(color: str) -> str:
    """A theme colour as openpyxl's RRGGBB (no leading #)."""
    return color.lstrip("#").upper()


# The exported workbook is a *document*, not a screenshot of the app: it gets
# opened in Excel, printed and emailed. So it takes the light palette's values
# regardless of which theme the app is running — a black-background spreadsheet
# is unreadable on paper and startling in someone else's inbox.
#
# What it must not do is what it did: freeze a copy of the *dark* theme's hex
# (1B2530 / 1A2129 / D7DDE3 / FFAB2E) and pair it with a *light* zebra stripe,
# which is neither theme and follows nothing. Pulling from
# ``palette_colors("light")`` keeps the amber identity, stays coherent, and
# moves with the palette instead of drifting from it.
_DOC = palette_colors("light")




def _fmt_compact(value: Any) -> str:
    """Human-format a financial-statement value: T/B/M suffixes, plain for
    small magnitudes, negatives keep their sign."""
    if value is None:
        return NULL_GLYPH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NULL_GLYPH
    if v != v:  # NaN (pandas turns None into NaN)
        return NULL_GLYPH
    sign = "-" if v < 0 else ""
    av = abs(v)
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if av >= div:
            return f"{sign}{av / div:.1f}{suffix}"
    if av >= 1e3:
        return f"{sign}{av:,.0f}"
    return f"{sign}{av:,.2f}"


def _fmt_kind(value: Any, kind: str) -> str:
    """Format a cell by its row kind — percentages and multiples read as
    such; everything else takes the compact statement format."""
    if kind == MONEY:
        return _fmt_compact(value)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NULL_GLYPH
    if v != v:
        return NULL_GLYPH
    if kind == PCT:
        return f"{v * 100:.2f}%"
    return f"{v:.2f}x"


# -- debt analysis -------------------------------------------------------------


def _stmt_lookup(data: dict, stmt: str, period: str) -> dict[str, dict[str, float]]:
    """{line item: {period column: value}} for one fetched statement."""
    block = (data.get(stmt) or {}).get(period) or {}
    columns = block.get("columns") or []
    out: dict[str, dict[str, float]] = {}
    for row in block.get("rows") or []:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        vals = {}
        for col, v in zip(columns, row[1:]):
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f:
                vals[str(col)] = f
        out[str(row[0])] = vals
    return out


def _pick(items: dict[str, dict[str, float]], col: str, *names: str) -> Optional[float]:
    """First line item among ``names`` that has a value for ``col`` —
    Yahoo's labels vary by filer, so each metric names its fallbacks."""
    for name in names:
        v = items.get(name, {}).get(col)
        if v is not None:
            return v
    return None


def _div(a: Optional[float], b: Optional[float], positive_den: bool = False) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    if positive_den and b < 0:
        return None
    return a / b


def debt_block(data: dict, period: str) -> dict:
    """Derive the Debt view from the fetched statements.

    Balance-sheet dates are the columns. Income and cash-flow figures are
    matched to them by period-end date; quarterly flows are annualized
    (×4) wherever they feed a rate or a multiple, so an annual and a
    quarterly column read on the same scale. The cost of debt uses
    average debt (this period and the prior one) when the prior period is
    in the fetch, and period-end debt otherwise.

    Returns ``{"columns", "rows", "kinds", "notes"}`` — ``rows`` in the
    same ``[label, *values]`` shape as a statement block.
    """
    bal = _stmt_lookup(data, "balance", period)
    inc = _stmt_lookup(data, "income", period)
    cf = _stmt_lookup(data, "cashflow", period)
    block = (data.get("balance") or {}).get(period) or {}
    columns = [str(c) for c in block.get("columns") or []]
    if not columns:
        return {"columns": [], "rows": [], "kinds": [], "notes": []}
    scale = 4.0 if period == "quarterly" else 1.0
    per = "annualized " if scale != 1.0 else ""

    def ann(v: Optional[float]) -> Optional[float]:
        return None if v is None else v * scale

    series: dict[str, list[Optional[float]]] = {}
    spec: list[tuple[str, str, str]] = []

    def add(label: str, kind: str, note: str, values: list[Optional[float]]) -> None:
        spec.append((label, kind, note))
        series[label] = values

    total_debt, st_debt, lt_debt, leases, cash, net_debt, equity = ([] for _ in range(7))
    interest, interest_paid, ebit, ebitda = ([] for _ in range(4))
    issued, repaid, net_issued = ([] for _ in range(3))
    for col in columns:
        st = _pick(bal, col, "Current Debt And Capital Lease Obligation", "Current Debt")
        lt = _pick(bal, col, "Long Term Debt And Capital Lease Obligation", "Long Term Debt")
        td = _pick(bal, col, "Total Debt")
        if td is None and (st is not None or lt is not None):
            td = (st or 0.0) + (lt or 0.0)
        c = _pick(
            bal, col,
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
        )
        total_debt.append(td)
        st_debt.append(st)
        lt_debt.append(lt)
        leases.append(_pick(bal, col, "Capital Lease Obligations"))
        cash.append(c)
        net_debt.append(None if td is None or c is None else td - c)
        equity.append(_pick(bal, col, "Stockholders Equity", "Common Stock Equity"))
        ie = _pick(inc, col, "Interest Expense", "Interest Expense Non Operating")
        interest.append(None if ie is None else abs(ie))
        ip = _pick(cf, col, "Interest Paid Supplemental Data")
        interest_paid.append(None if ip is None else abs(ip))
        ebit.append(_pick(inc, col, "EBIT", "Operating Income"))
        ebitda.append(_pick(inc, col, "EBITDA", "Normalized EBITDA"))
        issued.append(_pick(cf, col, "Issuance Of Debt", "Long Term Debt Issuance"))
        repaid.append(_pick(cf, col, "Repayment Of Debt", "Long Term Debt Payments"))
        net_issued.append(_pick(cf, col, "Net Issuance Payments Of Debt"))

    def avg_prior(values: list[Optional[float]], i: int) -> Optional[float]:
        # columns are newest-first, so the prior period is the next column
        cur = values[i]
        prev = values[i + 1] if i + 1 < len(values) else None
        if cur is None:
            return None
        return cur if prev is None else (cur + prev) / 2

    debt_ex_leases = [
        None if td is None else td - (ls or 0.0) for td, ls in zip(total_debt, leases)
    ]
    n = len(columns)
    cost = [_div(ann(interest[i]), avg_prior(total_debt, i), True) for i in range(n)]
    cost_ex = [_div(ann(interest[i]), avg_prior(debt_ex_leases, i), True) for i in range(n)]
    cost_cash = [_div(ann(interest_paid[i]), avg_prior(total_debt, i), True) for i in range(n)]

    add("Total Debt", MONEY, "Short-term + long-term debt, including leases", total_debt)
    add("Short-Term Debt", MONEY, "Debt and lease obligations due within a year", st_debt)
    add("Long-Term Debt", MONEY, "Debt and lease obligations due after a year", lt_debt)
    add("Lease Obligations", MONEY, "Capital/finance and operating lease liabilities (inside Total Debt)", leases)
    add("Cash & ST Investments", MONEY, "Cash, equivalents and short-term investments", cash)
    add("Net Debt", MONEY, "Total Debt − Cash & ST Investments (negative = net cash)", net_debt)
    add("Interest Expense", MONEY, "Reported interest expense for the period", interest)
    add("Interest Paid (Cash)", MONEY, "Cash interest paid, from the cash-flow supplemental data", interest_paid)
    add(
        "Implied Cost of Debt", PCT,
        f"ESTIMATE — {per}interest expense ÷ average Total Debt. The reported"
        " weighted-average rate is in the 10-K debt note (Browser ▸ Company filings)",
        cost,
    )
    add(
        "Implied Cost ex Leases", PCT,
        f"ESTIMATE — {per}interest expense ÷ average (Total Debt − leases);"
        " closer to the rate on borrowings when lease interest sits elsewhere",
        cost_ex,
    )
    add(
        "Implied Cost (Cash Basis)", PCT,
        f"ESTIMATE — {per}cash interest paid ÷ average Total Debt",
        cost_cash,
    )
    add("Interest Coverage (EBIT)", RATIO, "EBIT ÷ interest expense", [_div(e, i, True) for e, i in zip(ebit, interest)])
    add("EBITDA / Interest", RATIO, "EBITDA ÷ interest expense", [_div(e, i, True) for e, i in zip(ebitda, interest)])
    add(
        "Total Debt / EBITDA", RATIO,
        f"Total Debt ÷ {per}EBITDA (blank when EBITDA is negative)",
        [_div(d, ann(e), True) for d, e in zip(total_debt, ebitda)],
    )
    add(
        "Net Debt / EBITDA", RATIO,
        f"Net Debt ÷ {per}EBITDA (blank when EBITDA is negative)",
        [_div(d, ann(e), True) for d, e in zip(net_debt, ebitda)],
    )
    add("Debt / Equity", RATIO, "Total Debt ÷ stockholders' equity (blank when equity is negative)", [_div(d, e, True) for d, e in zip(total_debt, equity)])
    add(
        "Debt / Capital", PCT, "Total Debt ÷ (Total Debt + stockholders' equity)",
        [None if d is None or e is None else _div(d, d + e, True) for d, e in zip(total_debt, equity)],
    )
    add("Short-Term Share of Debt", PCT, "Short-Term Debt ÷ Total Debt — refinancing pressure", [_div(s, d, True) for s, d in zip(st_debt, total_debt)])
    add("Debt Issued", MONEY, "Cash raised from new borrowings in the period", issued)
    add("Debt Repaid", MONEY, "Cash used to repay borrowings in the period", repaid)
    add("Net Debt Issuance", MONEY, "Debt issued − debt repaid", net_issued)

    rows, kinds, notes = [], [], []
    for label, kind, note in spec:
        values = series[label]
        if all(v is None for v in values):
            continue  # this filer doesn't report it — don't show an empty row
        rows.append([label] + values)
        kinds.append(kind)
        notes.append(note)
    return {"columns": columns, "rows": rows, "kinds": kinds, "notes": notes}


@register_panel(id="fundamentals", title="Financials", category="Research", listed=False)
class FundamentalsPanel(Panel):
    GUIDE = PanelGuide(
        purpose="The company's income statement, balance sheet and cash flow, annual or quarterly, plus a derived debt view.",
        links="Follows the Company panel's symbol.",
        interactions=(
            "**Income / Balance / Cash Flow / Debt** pick the statement; **Annual / "
            "Quarterly** the period.",
            "Hover any line for a plain-language explanation; **Find line item…** filters.",
            "**Browser** opens the company's SEC filings or the statement on Yahoo "
            "Finance; **Download** saves a styled Excel file; **Copy** puts the table "
            "on the clipboard.",
            "In a narrow panel the pickers fold into dropdowns, the buttons into one "
            "⋯ menu, and the oldest periods drop first.",
        ),
        sections=(
            GuideSection("Debt", "A credit view derived from the statements: total, "
                "short- and long-term debt, leases, cash and net debt; interest expense "
                "and cash interest paid; the implied cost of debt; interest coverage, "
                "Debt/EBITDA, Net Debt/EBITDA, Debt/Equity and Debt/Capital; debt issued "
                "and repaid. Quarterly figures are annualized ×4 for rates and multiples. "
                "The implied rates are estimates; hover a line for its formula."),
        ),
    )

    def build(self) -> None:
        self._statement = "income"
        self._period = "annual"
        self._data: dict = {}

        # -- one control line: statement · period · search · actions -------
        # One line at any width. The statement and period pickers show as
        # chips when they fit and collapse to dropdowns when they don't; the
        # browser / download / copy actions live in one "⋯" menu. Three rows
        # of buttons above the table used to leave it a sliver in a narrow
        # dock.
        self._stmt_bar = ChipBar(self)
        for key, label in STATEMENTS:
            self._stmt_bar.setTabData(self._stmt_bar.addTab(label), key)
        self._stmt_bar.currentChanged.connect(
            lambda i: self._set_statement(self._stmt_bar.tabData(i))
        )
        self._period_bar = ChipBar(self)
        for key, label in PERIODS:
            self._period_bar.setTabData(self._period_bar.addTab(label), key)
        self._period_bar.currentChanged.connect(
            lambda i: self._set_period(self._period_bar.tabData(i))
        )

        # Browser ▾ · Download · Copy as buttons when there's room (a
        # maximized panel); folded into one "⋯" menu when there isn't
        self._actions = ActionBar(self)
        browser = self._actions.add_menu(
            "Browser", "Open the company's SEC filings (EDGAR) or this statement on Yahoo Finance"
        )
        self._edgar_act = browser.addAction("Company filings (SEC EDGAR)")
        self._edgar_act.triggered.connect(self._open_filings)
        self._yahoo_act = browser.addAction("Statement on Yahoo Finance")
        self._yahoo_act.triggered.connect(self._open_in_browser)
        self._browser_menu = browser
        self._actions.add_separator()
        self._download_act = self._actions.add_action(
            "Download", self._download, "Export the displayed table (CSV / Excel)",
            menu_text="Download (CSV / Excel)…",
        )
        self._copy_act = self._actions.add_action(
            "Copy", self._copy_to_clipboard, "Copy the displayed table to the clipboard",
            menu_text="Copy table",
        )

        # -- table -----------------------------------------------------------
        self.table = MarketTable(0, 1, self)
        self.register_state_target(self.table)
        self.table.viewport().installEventFilter(self)
        self.table.setHorizontalHeaderLabels(["Line Item"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        self._filter = make_filter_edit(self.table, "Find line item…")
        self._filter.setMinimumWidth(70)
        self._filter.setMaximumWidth(220)

        # pickers keep left, search and the menu keep right; the slack sits
        # between them rather than inside the picker groups
        controls = ResponsiveRow()
        controls.addWidget(self._stmt_bar)
        controls.addSpacing(6)
        controls.addWidget(self._period_bar)
        controls.addStretch(1)
        controls.addWidget(self._filter)
        controls.addWidget(self._actions)
        # pickers reach full size first, then search; the action buttons
        # only unfold from "⋯" once both have what they need
        controls.set_grow_priority(self._filter, 1)
        controls.set_grow_priority(self._actions, 2)
        self.content_layout.addLayout(controls)
        self._update_buttons()
        self._update_actions()
        self.content_layout.addWidget(self.table, 1)

    # -- statement / period toggles -------------------------------------------

    def _update_buttons(self) -> None:
        for bar, want in ((self._stmt_bar, self._statement), (self._period_bar, self._period)):
            bar.blockSignals(True)
            for i in range(bar.count()):
                if bar.tabData(i) == want:
                    bar.setCurrentIndex(i)
            bar.blockSignals(False)

    def _set_statement(self, key: str) -> None:
        if key == self._statement:
            return
        self._statement = key
        self._update_buttons()
        self._render()

    def _set_period(self, key: str) -> None:
        if key == self._period:
            return
        self._period = key
        self._update_buttons()
        self._render()

    # -- linked-symbol lifecycle ------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_loading(True)
        # Blanking the payload was never enough on its own — the table still
        # held the previous company's statement, rendered, under the new
        # ticker. Clear what is on screen, not just what is in memory.
        self._data = {}
        self.table.setRowCount(0)
        self.unsubscribe_all()
        self.subscribe(f"financials:{symbol}", self._on_financials)
        self._update_actions()

    def _on_financials(self, data: Any) -> None:
        # The fetch resolved — clear the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        self._data = data if isinstance(data, dict) else {}
        if self._needs_reason_refetch(self._data):
            # an empty result cached by a version that didn't record *why* it
            # was empty: fetch once more so the panel can explain itself
            self._hub.request([f"financials:{self.current_symbol}"], force=True)
        self._render()

    @staticmethod
    def _needs_reason_refetch(data: dict) -> bool:
        if not data or "quote_type" in data:
            return False
        return not any(
            ((data.get(k) or {}).get(f) or {}).get("rows")
            for k in ("income", "balance", "cashflow")
            for f in ("annual", "quarterly")
        )

    # -- period headers -----------------------------------------------------------

    @staticmethod
    def _short_period(label: str) -> str:
        """``2025-09-30`` → ``Sep '25``: the period end at a glance, in under
        half the width. Anything that isn't an ISO date is left as is."""
        try:
            d = datetime.strptime(label[:10], "%Y-%m-%d")
        except (TypeError, ValueError):
            return label
        return d.strftime("%b '%y")

    def _sync_header_labels(self) -> None:
        """Full period dates when they fit, ``Sep '25`` when they don't — a
        clipped "025-09-3" is neither date. The full date stays in the
        tooltip either way."""
        headers = getattr(self, "_full_headers", None)
        if not headers or len(headers) < 2:
            return
        periods = headers[1:]
        fm = self.table.horizontalHeader().fontMetrics()
        need = max(fm.horizontalAdvance(h) for h in periods) + 24
        per_col = (self.table.viewport().width() - 90) / len(periods)
        compact = per_col < need
        if compact == self._compact_headers:
            return
        self._compact_headers = compact
        for col, full in enumerate(periods, start=1):
            item = self.table.horizontalHeaderItem(col)
            if item is None:
                continue
            item.setText(self._short_period(full) if compact else full)
            item.setToolTip(full)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        # the table viewport's own resize: in the panel's resizeEvent the
        # viewport still had its old width, so the date/short-date choice
        # could be made for the wrong size
        if (
            hasattr(self, "table")
            and obj is self.table.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._sync_header_labels()
        return super().eventFilter(obj, event)

    # -- rendering -------------------------------------------------------------

    def _current_block(self) -> Optional[dict]:
        if self._statement == "debt":
            return debt_block(self._data, self._period) if self._data else None
        stmt = self._data.get(self._statement)
        if not isinstance(stmt, dict):
            return None
        block = stmt.get(self._period)
        return block if isinstance(block, dict) else None

    def _render(self) -> None:
        block = self._current_block()
        columns = block.get("columns") if block else None
        rows = block.get("rows") if block else None
        columns = columns if isinstance(columns, list) else []
        rows = rows if isinstance(rows, list) else []
        kinds = (block.get("kinds") if block else None) or []
        notes = (block.get("notes") if block else None) or []

        headers = ["Line Item"] + [str(c) for c in columns]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        # The line item takes the slack and the figures pack under their
        # period headers; a narrow panel gives up the oldest periods first.
        # Stretching every column split a wide panel into equal quarters, so
        # "Net Income From Continuing Operation…" elided beside half-empty
        # number columns.
        if len(headers) > 1:
            self.table.set_column_priority(
                keep=[0, 1], droppable=list(range(len(headers) - 1, 1, -1))
            )
        self._full_headers = headers
        self._compact_headers = None
        self._sync_header_labels()
        self.table.setRowCount(0)

        for idx, row_data in enumerate(rows):
            if not isinstance(row_data, (list, tuple)) or not row_data:
                continue
            label = row_data[0]
            values = list(row_data[1:])
            kind = kinds[idx] if idx < len(kinds) else MONEY
            r = self.table.rowCount()
            self.table.insertRow(r)
            label_item = QTableWidgetItem(str(label) if label is not None else NULL_GLYPH)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            note = notes[idx] if idx < len(notes) else None
            tip = glossary.tooltip(label_item.text(), note)
            if tip:
                label_item.setToolTip(tip)
            self.table.setItem(r, 0, label_item)
            for col in range(len(columns)):
                value = values[col] if col < len(values) else None
                item = QTableWidgetItem(_fmt_kind(value, kind))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                try:
                    if value is not None and float(value) < 0:
                        item.setForeground(QColor(DOWN))
                except (TypeError, ValueError):
                    pass
                self.table.setItem(r, col + 1, item)

        self.table.apply_filter(self._filter.text())
        if not rows:
            title, hint = self._empty_reason()
            self.table.set_empty_text(title, hint)

        sym = self.current_symbol or "—"
        stmt_label = dict(STATEMENTS).get(self._statement, self._statement)
        period_label = dict(PERIODS).get(self._period, self._period)
        status = f"{stmt_label} · {period_label} · {len(rows)} lines"
        if self._statement == "debt" and rows:
            status += " · rates are estimates — hover a line for its formula"
        self.set_status(status)
        self._update_actions()

    def _empty_reason(self) -> tuple[str, str]:
        """Why the table is empty, in words — never a silent blank panel."""
        sym = self.current_symbol
        if not sym:
            return "No symbol linked", "Type a company ticker (e.g. MSFT) in the SYMBOL box."
        if not self._data:
            return f"Loading {sym}…", ""
        qtype = str(self._data.get("quote_type") or "").upper()
        kinds = {
            "ETF": "an ETF",
            "MUTUALFUND": "a mutual fund",
            "INDEX": "an index",
            "FUTURE": "a futures contract",
            "CURRENCY": "a currency pair",
            "CRYPTOCURRENCY": "a cryptocurrency",
        }
        if qtype in kinds:
            return (
                f"{sym} is {kinds[qtype]} — it doesn't file financial statements",
                "Income statements, balance sheets and cash-flow statements exist only "
                "for companies. Link a company ticker (e.g. MSFT) to see them. "
                + ("Key Metrics shows this fund's yield, costs, returns and risk."
                   if qtype in ("ETF", "MUTUALFUND") else ""),
            )
        if self._statement == "debt":
            return (
                f"No debt data for {sym}",
                "The Debt view is derived from the balance sheet and income statement, "
                "and Yahoo Finance returned none for this symbol.",
            )
        stmt_label = dict(STATEMENTS).get(self._statement, self._statement).lower()
        period_label = dict(PERIODS).get(self._period, self._period).lower()
        return (
            f"No {period_label} {stmt_label} statement for {sym}",
            "Yahoo Finance (no API key needed) has no statement for this symbol — "
            "common for recent listings, SPACs and some foreign tickers. Try the "
            "other period, or Browser ▸ Company filings (SEC EDGAR) for the filings themselves.",
        )

    # -- statement actions: browser / download / copy -------------------------

    def _update_actions(self) -> None:
        """Enable/disable the browser/download/copy actions to match whether
        a symbol is linked and whether the displayed statement has rows."""
        has_symbol = bool(self.current_symbol)
        block = self._current_block()
        has_rows = bool(block and block.get("rows"))
        self._actions.set_menu_enabled(self._browser_menu, has_symbol)
        self._download_act.setEnabled(has_rows)
        self._copy_act.setEnabled(has_rows)

    def _open_in_browser(self) -> None:
        sym = self.current_symbol
        if not sym:
            return
        slug = _YF_SLUG.get(self._statement, "financials")
        webbrowser.open(f"https://finance.yahoo.com/quote/{sym}/{slug}")

    def _open_filings(self) -> None:
        """The company's actual filings on SEC EDGAR, pre-filtered to 10-K
        (annual) or 10-Q (quarterly). EDGAR resolves plain US tickers with
        dots swapped for dashes (BRK.B -> BRK-B); exchange-suffixed foreign
        listings won't match — the Yahoo action covers those."""
        sym = self.current_symbol
        if not sym:
            return
        cik = sym.replace(".", "-")
        form = "10-K" if self._period == "annual" else "10-Q"
        webbrowser.open(
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type={form}&dateb=&owner=include&count=40"
        )

    def _current_dataframe(self) -> Optional[pd.DataFrame]:
        """The currently displayed statement/period as a DataFrame, columns
        as periods, plus a leading "Line Item" column — same shape as the
        on-screen table."""
        block = self._current_block()
        if not block:
            return None
        columns = block.get("columns")
        rows = block.get("rows")
        columns = columns if isinstance(columns, list) else []
        rows = rows if isinstance(rows, list) else []
        if not rows:
            return None

        labels = []
        values_by_col: list[list] = [[] for _ in columns]
        for row_data in rows:
            if not isinstance(row_data, (list, tuple)) or not row_data:
                continue
            labels.append(row_data[0])
            values = list(row_data[1:])
            for col in range(len(columns)):
                values_by_col[col].append(values[col] if col < len(values) else None)

        df = pd.DataFrame({str(c): v for c, v in zip(columns, values_by_col)})
        df.insert(0, "Line Item", labels)
        return df

    def _default_filename(self, ext: str) -> str:
        sym = self.current_symbol or "symbol"
        return f"{sym}_{self._statement}_{self._period}{ext}"

    def export_to_path(self, path: str) -> bool:
        """Write the currently displayed table to ``path`` (.csv or .xlsx).
        Returns True on success; reports write errors via ``set_status``
        instead of raising. Factored out of ``_download`` so it can be
        exercised directly, without a file dialog."""
        df = self._current_dataframe()
        if df is None:
            self.set_status("⚠ nothing to export")
            return False
        try:
            if path.lower().endswith(".xlsx"):
                self._write_xlsx(path, df)
            else:
                # CSV carries no styling, so raw floats read terribly — write
                # the values exactly as the panel displays them (391.0B, 6.11).
                # Full-precision numbers live in the .xlsx export.
                self._display_frame(df).to_csv(path, index=False)
        except Exception as exc:
            self.set_status(f"⚠ {exc}")
            return False
        return True

    def _display_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Copy of the export frame with value columns rendered the way the
        on-screen table shows them (compact T/B/M figures)."""
        out = df.copy()
        kinds = self._row_kinds(len(out))
        for col in out.columns[1:]:
            out[col] = [_fmt_kind(v, k) for v, k in zip(out[col], kinds)]
        return out

    def _row_kinds(self, n: int) -> list[str]:
        """Per-row value kinds for the displayed block (all money for the
        raw statements)."""
        block = self._current_block() or {}
        kinds = list(block.get("kinds") or [])
        return (kinds + [MONEY] * n)[:n]

    def _write_xlsx(self, path: str, df: pd.DataFrame) -> None:
        """Styled Excel export: title bar, dark header band, thousands-
        separated figures with red negatives, frozen panes, sized columns."""
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        stmt_label = dict(STATEMENTS).get(self._statement, self._statement)
        period_label = dict(PERIODS).get(self._period, self._period)
        sym = self.current_symbol or "—"

        wb = Workbook()
        ws = wb.active
        ws.title = f"{sym} {stmt_label}"[:31]
        n_cols = len(df.columns)

        # row 1: title band across the full table
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
        title = ws.cell(row=1, column=1, value=f"{sym} — {stmt_label} ({period_label})")
        title.font = Font(bold=True, size=13, color=_xl(_DOC["ON_ACCENT"]))
        title.fill = PatternFill("solid", fgColor=_xl(_DOC["ACCENT"]))
        title.alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[1].height = 24

        # row 2: column headers (Line Item + one column per period)
        header_fill = PatternFill("solid", fgColor=_xl(_DOC["BG_HEADER"]))
        header_border = Border(bottom=Side(style="medium", color=_xl(_DOC["ACCENT"])))
        for col, name in enumerate(df.columns, start=1):
            cell = ws.cell(row=2, column=col, value=str(name))
            cell.font = Font(bold=True, size=10, color=_xl(_DOC["FG"]))
            cell.fill = header_fill
            cell.border = header_border
            cell.alignment = Alignment(
                horizontal="left" if col == 1 else "right", vertical="center"
            )
        ws.row_dimensions[2].height = 18

        # data: label column left, figures right with thousands separators;
        # small magnitudes (per-share items) keep decimals, negatives go red
        zebra = PatternFill("solid", fgColor=_xl(_DOC["BG_ALT"]))
        kinds = self._row_kinds(len(df))
        for r, row in enumerate(df.itertuples(index=False), start=3):
            kind = kinds[r - 3]
            for c, value in enumerate(row, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                if r % 2 == 1:
                    cell.fill = zebra
                if c == 1:
                    cell.font = Font(size=10)
                    continue
                cell.alignment = Alignment(horizontal="right")
                cell.font = Font(size=10)
                if isinstance(value, float) and value != value:
                    cell.value = None
                elif isinstance(value, (int, float)):
                    if kind == PCT:
                        fmt = "0.00%"
                    elif kind == RATIO:
                        fmt = '0.00"x"'
                    else:
                        fmt = "#,##0" if abs(value) >= 1000 else "#,##0.00"
                    cell.number_format = f"{fmt};[Red]({fmt})"

        label_width = max(
            [len(str(v)) for v in df.iloc[:, 0]] + [12]
        )
        ws.column_dimensions["A"].width = min(label_width + 2, 48)
        for col in range(2, n_cols + 1):
            ws.column_dimensions[get_column_letter(col)].width = 16
        ws.freeze_panes = "B3"
        wb.save(path)

    def _download(self) -> None:
        if self._current_dataframe() is None:
            return
        fn, _ = QFileDialog.getSaveFileName(
            self,
            "Export financials",
            self._default_filename(".csv"),
            "CSV (*.csv);;Excel (*.xlsx)",
        )
        if not fn:
            return
        if self.export_to_path(fn):
            self.set_status(f"exported {Path(fn).name}")

    def clipboard_text(self) -> str:
        """Tab-separated version of the currently displayed table."""
        df = self._current_dataframe()
        if df is None:
            return ""
        return df.to_csv(sep="\t", index=False)

    def _copy_to_clipboard(self) -> None:
        text = self.clipboard_text()
        if not text:
            self.set_status("⚠ nothing to copy")
            return
        QApplication.clipboard().setText(text)
        self.set_status("copied")

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"statement": self._statement, "period": self._period}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        stmt = settings.get("statement")
        if stmt in dict(STATEMENTS):
            self._statement = stmt
        period = settings.get("period")
        if period in dict(PERIODS):
            self._period = period
        self._update_buttons()
        self._render()
