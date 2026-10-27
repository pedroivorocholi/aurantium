"""The UI thread gets the GIL back promptly while providers work.

Measured in the real app: with Python's default 5ms switch interval the
chart's symbol morph got 2-3 frames, 400-600ms apart, while worker threads
parsed news feeds and price frames; at 0.5ms, 6-8 frames.
"""

import inspect


def test_startup_shortens_the_gil_switch_interval():
    from aurantium import __main__ as entry

    assert entry.GIL_SWITCH_INTERVAL_S <= 0.001
    src = inspect.getsource(entry.main)
    # first thing main() does, before any worker thread can start
    assert src.index("sys.setswitchinterval(GIL_SWITCH_INTERVAL_S)") < src.index(
        "QApplication(sys.argv)"
    )
