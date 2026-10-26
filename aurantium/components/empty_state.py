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

from .. import motion
from ..theme import ACCENT, FG_DIM, FG_MUTED, FONT_LG, FONT_MD, MONO_FONT, SPACE_MD, SPACE_XL, UI_FONT


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
        self._loading = False
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
        self.set_shown(not visible)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        view = getattr(self, "_view", None)
        if (
            view is not None
            and obj is view.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self.setGeometry(view.viewport().rect())
        return super().eventFilter(obj, event)

    def set_shown(self, on: bool) -> None:
        """Crossfade the message in or out (``OVERLAY_MS``), as MarketTable's
        own empty state always has. Opacity only — the overlay is out of
        layout, so nothing underneath moves.

        Keyed on ``isHidden()``, not ``isVisible()``: the latter is False
        whenever an ancestor is hidden, which during a layout restore is every
        panel, and the message would be left up when the window appeared.
        """
        if on:
            self.raise_()
            self.update()
            if self.isHidden():
                # a hidden overlay sits at full opacity; start the fade low so
                # it arrives instead of popping
                motion.fade_animation(self)
                self._aurantium_fade_effect.setOpacity(0.0)
            motion.fade(self, 1.0)
        elif not self.isHidden():
            motion.fade(self, 0.0, hide_when_done=True)

    # -- loading --------------------------------------------------------------

    def set_loading(self, on: bool) -> None:
        """While a fetch is in flight, say so instead of "no data".

        A news panel switching symbols cleared its table and showed "No recent
        headlines for AAPL" for the whole fetch — a false statement, beside a
        header that said loading. Panels forward ``set_loading`` here once this
        overlay is registered as a state target; the wording matches the
        MarketTable veil.
        """
        self._loading = bool(on)
        self.sync()

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

        # pixel sizes off the theme scale; these were 10.5pt and 8.5pt, two
        # sizes that appeared nowhere else in the app
        title_font = QFont(MONO_FONT)
        title_font.setPixelSize(FONT_LG)
        line_h = QFontMetrics(title_font).height()
        hint = "" if self._loading else self.hint
        gap = SPACE_MD if hint else 0
        # The hint wraps inside the panel: drawn as one line it was clipped at
        # both ends in a narrow dock ("ne above: a symbol, price or ch…").
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        hint_font = QFont(UI_FONT)
        hint_font.setPixelSize(FONT_MD)
        text_w = max(rect.width() - 2 * SPACE_XL, 40)
        hint_h = (
            QFontMetrics(hint_font).boundingRect(QRect(0, 0, text_w, 10_000), flags, hint).height()
            if hint else 0
        )
        block_h = line_h + gap + hint_h
        top = rect.center().y() - block_h // 2

        p.setFont(title_font)
        p.setPen(QColor(ACCENT if self._loading else FG_DIM))
        p.drawText(
            QRect(rect.left(), top, rect.width(), line_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "Loading…" if self._loading else self.title,
        )
        if hint:
            p.setFont(hint_font)
            p.setPen(QColor(FG_MUTED))
            p.drawText(
                QRect(rect.left() + (rect.width() - text_w) // 2, top + line_h + gap, text_w, hint_h),
                flags,
                hint,
            )
        p.end()
