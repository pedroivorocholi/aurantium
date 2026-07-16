"""Provider registration: wires concrete Provider implementations into the
DataHub singleton. Called once from ``__main__.py`` before panels load."""

from __future__ import annotations

from ..datahub import DataHub, TopicPolicy
from .econ import EconProvider
from .fundamentals import FundamentalsProvider
from .market import MarketProvider
from .news import NewsProvider


def register_all_providers() -> None:
    """Instantiate providers, register them, and set per-topic-pattern
    refresh policies."""
    hub = DataHub.instance()

    hub.register_provider(MarketProvider())
    hub.register_provider(NewsProvider())
    hub.register_provider(EconProvider())
    hub.register_provider(FundamentalsProvider())

    hub.set_policy("quote:*", TopicPolicy(ttl_s=30, min_interval_s=5))
    hub.set_policy("history:*", TopicPolicy(ttl_s=1800, min_interval_s=60))
    hub.set_policy("analyst:*", TopicPolicy(ttl_s=3600, min_interval_s=60))
    hub.set_policy("news:*", TopicPolicy(ttl_s=300, min_interval_s=30))
    hub.set_policy("newsq:*", TopicPolicy(ttl_s=300, min_interval_s=30))
    hub.set_policy("profile:*", TopicPolicy(ttl_s=86400, min_interval_s=120))
    hub.set_policy("fred:*", TopicPolicy(ttl_s=3600, min_interval_s=120))
    hub.set_policy("wb:*", TopicPolicy(ttl_s=3600, min_interval_s=120))
    hub.set_policy("wbc:*", TopicPolicy(ttl_s=3600, min_interval_s=120))
    hub.set_policy("cftc:*", TopicPolicy(ttl_s=3600, min_interval_s=120))
    hub.set_policy("eia:*", TopicPolicy(ttl_s=3600, min_interval_s=120))
    hub.set_policy("financials:*", TopicPolicy(ttl_s=86400, min_interval_s=120))
    hub.set_policy("holders:*", TopicPolicy(ttl_s=86400, min_interval_s=120))
    hub.set_policy("earnings:*", TopicPolicy(ttl_s=21600, min_interval_s=120))
    hub.set_policy("dividends:*", TopicPolicy(ttl_s=21600, min_interval_s=120))
    hub.set_policy("options:*", TopicPolicy(ttl_s=120, min_interval_s=30))
    hub.set_policy("movers:*", TopicPolicy(ttl_s=120, min_interval_s=30))
