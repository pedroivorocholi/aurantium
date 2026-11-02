"""DataHub.hold(): fetches wait while a workspace restores.

Panels subscribe as they are built, and every subscription used to start its
fetch at once — dozens of worker threads parsing feeds in Python while the UI
thread was still building panels. They took turns with it on the GIL and
stretched the restore to ~23s, with UI-thread stretches long enough for
Windows to mark the app "Not responding". Held, a subscriber still gets its
cached value at once; only the network fetch waits for release()."""

from aurantium.datahub import DataHub, Provider


class _Recorder(Provider):
    def __init__(self):
        self.batches = []

    def topic_patterns(self):
        return ["probe:*"]

    def refresh(self, topics):
        self.batches.append(list(topics))


def _hub(qapp):
    hub = DataHub()
    hub._cache = None  # no disk cache in tests
    prov = _Recorder()
    hub.register_provider(prov)
    return hub, prov


def test_held_hub_defers_fetches_until_release(qapp):
    from PySide6.QtCore import QObject

    hub, prov = _hub(qapp)
    owner = QObject()
    hub.hold()
    hub.subscribe(owner, "probe:A", lambda v: None)
    hub.subscribe(owner, "probe:B", lambda v: None)
    assert prov.batches == []
    hub.release()
    assert sorted(t for b in prov.batches for t in b) == ["probe:A", "probe:B"]


def test_held_hub_still_serves_known_values(qapp):
    from PySide6.QtCore import QObject

    hub, _prov = _hub(qapp)
    hub._do_publish("probe:A", 42)
    got = []
    hub.hold()
    hub.subscribe(QObject(), "probe:A", got.append)
    assert got == [42]
    hub.release()


def test_holds_nest(qapp):
    from PySide6.QtCore import QObject

    hub, prov = _hub(qapp)
    hub.hold()
    hub.hold()
    hub.subscribe(QObject(), "probe:A", lambda v: None)
    hub.release()
    assert prov.batches == []
    hub.release()
    assert prov.batches == [["probe:A"]]
