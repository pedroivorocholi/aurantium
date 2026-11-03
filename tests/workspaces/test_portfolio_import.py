"""One-time import of legacy per-layout portfolio books into the store."""

import json

from PySide6.QtCore import QSettings

from aurantium.portfolio_import import (
    books_in_layout,
    import_from_layout_file,
    import_legacy_books,
)
from aurantium.portfolio_store import PortfolioStore


def _qs(tmp_path, name="s.ini"):
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def _doc(*books):
    return {"panels": [
        {"instance": f"portfolio#{i}", "panel_id": "portfolio", "link_group": None,
         "settings": {"positions": b}} for i, b in enumerate(books)
    ] + [{"instance": "chart#1", "panel_id": "chart", "settings": {}}]}


AAPL = [["AAPL", 10, 150.0, "2026-01-02", None, None]]
TSLA = [["TSLA", 5, 200.0, "2026-02-03", None, None]]
MSFT = [["MSFT", 1, 300.0, "2026-03-04", None, None]]


def _setup(tmp_path):
    qs = _qs(tmp_path)
    return qs, PortfolioStore(qs)


def _syms(store):
    return [p["symbol"] for p in store.positions()]


def test_books_in_layout_skips_empty_and_other_panels():
    assert [b[0]["symbol"] for b in books_in_layout(_doc(AAPL, [], TSLA))] == ["AAPL", "TSLA"]
    assert books_in_layout({}) == []
    assert books_in_layout({"panels": "junk"}) == []


def test_imports_active_layout_book(tmp_path):
    qs, store = _setup(tmp_path)
    assert import_legacy_books(store, ("Main", _doc(AAPL)), {}, settings=qs) == []
    assert _syms(store) == ["AAPL"]
    assert qs.value("portfolio/imported", False, type=bool)


def test_falls_back_to_first_saved_layout_with_a_book(tmp_path):
    qs, store = _setup(tmp_path)
    saved = {"Empty": _doc([]), "Paper": _doc(TSLA), "Other": _doc(MSFT)}
    names = import_legacy_books(store, None, saved, settings=qs)
    assert _syms(store) == ["TSLA"]
    assert names == ["Other"]


def test_runs_once(tmp_path):
    qs, store = _setup(tmp_path)
    import_legacy_books(store, ("Main", _doc(AAPL)), {}, settings=qs)
    store.replace([])
    assert import_legacy_books(store, ("Main", _doc(TSLA)), {}, settings=qs) == []
    assert store.is_empty()


def test_flag_set_even_when_nothing_found(tmp_path):
    qs, store = _setup(tmp_path)
    assert import_legacy_books(store, None, {}, settings=qs) == []
    assert qs.value("portfolio/imported", False, type=bool)


def test_never_merges_and_reports_other_books(tmp_path):
    qs, store = _setup(tmp_path)
    names = import_legacy_books(
        store, ("Main", _doc(AAPL)), {"Main": _doc(MSFT), "Paper": _doc(TSLA)}, settings=qs)
    assert _syms(store) == ["AAPL"]
    assert names == ["Paper"]
    backup = json.loads(qs.value("portfolio/legacy_books", "", type=str))
    assert {b["layout"] for b in backup} == {"Main", "Paper"}
    assert len(backup) == 2


def test_two_portfolio_panels_in_one_layout(tmp_path):
    qs, store = _setup(tmp_path)
    names = import_legacy_books(store, ("Main", _doc(AAPL, TSLA)), {}, settings=qs)
    assert _syms(store) == ["AAPL"]
    assert names == ["Main"]
    backup = json.loads(qs.value("portfolio/legacy_books", "", type=str))
    assert [b["positions"][0]["symbol"] for b in backup] == ["AAPL", "TSLA"]


def test_backup_never_overwritten(tmp_path):
    qs, store = _setup(tmp_path)
    qs.setValue("portfolio/legacy_books", "[]")
    import_legacy_books(store, ("Main", _doc(AAPL)), {"Paper": _doc(TSLA)}, settings=qs)
    assert qs.value("portfolio/legacy_books", "", type=str) == "[]"


def test_non_empty_store_not_replaced(tmp_path):
    qs, store = _setup(tmp_path)
    store.replace(MSFT)
    import_legacy_books(store, ("Main", _doc(AAPL)), {}, settings=qs)
    assert _syms(store) == ["MSFT"]


def test_layout_file_import_only_fills_empty_store(tmp_path):
    qs, store = _setup(tmp_path)
    assert import_from_layout_file(store, _doc(AAPL)) is True
    assert _syms(store) == ["AAPL"]
    assert import_from_layout_file(store, _doc(TSLA)) is False
    assert _syms(store) == ["AAPL"]
    empty = PortfolioStore(_qs(tmp_path, "e.ini"))
    assert import_from_layout_file(empty, _doc([])) is False
