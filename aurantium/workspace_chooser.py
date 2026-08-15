"""First-run workspace picker.

A fresh install has no auto-saved session, so without this the app opens to an
empty window and the user has to discover the Panels menu unaided. Offering the
shipped presets at that moment is the cheapest activation win available.

Dismissing is always allowed and lands on the empty workspace — the previous
behaviour, unchanged.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from .presets import Preset


from . import motion

class WorkspaceChooser(QDialog):
    """Modal list of shipped workspaces shown on first run."""

    def __init__(self, presets: list[Preset], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a workspace")
        self.resize(420, 320)
        self._presets = list(presets)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "Start from a curated workspace, or skip and build your own from "
            "the <b>Panels</b> menu. You can switch anytime from "
            "<b>Settings &#9656; Workspaces</b>."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self._list = QListWidget(self)
        for preset in self._presets:
            self._list.addItem(preset.name)
        if self._presets:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self._list, 1)

        self._detail = QLabel(self)
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)
        self._list.currentRowChanged.connect(self._show_detail)
        self._show_detail(self._list.currentRow())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("Open workspace")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Start empty")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_detail(self, row: int) -> None:
        if 0 <= row < len(self._presets):
            self._detail.setText(self._presets[row].description)
        else:
            self._detail.setText("")

    def chosen(self) -> Preset | None:
        """The highlighted preset, or ``None`` if nothing is selected."""
        row = self._list.currentRow()
        if 0 <= row < len(self._presets):
            return self._presets[row]
        return None


    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # First-run tier: a beat of arrival is welcome here in a way it would
        # not be on a control the user hits all day.
        super().showEvent(event)
        motion.fade_in_dialog(self)

def pick_workspace(
    presets: list[Preset], parent: QWidget | None = None
) -> Preset | None:
    """Show the chooser. Returns the chosen preset, or ``None`` if dismissed."""
    if not presets:
        return None
    dialog = WorkspaceChooser(presets, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.chosen()
    return None
