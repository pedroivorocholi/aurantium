"""Options Chain panel — Bloomberg OMON-lite: expiry picker + spot label
above two side-by-side read-only tables (calls / puts). The ATM strike row
(closest to spot) is highlighted; ITM rows get a subtle tint.

Greeks (Δ delta, Γ gamma, Θ theta/day, Vega per 1% vol) are computed on the fly
via Black–Scholes (pure Python — no scipy) from spot, strike, implied vol and
time to expiry. The Greek columns are hidden by default; right-click a table
header (Columns) to show them, and the choice persists with the layout.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
)

from ..components import MarketTable
from ..panel import Panel, register_panel
from ..theme import BG_HEADER

# calls: Vol | OI | IV% | Δ | Γ | Θ | Vega | Bid | Ask | Last | Strike
CALL_HEADERS = ["Vol", "OI", "IV%", "Δ", "Γ", "Θ", "Vega", "Bid", "Ask", "Last", "Strike"]
(CALL_COL_VOL, CALL_COL_OI, CALL_COL_IV, CALL_COL_DELTA, CALL_COL_GAMMA,
 CALL_COL_THETA, CALL_COL_VEGA, CALL_COL_BID, CALL_COL_ASK, CALL_COL_LAST,
 CALL_COL_STRIKE) = range(11)
CALL_GREEK_COLS = (CALL_COL_DELTA, CALL_COL_GAMMA, CALL_COL_THETA, CALL_COL_VEGA)

# puts: Strike | Last | Bid | Ask | Δ | Γ | Θ | Vega | IV% | OI | Vol
PUT_HEADERS = ["Strike", "Last", "Bid", "Ask", "Δ", "Γ", "Θ", "Vega", "IV%", "OI", "Vol"]
(PUT_COL_STRIKE, PUT_COL_LAST, PUT_COL_BID, PUT_COL_ASK, PUT_COL_DELTA,
 PUT_COL_GAMMA, PUT_COL_THETA, PUT_COL_VEGA, PUT_COL_IV, PUT_COL_OI,
 PUT_COL_VOL) = range(11)
PUT_GREEK_COLS = (PUT_COL_DELTA, PUT_COL_GAMMA, PUT_COL_THETA, PUT_COL_VEGA)

# payload row layout: [strike, last, bid, ask, volume, open_interest, iv_pct]
ROW_STRIKE, ROW_LAST, ROW_BID, ROW_ASK, ROW_VOLUME, ROW_OI, ROW_IV = range(7)

_ITM_TINT_CALL = "#1c2b22"   # very subtle green tint
_ITM_TINT_PUT = "#2b1c1c"    # very subtle red tint

_RISK_FREE = 0.04  # flat short-rate assumption for the Greeks

# plain-language explanations shown on header hover
HEADER_TIPS = {
    "Δ": "Delta — how much the option price moves per $1 move in the underlying",
    "Γ": "Gamma — how much delta itself moves per $1 move in the underlying",
    "Θ": "Theta — option value lost to time decay, per day",
    "Vega": "Vega — option price change per 1 percentage-point change in implied volatility",
    "IV%": "Implied volatility — the market's expected annualized volatility (%)",
    "OI": "Open interest — number of contracts currently outstanding",
    "Vol": "Volume — contracts traded so far today",
    "Bid": "Highest price a buyer is currently offering",
    "Ask": "Lowest price a seller is currently asking",
    "Strike": "Strike price — the price at which the option can be exercised",
}


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _row_field(row: Any, idx: int) -> Any:
    if not isinstance(row, (list, tuple)) or idx >= len(row):
        return None
    return row[idx]


def years_to_expiry(expiry: Any, today: date | None = None) -> float | None:
    """Fractional years from today to an ``YYYY-MM-DD`` expiry, or None if the
    date is unparseable or not in the future (Greeks are unstable at/after
    expiry)."""
    try:
        y, m, d = (int(p) for p in str(expiry).split("-")[:3])
        exp = date(y, m, d)
    except (TypeError, ValueError):
        return None
    days = (exp - (today or date.today())).days
    return days / 365.0 if days > 0 else None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_greeks(
    spot: Any, strike: Any, iv_pct: Any, t_years: Any, is_call: bool,
    r: float = _RISK_FREE,
) -> tuple:
    """Black–Scholes (delta, gamma, theta_per_day, vega_per_1pct) for one
    option, or a 4-tuple of None when inputs are missing/degenerate."""
    none4 = (None, None, None, None)
    try:
        s = float(spot); k = float(strike); sigma = float(iv_pct) / 100.0
        t = float(t_years)
    except (TypeError, ValueError):
        return none4
    if s <= 0 or k <= 0 or sigma <= 0 or t <= 0:
        return none4
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    delta = _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0
    gamma = pdf_d1 / (s * sigma * sqrt_t)
    vega = s * pdf_d1 * sqrt_t * 0.01  # per 1 vol point
    term = -s * pdf_d1 * sigma / (2.0 * sqrt_t)
    if is_call:
        theta = (term - r * k * math.exp(-r * t) * _norm_cdf(d2)) / 365.0
    else:
        theta = (term + r * k * math.exp(-r * t) * _norm_cdf(-d2)) / 365.0
    return (delta, gamma, theta, vega)


def _closest_strike_index(rows: list, spot: Any) -> int | None:
    if spot is None or not rows:
        return None
    try:
        spot_f = float(spot)
    except (TypeError, ValueError):
        return None
    best_idx = None
    best_dist = None
    for i, row in enumerate(rows):
        strike = _row_field(row, ROW_STRIKE)
        if strike is None:
            continue
        try:
            dist = abs(float(strike) - spot_f)
        except (TypeError, ValueError):
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def _apply_header_tips(table) -> None:
    """Attach plain-language tooltips to any header whose label is jargon."""
    for c in range(table.columnCount()):
        item = table.horizontalHeaderItem(c)
        if item is not None and item.text() in HEADER_TIPS:
            item.setToolTip(HEADER_TIPS[item.text()])


def _make_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


@register_panel(id="options", title="Options Chain", category="Research")
class OptionsChainPanel(Panel):
    def build(self) -> None:
        self._expiries: list[str] = []
        self._current_expiry: str = ""
        self._spot: Any = None
        self._t_years: float | None = None

        # -- header row: expiry combo + spot label ---------------------------
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Expiry:", self))
        self.expiry_combo = QComboBox(self)
        self.expiry_combo.currentTextChanged.connect(self._on_expiry_changed)
        header_row.addWidget(self.expiry_combo)
        header_row.addStretch(1)
        self.spot_lbl = QLabel("Spot: -", self)
        self.spot_lbl.setStyleSheet("font-weight: bold;")
        header_row.addWidget(self.spot_lbl)
        self.content_layout.addLayout(header_row)

        # -- calls / puts tables side by side ---------------------------------
        tables_row = QHBoxLayout()

        self.calls_table = MarketTable(0, len(CALL_HEADERS), self)
        self.calls_table.setHorizontalHeaderLabels(CALL_HEADERS)
        self._configure_table(self.calls_table)
        tables_row.addWidget(self.calls_table, 1)

        self.puts_table = MarketTable(0, len(PUT_HEADERS), self)
        self.puts_table.setHorizontalHeaderLabels(PUT_HEADERS)
        self._configure_table(self.puts_table)
        tables_row.addWidget(self.puts_table, 1)

        _apply_header_tips(self.calls_table)
        _apply_header_tips(self.puts_table)

        self.content_layout.addLayout(tables_row, 1)

        # Greeks off by default — right-click a header ▸ Columns to reveal them.
        self.calls_table.set_hidden_columns(CALL_GREEK_COLS)
        self.puts_table.set_hidden_columns(PUT_GREEK_COLS)

    @staticmethod
    def _configure_table(table: MarketTable) -> None:
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.enable_column_menu()

    # -- symbol / subscription lifecycle -------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_status("loading…")
        self._expiries = []
        self._current_expiry = ""
        self._spot = None
        self._t_years = None
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        self.expiry_combo.blockSignals(False)
        self.calls_table.setRowCount(0)
        self.puts_table.setRowCount(0)
        self.spot_lbl.setText("Spot: -")
        self.unsubscribe_all()
        self.subscribe(f"options:{symbol}", self._on_options)

    def _on_expiry_changed(self, expiry: str) -> None:
        if not expiry or expiry == self._current_expiry or not self.current_symbol:
            return
        self._current_expiry = expiry
        self.unsubscribe_all()
        self.subscribe(f"options:{self.current_symbol}:{expiry}", self._on_options)

    # -- data callback --------------------------------------------------------

    def _on_options(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        # Drop stale callbacks from a previous symbol. Switching tickers quickly
        # can leave a just-queued payload for the old symbol in flight; if it
        # names a different symbol than the one we're showing now, ignore it so
        # the expiry combo and tables never fill with the wrong ticker's data.
        payload_sym = data.get("symbol")
        if payload_sym and payload_sym != self.current_symbol:
            return

        spot = data.get("spot")
        self._spot = spot
        self.spot_lbl.setText(f"Spot: {_fmt_num(spot)}" if spot is not None else "Spot: -")

        expiry = data.get("expiry")
        expiries = data.get("expiries")
        if isinstance(expiries, list) and expiries:
            self._populate_expiry_combo(expiries, expiry)

        # time to expiry for the Greeks (from the payload's expiry, else the combo)
        self._t_years = years_to_expiry(expiry or self._current_expiry)

        calls = data.get("calls") if isinstance(data.get("calls"), list) else []
        puts = data.get("puts") if isinstance(data.get("puts"), list) else []

        self._populate_calls(calls, spot)
        self._populate_puts(puts, spot)

        sym = data.get("symbol") or self.current_symbol
        self.set_status(f"{expiry or '-'} · {len(calls)}C / {len(puts)}P")

    def _populate_expiry_combo(self, expiries: list, current: Any) -> None:
        # Guard against feedback loops while repopulating.
        self.expiry_combo.blockSignals(True)
        try:
            existing = [self.expiry_combo.itemText(i) for i in range(self.expiry_combo.count())]
            new_items = [str(e) for e in expiries]
            if existing != new_items:
                self.expiry_combo.clear()
                self.expiry_combo.addItems(new_items)
                self._expiries = new_items

            target = str(current) if current is not None else self._current_expiry
            if target and target in new_items:
                idx = self.expiry_combo.findText(target)
                if idx >= 0:
                    self.expiry_combo.setCurrentIndex(idx)
                self._current_expiry = target
            elif new_items and not self._current_expiry:
                self.expiry_combo.setCurrentIndex(0)
                self._current_expiry = new_items[0]
        finally:
            self.expiry_combo.blockSignals(False)

    def _populate_calls(self, rows: list, spot: Any) -> None:
        atm_idx = _closest_strike_index(rows, spot)
        self.calls_table.setRowCount(0)
        for i, row in enumerate(rows):
            strike = _row_field(row, ROW_STRIKE)
            iv = _row_field(row, ROW_IV)
            delta, gamma, theta, vega = bs_greeks(
                spot, strike, iv, self._t_years, is_call=True
            )
            r = self.calls_table.rowCount()
            self.calls_table.insertRow(r)
            values = {
                CALL_COL_VOL: _fmt_int(_row_field(row, ROW_VOLUME)),
                CALL_COL_OI: _fmt_int(_row_field(row, ROW_OI)),
                CALL_COL_IV: _fmt_num(iv, 1),
                CALL_COL_DELTA: _fmt_num(delta, 3),
                CALL_COL_GAMMA: _fmt_num(gamma, 4),
                CALL_COL_THETA: _fmt_num(theta, 3),
                CALL_COL_VEGA: _fmt_num(vega, 3),
                CALL_COL_BID: _fmt_num(_row_field(row, ROW_BID)),
                CALL_COL_ASK: _fmt_num(_row_field(row, ROW_ASK)),
                CALL_COL_LAST: _fmt_num(_row_field(row, ROW_LAST)),
                CALL_COL_STRIKE: _fmt_num(strike),
            }
            is_itm = _is_itm(strike, spot, is_call=True)
            is_atm = i == atm_idx
            for col, text in values.items():
                item = _make_item(text)
                self._style_row_item(item, is_itm, is_atm, is_call=True)
                self.calls_table.setItem(r, col, item)

    def _populate_puts(self, rows: list, spot: Any) -> None:
        atm_idx = _closest_strike_index(rows, spot)
        self.puts_table.setRowCount(0)
        for i, row in enumerate(rows):
            strike = _row_field(row, ROW_STRIKE)
            iv = _row_field(row, ROW_IV)
            delta, gamma, theta, vega = bs_greeks(
                spot, strike, iv, self._t_years, is_call=False
            )
            r = self.puts_table.rowCount()
            self.puts_table.insertRow(r)
            values = {
                PUT_COL_STRIKE: _fmt_num(strike),
                PUT_COL_LAST: _fmt_num(_row_field(row, ROW_LAST)),
                PUT_COL_BID: _fmt_num(_row_field(row, ROW_BID)),
                PUT_COL_ASK: _fmt_num(_row_field(row, ROW_ASK)),
                PUT_COL_DELTA: _fmt_num(delta, 3),
                PUT_COL_GAMMA: _fmt_num(gamma, 4),
                PUT_COL_THETA: _fmt_num(theta, 3),
                PUT_COL_VEGA: _fmt_num(vega, 3),
                PUT_COL_IV: _fmt_num(iv, 1),
                PUT_COL_OI: _fmt_int(_row_field(row, ROW_OI)),
                PUT_COL_VOL: _fmt_int(_row_field(row, ROW_VOLUME)),
            }
            is_itm = _is_itm(strike, spot, is_call=False)
            is_atm = i == atm_idx
            for col, text in values.items():
                item = _make_item(text)
                self._style_row_item(item, is_itm, is_atm, is_call=False)
                self.puts_table.setItem(r, col, item)

    @staticmethod
    def _style_row_item(item: QTableWidgetItem, is_itm: bool, is_atm: bool, is_call: bool) -> None:
        if is_atm:
            item.setBackground(QColor(BG_HEADER))
        elif is_itm:
            item.setBackground(QColor(_ITM_TINT_CALL if is_call else _ITM_TINT_PUT))

    # -- persistence ---------------------------------------------------------

    def settings(self) -> dict:
        return {
            "calls_hidden": self.calls_table.hidden_columns(),
            "puts_hidden": self.puts_table.hidden_columns(),
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        if "calls_hidden" in settings:
            self.calls_table.set_hidden_columns(settings.get("calls_hidden", []))
        if "puts_hidden" in settings:
            self.puts_table.set_hidden_columns(settings.get("puts_hidden", []))


def _is_itm(strike: Any, spot: Any, is_call: bool) -> bool:
    if strike is None or spot is None:
        return False
    try:
        strike_f = float(strike)
        spot_f = float(spot)
    except (TypeError, ValueError):
        return False
    return strike_f < spot_f if is_call else strike_f > spot_f
