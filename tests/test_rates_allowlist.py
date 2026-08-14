"""The FRED allowlist is a licence control, not an optimization: FRED carries
third-party copyrighted series whose commercial redistribution isn't permitted.
An unlisted series must be refused BEFORE any request is made, so a network
failure can never fail open."""

import pytest

from aurantium import rates_allowlist
from aurantium.providers.rates import FredNotAllowed, fetch_fred_series, fred_allowed


def test_allowlist_is_a_frozenset_of_strings():
    assert isinstance(rates_allowlist.ALLOWED, frozenset)
    assert all(isinstance(s, str) for s in rates_allowlist.ALLOWED)


def test_checked_date_is_recorded():
    assert rates_allowlist.CHECKED  # ISO date the filter last ran


def test_unlisted_series_is_refused():
    assert fred_allowed("DEFINITELY_NOT_ALLOWLISTED") is False


def test_refusal_happens_before_any_request():
    """The injected getter raises if called. Refusal must beat it."""

    def exploding_get(*args, **kwargs):
        raise AssertionError("a request was made for a non-allowlisted series")

    with pytest.raises(FredNotAllowed):
        fetch_fred_series("DEFINITELY_NOT_ALLOWLISTED", "fake-key", get=exploding_get)


def test_empty_allowlist_fails_closed(monkeypatch):
    """A truncated or never-regenerated allowlist must refuse everything,
    not allow everything."""
    monkeypatch.setattr(rates_allowlist, "ALLOWED", frozenset())
    assert fred_allowed("IRLTLT01GBM156N") is False
