from aurantium import fx


def test_minor_units_normalise():
    assert fx.normalise("GBp") == ("GBP", 0.01)
    assert fx.normalise("ZAc") == ("ZAR", 0.01)
    assert fx.normalise("ILA") == ("ILS", 0.01)
    assert fx.normalise("brl") == ("BRL", 1.0)
    assert fx.normalise(None) == (None, 1.0)
    assert fx.normalise("") == (None, 1.0)


def test_same_currency_needs_no_rate():
    assert fx.convert(100.0, "BRL", "BRL", None) == 100.0
    assert fx.convert(250.0, "GBp", "GBP", None) == 2.5


def test_convert_with_rate():
    assert fx.convert(3800.0, "BRL", "USD", 0.18) == 684.0


def test_minor_unit_source_with_rate():
    assert fx.convert(250.0, "GBp", "USD", 1.25) == 3.125


def test_missing_rate_or_currency_is_none():
    assert fx.convert(1.0, "BRL", "USD", None) is None
    assert fx.convert(1.0, None, "USD", 1.0) is None
    assert fx.convert(None, "USD", "USD", None) is None


def test_pair_symbol():
    assert fx.pair_symbol("BRL", "USD") == "BRLUSD=X"


def test_listing_currency_guess():
    assert fx.listing_currency_guess("AAPL", None) == "USD"
    assert fx.listing_currency_guess("PETR4.SA", None) is None
    assert fx.listing_currency_guess("PETR4.SA", "BRL") == "BRL"


def test_menu_list_order():
    assert fx.CURRENCIES[0] == "USD"
    assert len(fx.CURRENCIES) == 17
