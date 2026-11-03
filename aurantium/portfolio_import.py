"""One-time import of the per-layout portfolio books into the app-wide store.

Before the store existed each saved layout carried its own Portfolio panel
positions. The first run after the upgrade adopts one book, never merges, and
keeps a JSON backup of every distinct book (with the layout it came from) so
nothing the user entered is lost.
"""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QSettings

from .portfolio_store import PortfolioStore, normalize_positions

_IMPORTED_KEY = "portfolio/imported"
_BACKUP_KEY = "portfolio/legacy_books"


def books_in_layout(doc: dict) -> list[list[dict]]:
    """Normalized positions of every portfolio panel spec holding >=1 position."""
    specs = doc.get("panels") if isinstance(doc, dict) else None
    books: list[list[dict]] = []
    for spec in specs if isinstance(specs, list) else []:
        if not isinstance(spec, dict) or spec.get("panel_id") != "portfolio":
            continue
        settings = spec.get("settings")
        raw = settings.get("positions") if isinstance(settings, dict) else None
        book = normalize_positions(raw)
        if book:
            books.append(book)
    return books


def import_legacy_books(
    store: PortfolioStore,
    active: Optional[tuple[str, dict]],
    saved: dict[str, dict],
    settings: Optional[QSettings] = None,
) -> list[str]:
    """Adopt one legacy book into an empty store, once; back up the rest.

    Returns the names of layouts whose books differ from the adopted one (empty
    when nothing was adopted), for the user-facing notice.
    """
    qs = settings if settings is not None else QSettings()
    if qs.value(_IMPORTED_KEY, False, type=bool):
        return []

    found: list[tuple[str, list[dict]]] = []
    if active is not None:
        name, doc = active
        found += [(name, b) for b in books_in_layout(doc)]
    for name, doc in saved.items():
        books = books_in_layout(doc)
        if active is not None and name == active[0]:
            # the last session is usually a copy of the layout saved under the
            # same name; only a genuinely different book is kept, relabelled
            if books == books_in_layout(active[1]):
                continue
            name = f"{name} (saved)"
        found += [(name, b) for b in books]

    if found and not qs.contains(_BACKUP_KEY):
        distinct: list[dict] = []
        for name, book in found:
            if all(d["positions"] != book for d in distinct):
                distinct.append({"layout": name, "positions": book})
        qs.setValue(_BACKUP_KEY, json.dumps(distinct))

    imported: Optional[list[dict]] = None
    if found and store.is_empty():
        imported = found[0][1]
        store.replace(imported)

    qs.setValue(_IMPORTED_KEY, True)
    qs.sync()

    if imported is None:
        return []
    names: list[str] = []
    for name, book in found:
        if book != imported and name not in names:
            names.append(name)
    return names


def import_from_layout_file(store: PortfolioStore, doc: dict) -> bool:
    """Fill an empty store from an imported layout file's first book."""
    if not store.is_empty():
        return False
    books = books_in_layout(doc)
    if not books:
        return False
    store.replace(books[0])
    return True
