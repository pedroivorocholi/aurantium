"""Reading-languages picker: main language + any others the user reads.

Shown once on first launch (ahead of the onboarding guide) and reopenable from
``Settings ▸ News Languages…``. The choice drives which feeds the news provider
fetches and which headlines survive its language gate — see ``languages.py``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import languages
from .theme import FG_DIM

#: checkbox columns in the "other languages" grid
_COLUMNS = 3


class LanguageDialog(QDialog):
    """Modal picker. Call :meth:`selection` after ``exec()`` returns accepted,
    or just read the stored preference — :meth:`_save` persists on accept."""

    def __init__(self, parent=None, first_run: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("News Languages")
        self.setMinimumWidth(560)
        self._checks: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)

        intro = QLabel(
            "Aurantium shows news in the languages you read — and only those.<br>"
            "Pick your main language, then tick any others you're comfortable "
            "reading. Headlines in every other language are filtered out.",
            self,
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        root.addWidget(intro)

        # -- main language -------------------------------------------------
        main_box = QGroupBox("Main language", self)
        main_layout = QVBoxLayout(main_box)
        self._main = QComboBox(main_box)
        for code, *_ in languages.LANGUAGES:
            self._main.addItem(languages.label(code), code)
        current_main = languages.main_language()
        index = self._main.findData(current_main)
        self._main.setCurrentIndex(index if index >= 0 else 0)
        self._main.currentIndexChanged.connect(self._sync_main)
        main_layout.addWidget(self._main)
        main_hint = QLabel("Headlines in this language are listed first.", main_box)
        main_hint.setStyleSheet(f"color: {FG_DIM};")
        main_layout.addWidget(main_hint)
        root.addWidget(main_box)

        # -- other languages -----------------------------------------------
        others_box = QGroupBox("Other languages you read", self)
        others_layout = QVBoxLayout(others_box)

        grid_host = QWidget(others_box)
        grid = QGridLayout(grid_host)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(2)
        spoken = set(languages.spoken_languages())
        for i, (code, *_) in enumerate(languages.LANGUAGES):
            check = QCheckBox(languages.label(code), grid_host)
            check.setChecked(code in spoken)
            self._checks[code] = check
            grid.addWidget(check, i // _COLUMNS, i % _COLUMNS)

        # The list is long enough to push the buttons off a short screen, so it
        # scrolls rather than forcing the dialog taller than the display.
        scroll = QScrollArea(others_box)
        scroll.setWidget(grid_host)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(190)
        others_layout.addWidget(scroll)
        root.addWidget(others_box, 1)

        self._warning = QLabel("", self)
        self._warning.setWordWrap(True)
        self._warning.setVisible(False)
        root.addWidget(self._warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        if first_run:
            # No saved preference to fall back on yet — "Cancel" would be a
            # dead end, so first run offers a single, obvious way forward.
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setVisible(False)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("Continue")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sync_main()

    # -- behaviour ---------------------------------------------------------

    def _sync_main(self) -> None:
        """The main language is always read, so its checkbox is ticked and
        locked — unticking it would be a contradiction the user can't act on."""
        main = self._main.currentData()
        for code, check in self._checks.items():
            is_main = code == main
            if is_main and not check.isChecked():
                check.setChecked(True)
            check.setEnabled(not is_main)
            check.setToolTip(
                "Your main language is always included" if is_main else ""
            )

    def selection(self) -> tuple[str, list[str]]:
        """``(main, others)`` as currently shown."""
        main = str(self._main.currentData())
        others = [c for c, chk in self._checks.items() if chk.isChecked() and c != main]
        return main, others

    def _save(self) -> None:
        main, others = self.selection()
        languages.set_languages(main, others)
        self.accept()


def prompt_languages(parent=None, first_run: bool = False) -> bool:
    """Show the picker. Returns True when the user saved a choice."""
    return bool(LanguageDialog(parent, first_run=first_run).exec())
