"""Day Brief: what happened to this stock on this date.

The panel answers one question -- "why did it move that day?" -- by putting
four things next to each other:

* the anchor day's OHLCV and volume against its own 30-day average
* the same session's move for a broad benchmark and a sector proxy, and the
  EXCESS between the stock and the benchmark, which is the only line here that
  can say whether the move was the company or the whole market
* the dated corporate events that landed inside the window
* the headlines from that window

The stat block always describes the **anchor day**; events and news sweep the
**window** around it (see date_context.WINDOW_SPAN_DAYS). That split is what makes
"widen until you catch the story" work without a second date control fighting
the first, and the resolved range is always printed back so a chip like "1W"
never stays abstract.

News helpers are *composed* from ``_news_common`` rather than inherited:
``NewsPanelBase`` owns its own symbol subscription, filter box, read-state and
empty state, all of which a date-driven panel needs to do differently.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl

from ..components.empty_state import EmptyState
from ..date_context import (
    DEFAULT_WINDOW,
    WINDOW_SPAN_DAYS,
    WINDOWS,
    DateContext,
    format_range,
    parse_iso,
    window_range,
)
from ..panel import Panel, register_panel
from ..symbol_context import UNLINKED
from ..theme import ACCENT, BORDER, FG, FG_DIM, FG_MUTED, MONO_FONT, tick_color
from ._news_common import (
    make_news_table,
    news_url_at,
    populate_news_table,
)

#: rows of the stat block, in the order they read
_STAT_ROWS = ("MOVE", "VOL", "VS", "EXCESS")

_HINT_PICK = (
    "Pick a date — click a mark in the chart's EVENTS lane, "
    "hover a bar and press D, or type /day 2026-03-14"
)

#: glyphs shared with the chart's events lane. The mark you click is the mark
#: you then read, which is most of what makes the two feel like one instrument.
EVENT_GLYPH = {
    "earnings": "E",
    "dividend": "D",
    "split": "S",
    "rating": "▲",
    "rating_down": "▼",
    "move": "●",
}


def event_glyph(event: dict) -> str:
    """Glyph for one event row. Rating changes split by direction so a
    downgrade never reads as an upgrade at a glance."""
    kind = str(event.get("kind") or "")
    if kind == "rating":
        action = str(event.get("action") or "").lower()
        detail = str(event.get("detail") or "").lower()
        if "down" in action or "down" in detail:
            return EVENT_GLYPH["rating_down"]
        return EVENT_GLYPH["rating"]
    return EVENT_GLYPH.get(kind, "·")


def fmt_num(value: Any, places: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{places}f}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_volume(value: Any) -> str:
    """Compact volume, matching the chart's own axis style (150M / 2.5B)."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= cut:
            return f"{v / cut:,.1f}{suffix}"
    return f"{v:,.0f}"


@register_panel(id="day_brief", title="Day Brief", category="Markets")
class DayBriefPanel(Panel):
    """Symbol- and date-linked. Subscribes to three topics and renders three
    blocks; the cross-source joining all happens in providers/dayinfo.py."""

    #: The one panel exempt from the never-float rule (see Panel.FLOATABLE).
    #: A brief is opened on demand, read, and closed; docking it into a tuned
    #: workspace to do that squeezes every other panel and forces the user to
    #: scroll sideways in the panels they actually keep open.
    FLOATABLE = True

    def build(self) -> None:
        self._anchor: str = ""
        self._window: str = DEFAULT_WINDOW
        self._suppress_date_signal = False

        self._date_ctx = DateContext.instance()
        self._date_ctx.date_changed.connect(self._on_ctx_date)

        self.content_layout.setSpacing(6)
        self._build_controls()
        self._build_stats()
        self._build_events()
        self._build_news()

        self._empty = EmptyState.attach(
            self.news_table, "No symbol linked", "Click a ticker in any panel."
        )
        # a date may already be live on this group when the panel is created
        existing = self._date_ctx.date(self.link_group)
        if existing:
            self._anchor = existing
            self._window = self._date_ctx.window(self.link_group)
            self._sync_controls()
        self._refresh()

    # -- construction ------------------------------------------------------

    def _build_controls(self) -> None:
        row = QWidget(self)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)

        self.prev_btn = QPushButton("◀", row)
        self.next_btn = QPushButton("▶", row)
        for btn, tip in (
            (self.prev_btn, "Previous session"),
            (self.next_btn, "Next session"),
        ):
            btn.setObjectName("chartChip")
            btn.setToolTip(tip)
            btn.setFixedWidth(22)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))

        self.date_edit = QDateEdit(row)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("ddd d MMM yyyy")
        self.date_edit.setMaximumDate(QDate.currentDate())
        self.date_edit.setToolTip("The day being explained")
        self.date_edit.dateChanged.connect(self._on_date_edit)

        hl.addWidget(self.prev_btn)
        hl.addWidget(self.date_edit)
        hl.addWidget(self.next_btn)
        hl.addStretch(1)

        self._chips: dict[str, QPushButton] = {}
        for token in WINDOWS:
            chip = QPushButton(token, row)
            chip.setObjectName("chartChip")
            chip.setCheckable(True)
            chip.setChecked(token == self._window)
            chip.setToolTip(
                f"Sweep events and news across "
                f"{WINDOW_SPAN_DAYS[token]} days centred on this date"
            )
            chip.clicked.connect(lambda _=False, t=token: self._set_window(t))
            self._chips[token] = chip
            hl.addWidget(chip)

        self.content_layout.addWidget(row)

        # the sub-line that keeps the window concrete: "13 - 15 Mar - 3 days"
        self.range_lbl = QLabel("", self)
        self.range_lbl.setObjectName("panelEyebrow")
        self.range_lbl.setStyleSheet(f"color: {FG_MUTED};")
        self.content_layout.addWidget(self.range_lbl)

    def _rule(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {BORDER}; border: none;")
        return line

    def _build_stats(self) -> None:
        self.content_layout.addWidget(self._rule())
        holder = QWidget(self)
        grid = QVBoxLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)

        mono = QFont(MONO_FONT)
        mono.setPointSize(9)

        self._stat_labels: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        for key in _STAT_ROWS:
            line = QWidget(holder)
            hl = QHBoxLayout(line)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)

            eyebrow = QLabel(key, line)
            eyebrow.setObjectName("panelEyebrow")
            eyebrow.setStyleSheet(f"color: {FG_MUTED};")
            eyebrow.setFixedWidth(58)

            value = QLabel("—", line)
            value.setFont(mono)
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            value.setFixedWidth(78)

            rest = QLabel("", line)
            rest.setFont(mono)
            rest.setStyleSheet(f"color: {FG_DIM};")

            hl.addWidget(eyebrow)
            hl.addWidget(value)
            hl.addWidget(rest, 1)
            grid.addWidget(line)
            self._stat_labels[key] = (eyebrow, value, rest)

        self.content_layout.addWidget(holder)

    def _build_events(self) -> None:
        self.content_layout.addWidget(self._rule())
        head = QLabel("EVENTS", self)
        head.setObjectName("panelEyebrow")
        head.setStyleSheet(f"color: {FG_MUTED};")
        self.content_layout.addWidget(head)

        self.events_table = QTableWidget(0, 4, self)
        self.events_table.horizontalHeader().setVisible(False)
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.setShowGrid(False)
        self.events_table.setWordWrap(False)
        self.events_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.events_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.events_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.events_table.verticalHeader().setDefaultSectionSize(19)
        hh = self.events_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.events_table.setColumnWidth(0, 20)
        self.events_table.setMaximumHeight(5 * 19 + 4)  # scrolls beyond; a busy day can carry a dozen rating actions
        self.content_layout.addWidget(self.events_table)

    def _build_news(self) -> None:
        self.content_layout.addWidget(self._rule())
        self.news_head = QLabel("NEWS", self)
        self.news_head.setObjectName("panelEyebrow")
        self.news_head.setStyleSheet(f"color: {FG_MUTED};")
        self.content_layout.addWidget(self.news_head)

        self.news_table = make_news_table(self)
        self.news_table.cellDoubleClicked.connect(self._open_row)
        self.content_layout.addWidget(self.news_table, 1)

    # -- date / window control --------------------------------------------

    def _sync_controls(self) -> None:
        """Push model state into the widgets without echoing signals back."""
        self._suppress_date_signal = True
        day = parse_iso(self._anchor)
        if day is not None:
            self.date_edit.setDate(QDate(day.year, day.month, day.day))
        self._suppress_date_signal = False
        for token, chip in self._chips.items():
            chip.setChecked(token == self._window)
        rng = window_range(self._anchor, self._window) if self._anchor else None
        if rng:
            span = (parse_iso(rng[1]) - parse_iso(rng[0])).days + 1
            unit = "day" if span == 1 else "days"
            self.range_lbl.setText(f"{format_range(*rng)} · {span} {unit}")
        else:
            self.range_lbl.setText("")

    def _on_date_edit(self, qdate: QDate) -> None:
        if self._suppress_date_signal:
            return
        self._publish(qdate.toString("yyyy-MM-dd"), self._window)

    def _set_window(self, token: str) -> None:
        if not self._anchor:
            # let the chip stick anyway, so the choice survives picking a date
            self._window = token
            self._sync_controls()
            return
        self._publish(self._anchor, token)

    def _step(self, days: int) -> None:
        """Walk a day at a time, skipping weekends. Holidays still land on a
        closed day; the provider snaps and the panel says it did."""
        day = parse_iso(self._anchor) or date.today()
        for _ in range(7):
            day = day + timedelta(days=days)
            if day.weekday() < 5:
                break
        if day > date.today():
            day = date.today()
        self._publish(day.isoformat(), self._window)

    def _publish(self, anchor: str, window: str) -> None:
        """Send the change to the link group, or apply it locally when
        unlinked -- mirroring Panel.set_symbol's two paths."""
        if self.link_group == UNLINKED:
            self._apply_date(anchor, window)
            return
        self._date_ctx.set_date(self.link_group, anchor, window, source=self)
        self._apply_date(anchor, window)

    def _on_ctx_date(self, group: str, anchor: str, window: str, source) -> None:
        if group != self.link_group or source is self:
            return
        self._apply_date(anchor, window)

    def _apply_date(self, anchor: str, window: str) -> None:
        if parse_iso(anchor) is None:
            return
        self._anchor, self._window = anchor, window
        self._sync_controls()
        self._refresh()

    # -- Panel hooks -------------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self._refresh()

    def set_link_group(self, group: str) -> None:
        super().set_link_group(group)
        if group != UNLINKED:
            live = self._date_ctx.date(group)
            if live:
                self._apply_date(live, self._date_ctx.window(group))

    # -- data --------------------------------------------------------------

    def _refresh(self) -> None:
        symbol = self.current_symbol
        if not symbol:
            self._empty.set_text("No symbol linked", "Click a ticker in any panel.")
            self._clear_all()
            return
        if not self._anchor:
            self._empty.set_text(f"{symbol} — no date chosen", _HINT_PICK)
            self._clear_all()
            self.set_status("pick a date")
            return

        rng = window_range(self._anchor, self._window)
        if rng is None:
            return
        start, end = rng
        self._empty.set_text("Loading…", "")
        self.set_loading(True)
        self.unsubscribe_all()
        self.subscribe(f"daystat:{symbol}:{self._anchor}", self._on_daystat)
        self.subscribe(f"dayev:{symbol}:{start}..{end}", self._on_events)
        self.subscribe(f"newsr:{symbol}:{start}..{end}", self._on_news)

    def _clear_all(self) -> None:
        for key in _STAT_ROWS:
            _, value, rest = self._stat_labels[key]
            value.setText("—")
            value.setStyleSheet(f"color: {FG_DIM};")
            rest.setText("")
        self.events_table.setRowCount(0)
        self.news_table.setRowCount(0)

    def _on_daystat(self, data: Any) -> None:
        # The fetch resolved — lower the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        if not isinstance(data, dict):
            return
        if data.get("empty"):
            self.set_status(str(data.get("reason") or "no data"))
            self._clear_all()
            return

        pct = data.get("pct")
        _, move_v, move_r = self._stat_labels["MOVE"]
        move_v.setText(fmt_pct(pct))
        move_v.setStyleSheet(f"color: {tick_color(pct or 0)};")
        move_r.setText(
            f"O {fmt_num(data.get('o'))}  H {fmt_num(data.get('h'))}  "
            f"L {fmt_num(data.get('l'))}  C {fmt_num(data.get('c'))}"
        )

        ratio = data.get("vol_ratio")
        _, vol_v, vol_r = self._stat_labels["VOL"]
        vol_v.setText(fmt_volume(data.get("v")))
        vol_v.setStyleSheet(f"color: {FG};")
        vol_r.setText(f"{ratio:,.1f}× 30-day avg" if ratio else "")

        _, vs_v, vs_r = self._stat_labels["VS"]
        bench, sector = data.get("bench_sym"), data.get("sector_sym")
        if bench:
            vs_v.setText(fmt_pct(data.get("bench_pct")))
            vs_v.setStyleSheet(f"color: {FG_DIM};")
            tail = f"{bench}"
            if sector:
                tail += f"   ·   {sector} {fmt_pct(data.get('sector_pct'))}"
            vs_r.setText(tail)
        else:
            # no honest benchmark for this listing; say so instead of guessing
            vs_v.setText("—")
            vs_v.setStyleSheet(f"color: {FG_DIM};")
            vs_r.setText("no benchmark for this listing")

        exc = data.get("excess_pct")
        _, exc_v, exc_r = self._stat_labels["EXCESS"]
        exc_v.setText(fmt_pct(exc))
        exc_v.setStyleSheet(f"color: {tick_color(exc or 0)}; font-weight: 600;")
        exc_r.setText(str(data.get("verdict") or ""))

        snapped = data.get("snapped_from")
        shown = data.get("date") or self._anchor
        if snapped:
            self.set_status(f"{snapped} was not a session — showing {shown}")
        else:
            self.set_status("")

    def _on_events(self, data: Any) -> None:
        events = data.get("events") if isinstance(data, dict) else None
        self.events_table.setRowCount(0)
        if not events:
            self.events_table.setRowCount(1)
            cell = QTableWidgetItem("no corporate events in this window")
            cell.setForeground(_muted())
            self.events_table.setItem(0, 1, cell)
            self.events_table.setSpan(0, 1, 1, 3)
            return
        for ev in events[:12]:
            row = self.events_table.rowCount()
            self.events_table.insertRow(row)
            cells = (
                event_glyph(ev),
                _short_date(ev.get("date")),
                str(ev.get("title") or ""),
                str(ev.get("detail") or ""),
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setForeground(_accent())
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif col in (1, 3):
                    item.setForeground(_dim())
                self.events_table.setItem(row, col, item)

    def _on_news(self, data: Any) -> None:
        items = data.get("items") if isinstance(data, dict) else data
        hidden = data.get("hidden", 0) if isinstance(data, dict) else 0
        count = populate_news_table(self.news_table, items or [])
        rng = window_range(self._anchor, self._window)
        label = format_range(*rng) if rng else ""
        tail = ""
        if count:
            tail = f"   ·   {count} headline" + ("s" if count != 1 else "")
        self.news_head.setText(f"NEWS  {label}".rstrip() + tail)
        if count:
            pass  # EmptyState hides itself once the table has rows
        elif hidden:
            # the gate normally fails open; on a narrow historical window it
            # can legitimately empty the result, so name that rather than
            # leaving the user staring at a blank panel
            self._empty.set_text(
                f"{hidden} headlines hidden by your reading languages",
                "Settings ▸ News Languages… to widen them.",
            )
        else:
            self._empty.set_text(
                f"No headlines for {label}", "Try a wider window — 1W or 1M."
            )

    def _open_row(self, row: int, _col: int) -> None:
        url = news_url_at(self.news_table, row)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # -- persistence -------------------------------------------------------

    def settings(self) -> dict:
        return {"anchor": self._anchor, "window": self._window}

    def restore(self, settings: dict) -> None:
        """Must never raise -- a bad layout file costs the whole workspace."""
        if not isinstance(settings, dict):
            return
        anchor = settings.get("anchor")
        if isinstance(anchor, str) and parse_iso(anchor) is not None:
            self._anchor = parse_iso(anchor).isoformat()
        window = settings.get("window")
        if window in WINDOWS:
            self._window = window
        self._sync_controls()
        self._refresh()


def _short_date(iso: Any) -> str:
    d = parse_iso(str(iso or ""))
    return f"{d.day} {d.strftime('%b')}" if d else ""


def _accent():
    from PySide6.QtGui import QColor

    return QColor(ACCENT)


def _dim():
    from PySide6.QtGui import QColor

    return QColor(FG_DIM)


def _muted():
    from PySide6.QtGui import QColor

    return QColor(FG_MUTED)
