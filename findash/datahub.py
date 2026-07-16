"""In-process pub/sub data bus, adapted from Fincept Terminal's DataHub.

Panels subscribe to string topics ("quote:AAPL"); providers own *-suffix
topic patterns ("quote:*") and are asked to refresh stale topics. Panels
never fetch data directly.

Threading model: providers fetch on the global QThreadPool and call
``publish()`` from worker threads; publishes are marshalled to the GUI
thread via a queued Qt signal before fan-out, so subscriber callbacks
always run on the GUI thread.
"""

from __future__ import annotations

import fnmatch
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal


@dataclass
class TopicPolicy:
    """Per-topic-pattern refresh/caching rules (Fincept TopicPolicy.h)."""

    ttl_s: float = 30.0           # cached value considered fresh this long
    min_interval_s: float = 5.0   # never refresh a topic more often than this
    refresh_timeout_s: float = 30.0  # clear a stuck in-flight refresh after this
    push_only: bool = False       # scheduler never touches; provider pushes


class Provider:
    """Interface a data provider implements (Fincept Producer.h).

    ``refresh()`` must not block: schedule work (e.g. via ``DataHub.run_async``)
    and call ``hub.publish(topic, value)`` / ``hub.publish_error(topic, msg)``
    when done.
    """

    def topic_patterns(self) -> list[str]:
        raise NotImplementedError

    def refresh(self, topics: list[str]) -> None:
        raise NotImplementedError


@dataclass
class _Subscription:
    owner: QObject
    callback: Callable[[Any], None]
    on_error: Optional[Callable[[str], None]] = None


@dataclass
class _TopicState:
    value: Any = None
    has_value: bool = False
    last_publish: float = 0.0
    last_request: float = 0.0
    in_flight: bool = False
    in_flight_since: float = 0.0
    last_error: str = ""


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], None]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:  # pragma: no cover - trivial
        try:
            self._fn()
        except Exception:
            traceback.print_exc()


class DataHub(QObject):
    """Singleton topic bus. Use ``DataHub.instance()``."""

    # (topic, value) — queued so cross-thread publishes land on the GUI thread
    _publish_sig = Signal(str, object)
    _error_sig = Signal(str, str)
    #: public: fires after fan-out; useful for diagnostics
    topic_updated = Signal(str, object)
    topic_error = Signal(str, str)

    _inst: Optional["DataHub"] = None

    @classmethod
    def instance(cls) -> "DataHub":
        if cls._inst is None:
            cls._inst = DataHub()
        return cls._inst

    def __init__(self) -> None:
        super().__init__()
        self._providers: list[Provider] = []
        self._policies: list[tuple[str, TopicPolicy]] = []  # (pattern, policy)
        self._subs: dict[str, list[_Subscription]] = {}
        self._topics: dict[str, _TopicState] = {}
        self._pool = QThreadPool.globalInstance()
        self._publish_sig.connect(self._do_publish)
        self._error_sig.connect(self._do_publish_error)
        self._scheduler = QTimer(self)
        self._scheduler.setInterval(1000)
        self._scheduler.timeout.connect(self._tick)
        self._scheduler.start()

    # -- registration ------------------------------------------------------

    def register_provider(self, provider: Provider) -> None:
        self._providers.append(provider)

    def set_policy(self, pattern: str, policy: TopicPolicy) -> None:
        # longest (most specific) pattern wins at resolve time
        self._policies.append((pattern, policy))
        self._policies.sort(key=lambda p: len(p[0]), reverse=True)

    # -- subscribing -------------------------------------------------------

    def subscribe(
        self,
        owner: QObject,
        topic: str,
        callback: Callable[[Any], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Subscribe ``owner`` to ``topic``. Auto-unsubscribes when the owner
        is destroyed. Delivers the cached value immediately if fresh, and
        triggers a refresh if stale."""
        sub = _Subscription(owner, callback, on_error)
        self._subs.setdefault(topic, []).append(sub)
        owner.destroyed.connect(lambda *_: self.unsubscribe_all(owner))
        st = self._topics.get(topic)
        if st and st.has_value:
            callback(st.value)  # warm start, stale-is-better-than-blank
        self.request([topic])

    def unsubscribe(self, owner: QObject, topic: str) -> None:
        subs = self._subs.get(topic, [])
        self._subs[topic] = [s for s in subs if s.owner is not owner]

    def unsubscribe_all(self, owner: QObject) -> None:
        for topic in list(self._subs):
            self._subs[topic] = [s for s in self._subs[topic] if s.owner is not owner]

    # -- publishing (provider side; any thread) ----------------------------

    def publish(self, topic: str, value: Any) -> None:
        try:
            self._publish_sig.emit(topic, value)
        except RuntimeError:
            pass  # hub torn down mid-fetch (app shutdown) — drop the result

    def publish_error(self, topic: str, error: str) -> None:
        try:
            self._error_sig.emit(topic, error)
        except RuntimeError:
            pass  # hub torn down mid-fetch (app shutdown)

    def run_async(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the thread pool (helper for providers)."""
        self._pool.start(_Job(fn))

    # -- pull-through ------------------------------------------------------

    def peek(self, topic: str) -> Any:
        """Last cached value regardless of freshness (None if never set)."""
        st = self._topics.get(topic)
        return st.value if st and st.has_value else None

    def request(self, topics: list[str], force: bool = False) -> None:
        """Ask for a refresh now, respecting min_interval unless forced."""
        now = time.monotonic()
        by_provider: dict[int, tuple[Provider, list[str]]] = {}
        for topic in topics:
            st = self._state(topic)
            pol = self._resolve_policy(topic)
            if st.in_flight or pol.push_only:
                continue
            if not force and now - st.last_request < pol.min_interval_s:
                continue
            prov = self._find_provider(topic)
            if prov is None:
                continue
            st.last_request = now
            st.in_flight = True
            st.in_flight_since = now
            key = id(prov)
            by_provider.setdefault(key, (prov, []))[1].append(topic)
        for prov, batch in by_provider.values():
            try:
                prov.refresh(batch)
            except Exception as exc:  # provider bug: fail the topics, not the app
                for t in batch:
                    self.publish_error(t, f"provider error: {exc}")

    # -- internals ---------------------------------------------------------

    def _do_publish(self, topic: str, value: Any) -> None:
        st = self._state(topic)
        st.value = value
        st.has_value = True
        st.last_publish = time.monotonic()
        st.in_flight = False
        st.last_error = ""
        for sub in list(self._subs.get(topic, [])):
            try:
                sub.callback(value)
            except Exception:
                traceback.print_exc()
        self.topic_updated.emit(topic, value)

    def _do_publish_error(self, topic: str, error: str) -> None:
        st = self._state(topic)
        st.in_flight = False
        st.last_error = error
        for sub in list(self._subs.get(topic, [])):
            if sub.on_error:
                try:
                    sub.on_error(error)
                except Exception:
                    traceback.print_exc()
        self.topic_error.emit(topic, error)

    def _tick(self) -> None:
        """1s scheduler: refresh stale topics that still have subscribers."""
        now = time.monotonic()
        due: list[str] = []
        for topic, subs in self._subs.items():
            if not subs:
                continue
            st = self._state(topic)
            pol = self._resolve_policy(topic)
            if pol.push_only:
                continue
            if st.in_flight:
                if now - st.in_flight_since > pol.refresh_timeout_s:
                    st.in_flight = False  # hung provider watchdog
                continue
            if now - st.last_publish >= pol.ttl_s:
                due.append(topic)
        if due:
            self.request(due)

    def _state(self, topic: str) -> _TopicState:
        if topic not in self._topics:
            self._topics[topic] = _TopicState()
        return self._topics[topic]

    def _resolve_policy(self, topic: str) -> TopicPolicy:
        for pattern, policy in self._policies:
            if fnmatch.fnmatchcase(topic, pattern):
                return policy
        return TopicPolicy()

    def _find_provider(self, topic: str) -> Optional[Provider]:
        for prov in self._providers:
            for pattern in prov.topic_patterns():
                if fnmatch.fnmatchcase(topic, pattern):
                    return prov
        return None
