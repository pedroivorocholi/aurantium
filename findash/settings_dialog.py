"""API keys dialog: enter optional provider keys in-app instead of hand-editing
``.env``. Saved to ``.env`` next to the app and applied to the live environment,
so providers pick them up on their next refresh — no restart needed."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from .paths import EXT_DIR

ENV_PATH = EXT_DIR / ".env"

# (env var, provider, what it improves, free-key signup URL)
API_KEYS = [
    (
        "FRED_API_KEY",
        "FRED",
        "Federal Reserve economic data series (Macro panel)",
        "https://fred.stlouisfed.org/docs/api/api_key.html",
    ),
    (
        "EIA_API_KEY",
        "EIA",
        "U.S. energy spot prices (Commodities panel)",
        "https://www.eia.gov/opendata/register.php",
    ),
    (
        "NEWSAPI_KEY",
        "NewsAPI.org",
        "Upgrades the News panel's primary source",
        "https://newsapi.org/register",
    ),
    (
        "FINNHUB_API_KEY",
        "Finnhub",
        "First-choice real-time quote source",
        "https://finnhub.io/register",
    ),
    (
        "TWELVEDATA_API_KEY",
        "Twelve Data",
        "Second-choice quote source",
        "https://twelvedata.com/pricing",
    ),
]


def write_env_keys(path: Path, values: dict[str, str]) -> None:
    """Update ``KEY=value`` lines in the .env file at ``path``, preserving any
    comments and unrelated variables; missing keys are appended."""
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    # atomic write: a crash mid-write must never truncate the user's saved keys.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ApiKeysDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API Keys")
        self.setMinimumWidth(560)
        self._fields: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        intro = QLabel(
            "All keys are optional — every panel works without them, keys "
            "unlock better sources. Saved to the .env file next to the app; "
            "panels use new keys on their next refresh.",
            self,
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        for row, (env, name, blurb, url) in enumerate(API_KEYS):
            r = row * 2
            grid.addWidget(QLabel(name, self), r, 0)
            field = QLineEdit(self)
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setText(os.environ.get(env, ""))
            field.setPlaceholderText("not set")
            self._fields[env] = field
            grid.addWidget(field, r, 1)
            hint = QLabel(
                f'{blurb} — <a href="{url}">get a free key</a>', self
            )
            hint.setOpenExternalLinks(True)
            hint.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction
            )
            grid.addWidget(hint, r + 1, 1)
        root.addLayout(grid)

        show = QCheckBox("Show keys", self)
        show.toggled.connect(self._toggle_echo)
        root.addWidget(show)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _toggle_echo(self, visible: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        for field in self._fields.values():
            field.setEchoMode(mode)

    def _save(self) -> None:
        values = {env: f.text().strip() for env, f in self._fields.items()}
        try:
            write_env_keys(ENV_PATH, values)
        except OSError as exc:
            QMessageBox.warning(
                self, "API Keys", f"Couldn't save {ENV_PATH.name}:\n{exc}"
            )
            return
        for env, val in values.items():
            if val:
                os.environ[env] = val
            else:
                os.environ.pop(env, None)
        self.accept()
