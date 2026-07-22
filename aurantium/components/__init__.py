"""Reusable UI building blocks shared across aurantium panels."""

from .list_editor import (
    EditorColumn,
    EditorSection,
    ListEditorDialog,
    open_add_picker,
    open_list_editor,
)
from .market_table import (
    MarketTable,
    NumericTableWidgetItem,
    make_filter_edit,
    parse_numeric,
)
from .plot_utils import attach_hover, clamp_view, view_limits
from .symbol_catalog import (
    FRED_ENTRIES,
    FX_ENTRIES,
    INDEX_ENTRIES,
    SECTOR_ETF_ENTRIES,
    TENOR_ENTRIES,
    CatalogEntry,
    commodity_entries,
    search_catalog,
)
from .symbol_search import (
    SuggestField,
    SuggestionEngine,
    SymbolSuggestion,
    attach_suggestions,
    shared_engine,
)

__all__ = [
    "CatalogEntry",
    "EditorColumn",
    "EditorSection",
    "FRED_ENTRIES",
    "FX_ENTRIES",
    "INDEX_ENTRIES",
    "ListEditorDialog",
    "MarketTable",
    "NumericTableWidgetItem",
    "SECTOR_ETF_ENTRIES",
    "SuggestField",
    "SuggestionEngine",
    "SymbolSuggestion",
    "TENOR_ENTRIES",
    "attach_hover",
    "attach_suggestions",
    "clamp_view",
    "commodity_entries",
    "make_filter_edit",
    "open_add_picker",
    "open_list_editor",
    "parse_numeric",
    "search_catalog",
    "shared_engine",
    "view_limits",
]
