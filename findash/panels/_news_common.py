"""Shared news rendering: robust timestamp parsing + a clean Time/Headline
table used by both the symbol News panel and the Topic News panel.

Underscore-prefixed so ``discover_panels`` skips it (it registers no panel).
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from ..theme import ACCENT


def parse_published(value: Any) -> Optional[datetime]:
    """Parse the many shapes a feed 'published' field arrives in: epoch number,
    ISO-8601, or RFC-2822 (what gnews/RSS emit, e.g. 'Wed, 05 Feb 2026 08:00:00
    GMT') — the last of which the old ISO-only parser silently dropped."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return None


def format_when(dt: Optional[datetime]) -> str:
    """HH:MM for today's items (in local time), 'Mon DD' for older ones."""
    if dt is None:
        return "—"
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%b %d")


def make_news_table(parent) -> QTableWidget:
    table = QTableWidget(0, 2, parent)
    table.setHorizontalHeaderLabels(["Time", "Headline"])
    vh = table.verticalHeader()
    vh.setVisible(False)
    vh.setDefaultSectionSize(20)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setAlternatingRowColors(True)
    hh = table.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    return table


def populate_news_table(table: QTableWidget, data: Any) -> int:
    """Fill ``table`` from a list of news dicts. Returns the row count."""
    table.setRowCount(0)
    items = data if isinstance(data, list) else []
    count = 0
    for entry in items:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title") or "(untitled)"
        publisher = entry.get("publisher") or ""
        when = format_when(parse_published(entry.get("published")))

        row = table.rowCount()
        table.insertRow(row)

        time_item = QTableWidgetItem(when)
        time_item.setForeground(QColor(ACCENT))
        time_item.setFlags(time_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        head_item = QTableWidgetItem(title)
        head_item.setToolTip(f"{title}\n— {publisher}" if publisher else title)
        head_item.setData(Qt.ItemDataRole.UserRole, entry.get("url"))
        head_item.setFlags(head_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        table.setItem(row, 0, time_item)
        table.setItem(row, 1, head_item)
        count += 1
    return count


def news_url_at(table: QTableWidget, row: int) -> Optional[str]:
    item = table.item(row, 1)
    if item is None:
        return None
    url = item.data(Qt.ItemDataRole.UserRole)
    return str(url) if url else None
