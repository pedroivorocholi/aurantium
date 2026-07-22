"""The command bar: a QLineEdit with Up/Down history recall.

Typeahead suggestions (symbols by name, slash-commands) are attached by the
owning window via ``components.symbol_search.SuggestField`` — that owns the
popup, Tab-cycling, and pick behavior. History persists in QSettings across
sessions.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings

from PySide6.QtWidgets import QLineEdit

_HISTORY_KEY = "command_bar/history"
_MAX_HISTORY = 50


class CommandBar(QLineEdit):
    """Ticker/command entry with history recall."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        stored = QSettings().value(_HISTORY_KEY, [], type=list) or []
        self._history: list[str] = [str(h) for h in stored][-_MAX_HISTORY:]
        self._history_idx = len(self._history)  # past the end == current draft
        self._draft = ""

    # -- history -----------------------------------------------------------

    def push_history(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            del self._history[:-_MAX_HISTORY]
            QSettings().setValue(_HISTORY_KEY, self._history)
        self._history_idx = len(self._history)

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_idx == len(self._history):
            self._draft = self.text()  # remember what was being typed
        if self._history_idx > 0:
            self._history_idx -= 1
            self.setText(self._history[self._history_idx])

    def _history_next(self) -> None:
        if self._history_idx >= len(self._history):
            return
        self._history_idx += 1
        if self._history_idx == len(self._history):
            self.setText(self._draft)
        else:
            self.setText(self._history[self._history_idx])

    # -- keys --------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        key = event.key()
        completer = self.completer()
        popup = completer.popup() if completer else None

        # while the suggestion popup is open, it owns Up/Down/Enter/Esc
        if popup is not None and popup.isVisible():
            super().keyPressEvent(event)
            return

        if key == Qt.Key.Key_Up:
            self._history_prev()
            return
        if key == Qt.Key.Key_Down:
            self._history_next()
            return
        super().keyPressEvent(event)
