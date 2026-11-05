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

from typing import Optional

__all__ = [
    "CURRENCIES",
    "normalise",
    "pair_symbol",
    "convert",
    "listing_currency_guess",
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
