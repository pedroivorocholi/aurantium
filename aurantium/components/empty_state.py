"""The message a panel shows when it has nothing to display.

A blank table or an empty chart canvas reads as a broken panel: the user can't
tell "no symbol selected yet" from "this name has no analyst coverage" from
"the fetch failed". This states which, in two lines of quiet type on the data
surface — no illustration, no card, no icon. The terminal says the fact and
gets out of the way.

Used by :class:`~aurantium.components.market_table.MarketTable` for empty
tables and by the chart panel for an empty plot, so both read identically.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..theme import FG_DIM, FG_MUTED, MONO_FONT, UI_FONT


class EmptyState(QWidget):
    """A transparent overlay painting a centered title + optional hint.

    Add it as a child of the widget whose content is missing (a table's
    viewport, a plot canvas), keep its geometry in sync with that widget, and
    show/hide it. It never takes mouse events, so whatever is underneath stays
    interactive — a chart's crosshair and context menu keep working while the
    message is up.
    """

    def __init__(self, parent: QWidget, title: str = "No data", hint: str = "") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title = title
        self.hint = hint
        self.hide()

    # -- attaching to a plain item view -------------------------------------

    @classmethod
    def attach(cls, view, title: str = "No data", hint: str = "") -> "EmptyState":
        """Give any item view an empty state and keep it in sync by itself.

        For views that aren't a :class:`MarketTable` (which wires this up with
        its own loading/filter rules). The overlay parents to the viewport —
        an item view paints its rows there, so a child of the view itself would
        sit behind them — and follows the model's own row signals, so callers
        never have to remember to refresh it.

        Returns the overlay; call ``set_text`` on it to change the message, and
        ``sync`` after hiding rows yourself (a filter, say), which the model
        signals don't report.
        """
        state = cls(view.viewport(), title, hint)
        state._view = view
        view.viewport().installEventFilter(state)
        model = view.model()
        for signal in (
            model.rowsInserted,
            model.rowsRemoved,
            model.modelReset,
            model.layoutChanged,
        ):
            signal.connect(state.sync)
        state.sync()
        return state

    def sync(self, *args) -> None:
        """Show the message when the attached view has no visible rows."""
        view = getattr(self, "_view", None)
        if view is None:
            return
        visible = any(
            not view.isRowHidden(r) for r in range(view.model().rowCount())
        )
        self.setGeometry(view.viewport().rect())
        self.setVisible(not visible)
        if not visible:
            self.raise_()
            self.update()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        view = getattr(self, "_view", None)
        if (
            view is not None
            and obj is view.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self.setGeometry(view.viewport().rect())
        return super().eventFilter(obj, event)

    # -- content ------------------------------------------------------------

    def set_text(self, title: str, hint: str = "") -> None:
        self.title = title or "No data"
        self.hint = hint
        if self.isVisible():
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()

        title_font = QFont(MONO_FONT)
        title_font.setPointSizeF(10.5)
        line_h = QFontMetrics(title_font).height()
        gap = 5 if self.hint else 0
        block_h = line_h * (2 if self.hint else 1) + gap
        top = rect.center().y() - block_h // 2

        p.setFont(title_font)
        p.setPen(QColor(FG_DIM))
        p.drawText(
            QRect(rect.left(), top, rect.width(), line_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self.title,
        )
        if self.hint:
            hint_font = QFont(UI_FONT)
            hint_font.setPointSizeF(8.5)
            p.setFont(hint_font)
            p.setPen(QColor(FG_MUTED))
            p.drawText(
                QRect(rect.left(), top + line_h + gap, rect.width(), line_h),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                self.hint,
            )
        p.end()
