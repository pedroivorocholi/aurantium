"""Currency conversion maths for the preferred-currency setting.

Pure functions only; the rate-fetching service builds on these. Every function
fails closed: a missing currency or rate yields ``None`` rather than a number
silently computed in the wrong unit, so no total ever mixes currencies.

Yahoo quotes some listings in a minor unit (pence, South African cents,
Israeli agorot) spelled with a lower-case tail: ``GBp``, ``ZAc``, ``ILA``.
``normalise`` folds those into the base ISO code plus the factor that turns an
amount in the minor unit into the base one.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timezone
from typing import Any, Optional

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from .datahub import DataHub

__all__ = [
    "CURRENCIES",
    "normalise",
    "pair_symbol",
    "convert",
    "listing_currency_guess",
    "FxService",
]

#: Preferred-currency menu, in display order (first is the default).
CURRENCIES: tuple[str, ...] = (
    "USD", "EUR", "GBP", "JPY", "BRL", "CAD", "AUD", "CHF", "CNY",
    "HKD", "INR", "MXN", "KRW", "SEK", "NOK", "ZAR", "SGD",
)

# Case-sensitive on purpose: "GBp" is pence, "GBP" is pounds.
_MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 0.01),
    "ZAc": ("ZAR", 0.01),
    "ILA": ("ILS", 0.01),
}


def normalise(code: Optional[str]) -> tuple[Optional[str], float]:
    """Return ``(ISO code, factor to the base unit)``; ``(None, 1.0)`` if blank."""
    if not code:
        return None, 1.0
    if code in _MINOR_UNITS:
        return _MINOR_UNITS[code]
    return code.upper(), 1.0


def pair_symbol(src: str, dst: str) -> str:
    """Yahoo FX symbol for ``src`` -> ``dst``, e.g. ``BRLUSD=X``."""
    return f"{src}{dst}=X"


def convert(
    amount: Optional[float],
    src: Optional[str],
    dst: str,
    rate: Optional[float],
) -> Optional[float]:
    """Convert ``amount`` from ``src`` to ``dst``.

    ``rate`` is units of ``dst`` per 1 ``src`` (base unit, after normalising)
    and is ignored when both are the same currency. ``None`` when the amount,
    source or a needed rate is missing.
    """
    if amount is None:
        return None
    base, factor = normalise(src)
    if base is None:
        return None
    amount = amount * factor
    if base == normalise(dst)[0]:
        return amount
    if rate is None:
        return None
    return amount * rate


def listing_currency_guess(symbol: str, quoted: Optional[str]) -> Optional[str]:
    """The listing currency: the quoted one, else USD for suffix-less tickers."""
    if quoted:
        return quoted
    return "USD" if "." not in symbol else None


_SETTING_KEY = "ui/currency"
#: Rates arrive in bursts (one quote per pair); fold them into one ``changed``.
_COALESCE_MS = 150
_HISTORY_TOPIC = "history:{pair}:2y:1d"


def _day_of(ts: Any) -> Optional[str]:
    """ISO date of a history timestamp (epoch seconds, or an ISO string).

    Daily FX bars are stamped at local midnight of the exchange, which is the
    evening before in UTC for zones ahead of it; 12 hours of slack lands every
    such stamp on the day it belongs to.
    """
    try:
        if isinstance(ts, str):
            return ts[:10]
        moment = datetime.fromtimestamp(float(ts) + 43200, tz=timezone.utc)
        return moment.date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class FxService(QObject):
    """The preferred currency and the rates that convert into it.

    Rates are fetched lazily: the first ask for a pair subscribes to its Yahoo
    FX topic, and ``changed`` fires (coalesced) as values arrive. Until then a
    rate is ``None`` and ``value`` fails closed rather than guessing.
    """

    changed = Signal()

    _inst: Optional["FxService"] = None

    @classmethod
    def instance(cls) -> "FxService":
        if cls._inst is None:
            cls._inst = FxService()
        return cls._inst

    @classmethod
    def reset_instance(cls, svc: Optional["FxService"]) -> None:
        """Swap the singleton (tests); ``None`` makes the next call build one."""
        cls._inst = svc

    def __init__(self) -> None:
        super().__init__()
        raw = QSettings().value(_SETTING_KEY, CURRENCIES[0], type=str)
        self._preferred = raw if raw in CURRENCIES else CURRENCIES[0]
        self._currencies: dict[str, str] = {}
        self._live: dict[str, float] = {}
        # pair -> (ascending ISO days, closes aligned to them)
        self._history: dict[str, tuple[list[str], list[float]]] = {}
        self._live_subs: set[str] = set()
        self._hist_subs: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_COALESCE_MS)
        self._timer.timeout.connect(self.changed.emit)

    # -- the setting -------------------------------------------------------

    def preferred(self) -> str:
        return self._preferred

    def set_preferred(self, code: str) -> None:
        if code not in CURRENCIES or code == self._preferred:
            return
        self._preferred = code
        QSettings().setValue(_SETTING_KEY, code)
        self._notify()

    # -- listing currencies ------------------------------------------------

    def note_quote(self, symbol: str, quote: dict) -> None:
        """Remember the listing currency a quote reported for ``symbol``."""
        cur = quote.get("currency") if quote else None
        if cur:
            self._currencies[symbol] = cur

    def currency_of(self, symbol: str) -> Optional[str]:
        return listing_currency_guess(symbol, self._currencies.get(symbol))

    # -- rates -------------------------------------------------------------

    def _target(self, dst: Optional[str]) -> Optional[str]:
        return normalise(dst or self._preferred)[0]

    def rate(self, src: Optional[str], dst: Optional[str] = None) -> Optional[float]:
        """Live ``dst`` per 1 base-unit ``src``; ``None`` until it arrives."""
        base, target = normalise(src)[0], self._target(dst)
        if base is None or target is None:
            return None
        if base == target:
            return 1.0
        pair = pair_symbol(base, target)
        if pair not in self._live_subs:
            self._live_subs.add(pair)
            DataHub.instance().subscribe(
                self, f"quote:{pair}", lambda q, p=pair: self._on_quote(p, q), None
            )
        return self._live.get(pair)

    def rate_on(
        self, src: Optional[str], day: str, dst: Optional[str] = None
    ) -> Optional[float]:
        """Close on ``day`` (last trading day on or before it), else ``None``."""
        base, target = normalise(src)[0], self._target(dst)
        if base is None or target is None:
            return None
        if base == target:
            return 1.0
        pair = pair_symbol(base, target)
        if pair not in self._hist_subs:
            self._hist_subs.add(pair)
            DataHub.instance().subscribe(
                self,
                _HISTORY_TOPIC.format(pair=pair),
                lambda h, p=pair: self._on_history(p, h),
                None,
            )
        days, closes = self._history.get(pair, ([], []))
        i = bisect.bisect_right(days, day[:10])
        return closes[i - 1] if i else None

    def value(
        self, symbol: str, amount_local: Optional[float], day: Optional[str] = None
    ) -> Optional[float]:
        """``amount_local`` (in the listing currency) in the preferred one."""
        src = self.currency_of(symbol)
        if src is None or amount_local is None:
            return None
        r = self.rate_on(src, day) if day else self.rate(src)
        return convert(amount_local, src, self._preferred, r)

    # -- hub callbacks -----------------------------------------------------

    def _on_quote(self, pair: str, quote: Any) -> None:
        price = quote.get("price") if isinstance(quote, dict) else None
        if price and price > 0 and self._live.get(pair) != price:
            self._live[pair] = float(price)
            self._notify()

    def _on_history(self, pair: str, hist: Any) -> None:
        if not isinstance(hist, dict):
            return
        rows = []
        for ts, close in zip(hist.get("t") or [], hist.get("c") or []):
            day = _day_of(ts)
            if day and close:
                rows.append((day, float(close)))
        rows.sort(key=lambda r: r[0])
        self._history[pair] = ([r[0] for r in rows], [r[1] for r in rows])
        self._notify()

    def _notify(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
