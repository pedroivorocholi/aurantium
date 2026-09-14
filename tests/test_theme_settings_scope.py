"""The theme must survive being read before the application has a name.

``theme.py`` resolves the whole palette at import — ``_ACTIVE``, every colour
constant, ``STYLESHEET`` and ``ADS_STYLESHEET`` are computed once at the bottom
of the module. So the settings read that decides which palette wins happens at
whatever moment something first imports ``aurantium.theme``.

That moment is early. ``__main__`` builds its splash card with ``MONO_FONT``
(``__main__.py:80``) long before ``main()`` reaches
``app.setOrganizationName("aurantium")`` — and on the way there the
findash→aurantium migration deliberately points the application names at
``findash`` for a few lines. A bare ``QSettings()`` keys off exactly those
names, so both windows read an empty scope, fall back to ``DEFAULT_THEME``, and
pin the process to dark.

That is not hypothetical: it is what shipped. Choosing the light theme restarted
the app and changed nothing but the chart canvas, whose colours live in the
saved layout and are remapped later, once the names are finally correct. The
result was a dark terminal with one white rectangle in it.

The fix is that ``theme`` names its own scope. These tests hold that line.

**Which test is the actual guard.** ``test_the_settings_scope_is_named_explicitly``
is the one that fails against the bug — checked by restoring the old inline
``QSettings()`` and watching it report ``organizationName() == ""``. The
behavioural tests below it cannot reproduce the bug, and it is worth saying so
rather than trusting them: isolating settings needs ``IniFormat`` + ``setPath``
(see ``test_languages``), and under Ini an unnamed scope collapses onto the same
file as a named one. On the registry, where this actually shipped, they are
different paths. So the behavioural tests document the contract and would catch
a *different* future regression — a read path that stops going through
``_settings()`` — while the scope-name assertion is what pins this bug.
"""

import pytest

from aurantium import theme


@pytest.fixture(autouse=True)
def isolated_settings(qapp, tmp_path, monkeypatch):
    """Redirect ``theme``'s settings scope at ``tmp_path``.

    **Not** the ``setDefaultFormat`` + ``setPath`` pattern ``test_languages``
    uses, because that pattern does not work here — and failing to notice cost
    this file a round of resetting the developer's real theme on every run.

    That pattern isolates a *bare* ``QSettings()``, which picks up
    ``defaultFormat()``. ``theme._settings()`` names its scope explicitly
    (``QSettings("aurantium", "aurantium")``) and, measured, ignores
    ``setDefaultFormat`` entirely: it still resolves to
    ``\\HKEY_CURRENT_USER\\Software\\aurantium\\aurantium``. So the fixture's
    own ``clear()`` was wiping the real preferences it was written to protect.

    Overriding the accessor is the honest fix — the thing under test is which
    *scope* the module reads, so the test substitutes the scope rather than
    trying to move the whole process's default underneath it.
    """
    from PySide6.QtCore import QSettings

    def _tmp_settings() -> QSettings:
        return QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            "aurantium-test",
            "aurantium-test",
        )

    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path)
    )
    real = theme._settings
    monkeypatch.setattr(theme, "_settings", _tmp_settings)
    _tmp_settings().clear()
    yield real  # the un-patched accessor, for the test that inspects the scope
    _tmp_settings().clear()


@pytest.fixture
def app_names(qapp):
    """Restore the application's org/app names after a test scrambles them."""
    from PySide6.QtCore import QCoreApplication

    org = QCoreApplication.organizationName()
    name = QCoreApplication.applicationName()
    yield
    QCoreApplication.setOrganizationName(org)
    QCoreApplication.setApplicationName(name)


# -- the scope is named, not inherited -------------------------------------


def test_the_settings_scope_is_named_explicitly(isolated_settings):
    """A bare ``QSettings()`` would inherit whatever the application is called
    at that instant. The palette read must not depend on that.

    Takes the *un-patched* accessor from the fixture — this is the one test that
    is about the real scope rather than about behaviour through it, and it is
    the assertion that actually fails against the shipped bug (an inline
    ``QSettings()`` at splash time reports ``organizationName() == ""``).
    """
    s = isolated_settings()
    assert s.organizationName() == "aurantium"
    assert s.applicationName() == "aurantium"


@pytest.mark.parametrize(
    "org,app",
    [
        ("", ""),              # the splash: imported before main() names anything
        ("findash", "findash"),  # mid-migration, __main__.py:243-244
        ("someone-else", "other"),
    ],
    ids=["unnamed", "mid-migration", "foreign"],
)
def test_the_saved_theme_wins_whatever_the_app_is_called(app_names, org, app):
    """Reading the theme must not depend on the application's current names.

    ``light`` specifically, because ``dark`` is also the fallback — a test that
    only checked dark would pass just as happily against the bug.
    """
    from PySide6.QtCore import QCoreApplication

    theme.set_theme("light")
    QCoreApplication.setOrganizationName(org)
    QCoreApplication.setApplicationName(app)

    assert theme.current_theme() == "light"
    assert theme.palette_colors()["BG"] == "#ffffff"


def test_the_colorblind_flag_wins_whatever_the_app_is_called(app_names):
    """Colour-blind mode is read at import too (it folds into ``_ACTIVE``), so
    it carries the identical hazard and needs the identical guarantee."""
    from PySide6.QtCore import QCoreApplication

    theme.set_colorblind(True)
    QCoreApplication.setOrganizationName("findash")
    QCoreApplication.setApplicationName("findash")

    assert theme.colorblind_enabled() is True
    # blue up / vermillion down, not green/red
    assert theme.palette_colors("dark")["UP"] == theme._COLORBLIND["dark"]["UP"]


def test_reads_and_writes_agree():
    """The shipped bug was asymmetric — ``set_theme`` wrote to the correct scope
    (it runs late, once the names are right) while the import-time read landed
    somewhere else. Round-tripping through the module's own API is what catches
    a future divergence."""
    for name in theme.THEMES:
        theme.set_theme(name)
        assert theme.current_theme() == name
