"""App-wide portfolio positions.

A process-wide singleton that owns the user's positions so they outlive any
panel: the Portfolio panel and the Portfolio news feed both read from here.
Positions persist in ``QSettings`` as a JSON list and :attr:`changed` fires on
every mutation.
"""

from __future__ import annotations

import copy
import json
import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, QSettings, Signal

_POSITIONS_KEY = "portfolio/positions"


def _num_or_none(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text_or_none(v: Any) -> Optional[str]:
    return str(v) if v else None


def _normalize_one(entry: Any) -> Optional[dict]:
    if isinstance(entry, dict):
        symbol, qty = entry.get("symbol"), entry.get("qty")
        cost, date = entry.get("cost"), entry.get("date")
        sell_date, sell_price = entry.get("sell_date"), entry.get("sell_price")
    elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
        # legacy [symbol, qty, cost, date?, sell_date?, sell_price?]
        symbol, qty, cost = entry[0], entry[1], entry[2]
        date = entry[3] if len(entry) > 3 else None
        sell_date = entry[4] if len(entry) > 4 else None
        sell_price = entry[5] if len(entry) > 5 else None
    else:
        return None
    symbol = str(symbol if symbol is not None else "").strip().upper()
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        return None
    if not symbol:
        return None
    return {
        "symbol": symbol,
        "qty": qty,
        "cost": _num_or_none(cost),
        "date": _text_or_none(date),
        "sell_date": _text_or_none(sell_date),
        "sell_price": _num_or_none(sell_price),
    }


def normalize_positions(raw: Any) -> list[dict]:
    """Clean a stored/legacy positions payload (dict or list entries)."""
    out = []
    for entry in raw if isinstance(raw, list) else []:
        norm = _normalize_one(entry)
        if norm is not None:
            out.append(norm)
    return out


class PortfolioStore(QObject):
    """Singleton holder of the user's positions."""

    changed = Signal()

    _instance: Optional["PortfolioStore"] = None

    @classmethod
    def instance(cls) -> "PortfolioStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls, store: Optional["PortfolioStore"]) -> None:
        cls._instance = store

    def __init__(self, settings: Optional[QSettings] = None) -> None:
        super().__init__()
        self._settings = settings if settings is not None else QSettings()
        self._lock = threading.Lock()
        self._positions: list[dict] = self._load()

    # -- reads -------------------------------------------------------------

    def positions(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._positions)

    def open_positions(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._positions if p["sell_date"] is None]

    def is_empty(self) -> bool:
        with self._lock:
            return not self._positions

    # -- mutations ---------------------------------------------------------

    def replace(self, positions: list[dict]) -> None:
        with self._lock:
            self._positions = normalize_positions(positions)
        self._commit()

    def add(self, symbol: str, qty: float, cost: Optional[float], date: str) -> None:
        norm = _normalize_one(
            {"symbol": symbol, "qty": qty, "cost": cost, "date": date})
        if norm is None:
            return
        with self._lock:
            self._positions.append(norm)
        self._commit()

    def mark_sold(self, indices: list[int], prices: dict, today: str) -> None:
        changed = False
        with self._lock:
            for i in indices:
                if not 0 <= i < len(self._positions):
                    continue
                p = self._positions[i]
                if p["sell_date"] is not None:
                    continue
                p["sell_date"] = today
                p["sell_price"] = _num_or_none(prices.get(p["symbol"]))
                changed = True
        if changed:
            self._commit()

    def remove(self, indices: list[int]) -> None:
        drop = set(indices)
        with self._lock:
            kept = [p for i, p in enumerate(self._positions) if i not in drop]
            changed = len(kept) != len(self._positions)
            self._positions = kept
        if changed:
            self._commit()

    def fill_market_cost(self, symbol: str, price: float) -> None:
        symbol = symbol.strip().upper()
        changed = False
        with self._lock:
            for p in self._positions:
                if p["symbol"] == symbol and p["cost"] is None:
                    p["cost"] = float(price)
                    changed = True
        if changed:
            self._commit()

    # -- persistence -------------------------------------------------------

    def _load(self) -> list[dict]:
        raw = self._settings.value(_POSITIONS_KEY, "", type=str)
        try:
            data = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            data = []
        return normalize_positions(data)

    def _commit(self) -> None:
        with self._lock:
            payload = json.dumps(self._positions)
        self._settings.setValue(_POSITIONS_KEY, payload)
        self._settings.sync()
        self.changed.emit()
