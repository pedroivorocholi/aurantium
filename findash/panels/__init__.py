"""Built-in findash panels.

Normally discovered dynamically (see ``discover_package_panels``). ``BUILTIN``
is an explicit fallback used when dynamic enumeration finds nothing — which is
the case inside a frozen (PyInstaller) build, where the package's modules live
in an archive rather than on disk. Add new built-in panel modules here so they
survive packaging.
"""

BUILTIN = (
    "analyst",
    "chart",
    "chart_grid",
    "commodities",
    "dividends",
    "earnings",
    "fundamentals",
    "fx_monitor",
    "holders",
    "macro",
    "movers",
    "news",
    "options_chain",
    "performance",
    "portfolio",
    "profile",
    "sector_heatmap",
    "topic_news",
    "watchlist",
    "world_indices",
)
