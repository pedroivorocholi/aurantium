"""Hover glossary: jargon → plain-language tooltip.

Pins the forgiving lookup (yfinance's run-together labels), the fallbacks
for "Change In X" and "... Net Minority Interest", and that the tooltip is
wrapped rich text and empty for terms that need no help.
"""

from aurantium import glossary
from aurantium.components import MarketTable


def test_exact_and_case_insensitive():
    assert "subsidiary" in glossary.explain("Minority Interest")
    assert glossary.explain("minority interest") == glossary.explain("Minority Interest")


def test_yfinance_run_together_labels_match():
    assert glossary.explain("Tradeand Other Payables Non Current")
    assert glossary.explain("Pensionand Other Post Retirement Benefit Plans Current")
    assert glossary.explain("Dueto Related Parties Current")


def test_change_in_fallback():
    text = glossary.explain("Change In Inventory")
    assert "Inventory" in text and "cash" in text


def test_net_minority_suffix_fallback():
    assert glossary.explain("Total Non Current Liabilities Net Minority Interest")
    base = glossary.explain("Working Capital")
    assert glossary.explain("Working Capital Net Minority Interest").startswith(base)


def test_percent_headers_distinct():
    assert glossary.explain("Chg") != glossary.explain("Chg%")


def test_unknown_or_obvious_gets_no_tooltip():
    assert glossary.explain("Total Revenue") is None
    assert glossary.tooltip("Total Revenue") == ""
    assert glossary.tooltip("") == ""


def test_tooltip_is_wrapped_rich_text_and_escaped():
    tip = glossary.tooltip("Minority Interest")
    assert tip.startswith("<qt><b>Minority Interest</b>")
    assert "<" not in glossary.tooltip("P/E", "a < b")[len("<qt><b>P/E</b><br>"):-len("</qt>")]


def test_body_override():
    assert "custom note" in glossary.tooltip("Net Debt", "custom note")


def test_market_table_headers_get_tooltips(qapp):
    t = MarketTable(0, 3)
    t.setHorizontalHeaderLabels(["Date", "IV%", "Surprise%"])
    assert t.horizontalHeaderItem(0).toolTip() == ""
    assert "volatility" in t.horizontalHeaderItem(1).toolTip()
    assert "consensus" in t.horizontalHeaderItem(2).toolTip()
