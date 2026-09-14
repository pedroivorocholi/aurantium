"""Company Profile panel — DES-style overview: name, sector/industry,
business description, key stats, and officers for the linked symbol.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..components.fmt import integer as _fmt_int
from ..components.fmt import num as _fmt_num
from ..panel import NULL_GLYPH, Panel, register_panel
from ..theme import ACCENT, FG_DIM


def _fmt_market_cap(value: Any) -> str:
    if value is None:
        return NULL_GLYPH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NULL_GLYPH
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"{v / div:.1f}{suffix}"
    return f"{v:,.0f}"


def _fmt_pct(value: Any, decimals: int = 2) -> str:
    if value is None:
        return NULL_GLYPH
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return NULL_GLYPH


def _fmt_range(low: Any, high: Any) -> str:
    if low is None and high is None:
        return NULL_GLYPH
    lo = _fmt_num(low)
    hi = _fmt_num(high)
    return f"{lo} – {hi}"


@register_panel(id="profile", title="Company Profile", category="Research")
class ProfilePanel(Panel):
    def build(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget(scroll)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(8)

        self.name_lbl = QLabel("—", body)
        self.name_lbl.setObjectName("statValueLarge")
        self.name_lbl.setWordWrap(True)
        outer.addWidget(self.name_lbl)

        self.sector_lbl = QLabel("", body)
        self.sector_lbl.setObjectName("secondary")
        self.sector_lbl.setWordWrap(True)
        outer.addWidget(self.sector_lbl)

        self.desc_lbl = QLabel("", body)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.desc_lbl)

        self.stats_grid = QGridLayout()
        self.stats_grid.setHorizontalSpacing(16)
        self.stats_grid.setVerticalSpacing(4)
        self._stat_labels: dict[str, QLabel] = {}
        stat_keys = [
            "Mkt Cap", "P/E (TTM)", "P/E (Fwd)", "EPS (TTM)",
            "Div Yield", "Beta", "52wk Range", "Employees",
            "Shares Out", "Website",
        ]
        for i, key in enumerate(stat_keys):
            row, col = divmod(i, 2)
            k_lbl = QLabel(key, body)
            k_lbl.setObjectName("secondary")
            v_lbl = QLabel(NULL_GLYPH, body)
            v_lbl.setStyleSheet("font-weight: 700;")
            v_lbl.setWordWrap(True)
            v_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.stats_grid.addWidget(k_lbl, row, col * 2)
            self.stats_grid.addWidget(v_lbl, row, col * 2 + 1)
            self._stat_labels[key] = v_lbl
        outer.addLayout(self.stats_grid)

        officers_hdr = QLabel("Officers", body)
        officers_hdr.setStyleSheet(f"color: {FG_DIM}; font-weight: 700;")
        outer.addWidget(officers_hdr)
        self.officers_layout = QVBoxLayout()
        self.officers_layout.setSpacing(2)
        outer.addLayout(self.officers_layout)

        outer.addStretch(1)
        scroll.setWidget(body)
        self.content_layout.addWidget(scroll, 1)

    def on_symbol(self, symbol: str) -> None:
        self.set_loading(True)
        self._clear()
        self.unsubscribe_all()
        self.subscribe(f"profile:{symbol}", self._on_profile)

    def _clear(self) -> None:
        """Empty the panel before the next company arrives.

        This one mattered most of the three. The others leave stale *numbers*,
        which at least look like placeholders; this left a full prose
        description, market cap and officer list of the previous company sitting
        under the new ticker — a confident, complete, wrong answer.
        """
        self.name_lbl.setText(NULL_GLYPH)
        self.sector_lbl.setText("")
        self.desc_lbl.setText("")
        for lbl in self._stat_labels.values():
            lbl.setText(NULL_GLYPH)
        while self.officers_layout.count():
            item = self.officers_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_profile(self, data: Any) -> None:
        # The fetch resolved — lower the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        info = data if isinstance(data, dict) else {}

        name = info.get("name") or self.current_symbol or "—"
        self.name_lbl.setText(str(name))

        sector = info.get("sector")
        industry = info.get("industry")
        parts = [p for p in (sector, industry) if p]
        loc_parts = [p for p in (info.get("city"), info.get("country")) if p]
        line = " · ".join(parts)
        if loc_parts:
            line = f"{line}  ({', '.join(loc_parts)})" if line else ", ".join(loc_parts)
        self.sector_lbl.setText(line or NULL_GLYPH)

        self.desc_lbl.setText(info.get("description") or "No description available.")

        self._stat_labels["Mkt Cap"].setText(_fmt_market_cap(info.get("market_cap")))
        self._stat_labels["P/E (TTM)"].setText(_fmt_num(info.get("pe_trailing")))
        self._stat_labels["P/E (Fwd)"].setText(_fmt_num(info.get("pe_forward")))
        self._stat_labels["EPS (TTM)"].setText(_fmt_num(info.get("eps_trailing")))
        self._stat_labels["Div Yield"].setText(_fmt_pct(info.get("dividend_yield")))
        self._stat_labels["Beta"].setText(_fmt_num(info.get("beta")))
        self._stat_labels["52wk Range"].setText(
            _fmt_range(info.get("week52_low"), info.get("week52_high"))
        )
        self._stat_labels["Employees"].setText(_fmt_int(info.get("employees")))
        self._stat_labels["Shares Out"].setText(_fmt_int(info.get("shares_outstanding")))
        self._stat_labels["Website"].setText(info.get("website") or NULL_GLYPH)

        while self.officers_layout.count():
            item = self.officers_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        officers = info.get("officers")
        officers = officers if isinstance(officers, list) else []
        if not officers:
            lbl = QLabel(NULL_GLYPH, self)
            lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
            self.officers_layout.addWidget(lbl)
        for o in officers:
            if not isinstance(o, dict):
                continue
            oname = o.get("name") or ""
            otitle = o.get("title") or ""
            text = f"{oname} — {otitle}" if otitle else oname
            lbl = QLabel(text or NULL_GLYPH, self)
            lbl.setStyleSheet("font-size: 11px;")
            self.officers_layout.addWidget(lbl)

        self.set_status(info.get("sector") or "")
