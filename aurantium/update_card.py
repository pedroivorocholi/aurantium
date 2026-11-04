"""The update card — what's new, and one button that installs it.

Shown by ``updater.py`` once a newer version is already downloaded and
verified, so the single click never waits on a download: **Update & restart**
runs the installer, the app quits through its normal close path (layout
saved), and the installer reopens the new version.

It pops up centred over the window. Drawn as terminal chrome, like the notice
card (``notifier.py``): chrome fill, hairline border, a lit top edge,
monospaced eyebrows, amber for the one thing that is actionable. Unlike a
notice it does not time out — an update is worth a decision — but it never
takes focus or blocks work, and × puts it away until the next launch.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, icons, motion
from .theme import (
    ACCENT,
    ACCENT_DEEP,
    CHROME,
    CHROME_BORDER,
    CHROME_EDGE,
    CHROME_HOVER,
    CHROME_TEXT,
    CHROME_TEXT_DIM,
    DOWN,
    FG_MUTED,
    FONT_LG,
    FONT_MD,
    FONT_SM,
    MONO_FONT,
    ON_ACCENT,
    RADIUS_MD,
    RADIUS_SM,
    SPACE_LG,
    SPACE_SM,
    SPACE_XL,
    UI_FONT,
    WEIGHT_MEDIUM,
)

#: Minimum gap to the window's edges when the window is small.
MARGIN = 18

#: Fixed width: wide enough for a release note to wrap onto two lines, narrow
#: enough to cover one panel's corner rather than a column of them.
WIDTH = 360

#: The notes scroll past this height, so a long release never covers the window.
NOTES_MAX_HEIGHT = 220

#: How far the card rises on its way in (the notice card's RISE).
RISE = 10


class UpdateCard(QWidget):
    """Child of the main window, centred over it."""

    install_requested = Signal(dict)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("updateCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(WIDTH)
        self._item: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_XL, SPACE_LG + 2, SPACE_XL, SPACE_XL)
        lay.setSpacing(SPACE_SM)

        top = QHBoxLayout()
        top.setSpacing(0)
        self._eyebrow = QLabel("UPDATE READY", self)
        self._eyebrow.setObjectName("updateEyebrow")
        top.addWidget(self._eyebrow)
        top.addStretch(1)
        self._close = QToolButton(self)
        self._close.setObjectName("updateClose")
        icons.set_icon(
            self._close, "close", CHROME_TEXT_DIM,
            hover_color=CHROME_TEXT, disabled_color=FG_MUTED,
        )
        self._close.setToolTip("Not now — ask again next launch")
        self._close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.clicked.connect(self.dismiss)
        top.addWidget(self._close)
        lay.addLayout(top)

        self._title = QLabel("", self)
        self._title.setObjectName("updateTitle")
        lay.addWidget(self._title)
        self._current = QLabel(f"You have {__version__}", self)
        self._current.setObjectName("updateCurrent")
        lay.addWidget(self._current)

        lay.addSpacing(SPACE_LG)
        lay.addWidget(self._rule())
        lay.addSpacing(SPACE_SM)
        heading = QLabel("WHAT'S NEW", self)
        heading.setObjectName("updateHeading")
        lay.addWidget(heading)

        self._notes = QLabel("", self)
        self._notes.setObjectName("updateNotes")
        self._notes.setWordWrap(True)
        self._notes.setTextFormat(Qt.TextFormat.RichText)
        self._notes.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("updateScroll")
        self._scroll.setWidget(self._notes)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self._scroll)

        self._error = QLabel("", self)
        self._error.setObjectName("updateError")
        self._error.setWordWrap(True)
        self._error.hide()
        lay.addWidget(self._error)

        lay.addSpacing(SPACE_LG)
        self._button = QPushButton("Update && restart", self)
        self._button.setObjectName("updateButton")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self._on_install)
        lay.addWidget(self._button)

        self._apply_style()

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setEasingCurve(motion.EASE_OUT)
        self._fade.finished.connect(self._on_fade_finished)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(motion.EASE_OUT)
        self._hide_when_faded = False
        self.hide()

    # -- public API ---------------------------------------------------------

    @Slot(dict)
    def show_update(self, item: dict) -> None:
        """Pop up with ``item`` (an ``updater.find_item`` dict plus ``path``)."""
        self._item = item
        self._title.setText(f"Aurantium {item['version']}")
        self._notes.setText(self._notes_html(item.get("notes") or []))
        self._error.hide()
        self._set_busy(False)
        self._button.setText("Update && restart")
        self._fit_notes()
        self.adjustSize()
        if not self.isHidden() and not self._hide_when_faded:
            self.reposition()
            self.raise_()
            return
        self.show()
        self.raise_()
        self._animate(rise=True)

    @Slot(str)
    def install_failed(self, reason: str) -> None:
        self._error.setText(f"Couldn't start the installer: {reason}")
        self._error.show()
        self._set_busy(False)
        self._button.setText("Try again")
        self.adjustSize()
        self.reposition()

    @Slot(str, str)
    def report(self, text: str, level: str) -> None:
        """A manual check's answer, sent to the window's notice card. A slot on
        this widget (not a lambda) so the worker thread's signal is queued
        onto the UI thread."""
        notify = getattr(self.parentWidget(), "notify", None)
        if notify is not None:
            notify(text, 5000, level)

    @Slot()
    def dismiss(self) -> None:
        if self.isHidden():
            return
        self._animate(rise=False)

    def reposition(self) -> None:
        self.move(self._resting_pos())

    # -- internals ----------------------------------------------------------

    def _on_install(self) -> None:
        if self._item is None:
            return
        self._error.hide()
        self._set_busy(True)
        self._button.setText("Installing…")
        self.install_requested.emit(self._item)

    def _set_busy(self, busy: bool) -> None:
        self._button.setEnabled(not busy)
        self._close.setEnabled(not busy)

    @staticmethod
    def _notes_html(notes: list[str]) -> str:
        if not notes:
            return f'<span style="color:{CHROME_TEXT_DIM}">Fixes and improvements.</span>'
        rows = "".join(
            f'<tr><td style="color:{ACCENT}; padding-right:6px">▸</td>'
            f'<td style="padding-bottom:5px">{html.escape(n)}</td></tr>'
            for n in notes
        )
        return f'<table cellspacing="0" cellpadding="0">{rows}</table>'

    def _fit_notes(self) -> None:
        """Size the scroll area to its notes, up to NOTES_MAX_HEIGHT."""
        inner = WIDTH - 2 * SPACE_XL
        self._notes.setFixedWidth(inner - 10)
        h = self._notes.heightForWidth(inner - 10)
        self._scroll.setFixedHeight(min(max(h, 0), NOTES_MAX_HEIGHT))
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if h > NOTES_MAX_HEIGHT
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def _rule(self) -> QFrame:
        rule = QFrame(self)
        rule.setObjectName("updateRule")
        rule.setFixedHeight(1)
        return rule

    def _resting_pos(self) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return self.pos()
        return QPoint(
            max(MARGIN, (parent.width() - self.width()) // 2),
            max(MARGIN, (parent.height() - self.height()) // 2),
        )

    def _animate(self, rise: bool) -> None:
        resting = self._resting_pos()
        low = QPoint(resting.x(), resting.y() + RISE)
        ms = motion.duration(motion.NOTICE_IN_MS if rise else motion.NOTICE_OUT_MS)
        self._hide_when_faded = not rise
        for anim in (self._slide, self._fade):
            anim.stop()
            anim.setDuration(ms)
        self._slide.setStartValue(low if rise else self.pos())
        self._slide.setEndValue(resting if rise else low)
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(1.0 if rise else 0.0)
        if ms == 0:
            # reduced motion: a zero-length animation never emits finished()
            self.move(resting if rise else low)
            self._effect.setOpacity(1.0 if rise else 0.0)
            self._on_fade_finished()
            return
        self.move(self._slide.startValue())
        self._slide.start()
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._hide_when_faded:
            self._hide_when_faded = False
            self.hide()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"QWidget#updateCard {{ background: {CHROME};"
            f" border: 1px solid {CHROME_BORDER}; border-top-color: {CHROME_EDGE};"
            f" border-radius: {RADIUS_MD}px; }}"
            f"QWidget#updateCard QLabel {{ background: transparent; }}"
            f"QLabel#updateEyebrow {{ color: {ACCENT}; font-family: \"{MONO_FONT}\";"
            f" font-size: {FONT_SM}px; font-weight: {WEIGHT_MEDIUM}; letter-spacing: 1px; }}"
            f"QLabel#updateHeading {{ color: {CHROME_TEXT_DIM}; font-family: \"{MONO_FONT}\";"
            f" font-size: {FONT_SM}px; letter-spacing: 1px; }}"
            f"QLabel#updateTitle {{ color: {CHROME_TEXT}; font-family: \"{UI_FONT}\";"
            f" font-size: {FONT_LG}px; font-weight: {WEIGHT_MEDIUM}; }}"
            f"QLabel#updateCurrent {{ color: {CHROME_TEXT_DIM}; font-family: \"{MONO_FONT}\";"
            f" font-size: {FONT_SM}px; }}"
            f"QLabel#updateNotes {{ color: {CHROME_TEXT}; font-family: \"{UI_FONT}\";"
            f" font-size: {FONT_MD}px; }}"
            f"QLabel#updateError {{ color: {DOWN}; font-family: \"{MONO_FONT}\";"
            f" font-size: {FONT_SM}px; }}"
            f"QFrame#updateRule {{ background: {CHROME_HOVER}; border: none; }}"
            # the viewport and the scrollbar's container are plain QWidgets,
            # which the app-wide rule paints true black
            f"QScrollArea#updateScroll, QScrollArea#updateScroll QWidget"
            f" {{ background: transparent; }}"
            f"QScrollArea#updateScroll QScrollBar::handle:vertical {{ background: {FG_MUTED}; }}"
            f"QToolButton#updateClose {{ background: transparent; border: none;"
            f" color: {CHROME_TEXT_DIM}; font-size: {FONT_SM}px; padding: 2px 4px;"
            f" border-radius: {RADIUS_SM}px; }}"
            f"QToolButton#updateClose:hover {{ background: {CHROME_HOVER}; color: {CHROME_TEXT}; }}"
            f"QPushButton#updateButton {{ background: {ACCENT}; color: {ON_ACCENT};"
            f" border: 1px solid {ACCENT}; border-radius: {RADIUS_SM}px;"
            f" font-family: \"{MONO_FONT}\"; font-size: {FONT_MD}px;"
            f" font-weight: {WEIGHT_MEDIUM}; letter-spacing: 1px; min-height: 27px; }}"
            f"QPushButton#updateButton:hover {{ background: {ACCENT_DEEP}; border-color: {ACCENT_DEEP}; }}"
            f"QPushButton#updateButton:disabled {{ background: {CHROME_HOVER};"
            f" color: {FG_MUTED}; border-color: {CHROME_HOVER}; }}"
        )
