"""The news language gate.

Two properties matter and they pull against each other. It must *never* show a
headline in a language the user didn't pick — the complaint that prompted the
feature was Mandarin headlines in an English-only feed. But it must also never
hide a real story, because a false reject is invisible: the user cannot tell a
filtered headline from one that was never published. So the script check is
absolute and the statistical check is deliberately timid.
"""

import pytest

from aurantium import languages


@pytest.fixture(autouse=True)
def _clean_settings(qapp, tmp_path):
    """Keep tests off the developer's real settings.

    ``languages`` uses a bare ``QSettings()``, which on Windows is registry-
    backed — redirecting only the Ini path would leave the tests writing to the
    developer's actual Aurantium preferences. Forcing the default format to Ini
    *and* pointing it at tmp_path is what actually isolates them.
    """
    from PySide6.QtCore import QSettings

    previous = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path)
    )
    QSettings().clear()
    yield
    QSettings().clear()
    QSettings.setDefaultFormat(previous)


# -- the language table ----------------------------------------------------


def test_every_language_has_a_feed():
    """A language you can pick but get no feeds for is a broken promise: the
    picker says it adds coverage."""
    from aurantium.providers.news import RSS_FEEDS

    with_feeds = {lang for *_, lang in RSS_FEEDS}
    offered = {code for code, *_ in languages.LANGUAGES}
    assert offered - with_feeds == set()


def test_codes_are_unique():
    codes = [code for code, *_ in languages.LANGUAGES]
    assert len(codes) == len(set(codes))


# -- script gate (absolute) -------------------------------------------------


def test_mandarin_is_rejected_for_an_english_reader():
    """The original bug, pinned."""
    assert languages.script_accepted("英伟达股价上涨", ["en"]) is False


@pytest.mark.parametrize(
    "text",
    [
        "日経平均株価が上昇",  # Japanese
        "삼성전자 주가 상승",  # Korean
        "Акции Газпрома выросли",  # Russian
        "ارتفاع أسهم أرامكو",  # Arabic
    ],
)
def test_other_scripts_rejected_for_an_english_reader(text):
    assert languages.script_accepted(text, ["en"]) is False


def test_selected_script_is_accepted():
    assert languages.script_accepted("英伟达股价上涨", ["en", "zh"]) is True


def test_latin_tickers_inside_foreign_text_do_not_rescue_it():
    """Chinese coverage of a US ticker still contains 'NVDA'. Judging on the
    presence of Latin characters would let every such headline through."""
    assert languages.script_accepted("NVDA 英伟达股价上涨 5%", ["en"]) is False


def test_foreign_name_inside_an_english_headline_is_kept():
    """The mirror image, and why the rule isn't simply "reject on any foreign
    character": an English headline citing a Chinese company's name in Han is
    still an English headline."""
    assert languages.script_accepted(
        "Alibaba (阿里巴巴) shares rise on cloud growth", ["en"]
    ) is True


def test_latin_heavy_chinese_headline_is_rejected():
    """Regression, caught against live feeds. Market headlines are stuffed with
    Latin tickers and figures, so a Chinese headline about a US company can
    carry *more* Latin characters than Han — a dominance rule shows it to an
    English-only reader."""
    for title in (
        "Magnetar Financial出售CoreWeave约3,340万美元股票，持有10%所有权",
        "AJB Investment Fund II斥资$49,299增持Jewett Cameron股份",
    ):
        assert languages.script_accepted(title, ["en"]) is False, title


def test_latin_heavy_russian_headline_is_rejected():
    title = "AJB Investment Fund II купил акции Jewett Cameron на $49 299"
    assert languages.script_accepted(title, ["en"]) is False
    assert languages.script_accepted(title, ["en", "ru"]) is True


def test_latin_language_accepts_latin_text():
    assert languages.script_accepted("Petrobras anuncia plano", ["en", "pt"]) is True


def test_digits_and_punctuation_alone_are_not_evidence():
    assert languages.script_accepted("+5.2% (2026)", ["en"]) is True


def test_han_alone_is_ambiguous_between_chinese_and_japanese():
    """Han-only text genuinely could be either, so accepting it for either is
    correct — over-rejecting here would hide real stories."""
    assert languages.script_accepted("株価", ["ja"]) is True
    assert languages.script_accepted("株価", ["zh"]) is True


def test_kana_settles_it_even_as_a_minority_of_the_text():
    """Kana appears in Japanese and nowhere else, so any amount of it is proof
    — it does not have to win the character count."""
    assert languages.script_accepted("ソニーの株価収益率動向分析", ["zh"]) is False
    assert languages.script_accepted("ソニーの株価収益率動向分析", ["ja"]) is True


def test_hangul_settles_it_for_korean():
    assert languages.script_accepted("삼성전자 株価", ["zh"]) is False
    assert languages.script_accepted("삼성전자 株価", ["ko"]) is True


# -- statistical gate (conservative) ---------------------------------------


def test_short_english_headline_survives():
    """langdetect scores this en 0.57 / id 0.29 — a naive top-guess filter
    would drop it. It must not."""
    assert languages.detect_accepted(
        "Nvidia shares jump on strong AI demand", ["en"]
    ) is True


def test_confident_foreign_latin_text_is_rejected():
    text = "La bolsa espanola cierra en verde tras el dato de inflacion mensual"
    assert languages.detect_accepted(text, ["en"]) is False
    assert languages.detect_accepted(text, ["en", "es"]) is True


def test_very_short_text_is_never_rejected():
    assert languages.detect_accepted("Fed holds", ["pt"]) is True


def test_missing_detector_fails_open(monkeypatch):
    """If langdetect is absent from a build, news must keep working — the
    script gate still covers the case that actually prompted this feature."""
    monkeypatch.setattr(languages, "_detector", lambda: None)
    assert languages.detect_accepted("La bolsa espanola cierra en verde", ["en"]) is True


# -- item-level gate --------------------------------------------------------


def test_declared_language_outside_the_selection_is_dropped():
    item = {"title": "Something", "lang": "zh"}
    assert languages.accepts_item(item, ["en", "pt"]) is False


def test_declared_language_is_not_second_guessed():
    """A source we asked for English answered in English. Re-running a shaky
    detector over a short headline could only manufacture a false reject."""
    item = {"title": "Fed holds rates steady", "lang": "en"}
    assert languages.accepts_item(item, ["en"]) is True


def test_declared_language_still_faces_the_script_check():
    """Google News' English edition does occasionally return Han-script items;
    a truthful-looking tag must not be a bypass."""
    item = {"title": "英伟达股价上涨", "lang": "en"}
    assert languages.accepts_item(item, ["en"]) is False


def test_untagged_item_is_judged_on_its_text():
    """yfinance declares no language."""
    assert languages.accepts_item({"title": "英伟达股价上涨"}, ["en"]) is False
    assert languages.accepts_item({"title": "Nvidia shares jump"}, ["en"]) is True


def test_filter_preserves_order():
    items = [
        {"title": "First", "lang": "en"},
        {"title": "英伟达股价上涨", "lang": "zh"},
        {"title": "Second", "lang": "en"},
    ]
    kept = languages.filter_items(items, ["en"])
    assert [i["title"] for i in kept] == ["First", "Second"]


# -- stored preference ------------------------------------------------------


def test_main_language_leads_the_spoken_list():
    languages.set_languages("pt", ["en", "es"])
    assert languages.spoken_languages()[0] == "pt"
    assert set(languages.spoken_languages()) == {"pt", "en", "es"}


def test_main_is_never_duplicated():
    languages.set_languages("en", ["en", "en", "fr"])
    assert languages.spoken_languages() == ["en", "fr"]


def test_unknown_codes_are_ignored():
    languages.set_languages("en", ["klingon", "fr"])
    assert languages.spoken_languages() == ["en", "fr"]


def test_empty_selection_still_yields_the_main_language():
    """A user who unticks everything gets news, not a blank panel."""
    languages.set_languages("de", [])
    assert languages.spoken_languages() == ["de"]


def test_choice_is_recorded_for_the_first_run_gate():
    assert languages.has_chosen() is False
    languages.set_languages("en", [])
    assert languages.has_chosen() is True
