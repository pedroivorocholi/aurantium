"""Data tables scroll by pixel, not by row.

Qt's default is ScrollPerItem: one wheel notch jumps a whole row and the view
snaps to a row boundary. In a dense table that is the single most "not fluid"
thing in the app, and it is the one improvement that costs no motion budget at
all — there is nothing to animate, nothing to time, and nothing to skip under
reduced motion. It is just how scrolling should have worked.
"""

import pytest
from PySide6.QtWidgets import QAbstractItemView


def _per_pixel(view) -> bool:
    return (
        view.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
        and view.horizontalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    )


def test_market_tables_scroll_by_pixel(qapp):
    from aurantium.components.market_table import MarketTable

    assert _per_pixel(MarketTable(0, 3))


def test_the_news_table_scrolls_by_pixel(qapp):
    """It is a plain QTableWidget rather than a MarketTable, so it needs the
    same setting applied by hand — headlines are exactly the kind of list a
    user scans by dragging."""
    from aurantium.panels._news_common import make_news_table

    assert _per_pixel(make_news_table(None))
