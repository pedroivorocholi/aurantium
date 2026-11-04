"""The in-app updater: appcast parsing, signature checks, and the update card.

The card's whole promise is one click: it only appears once the update is
downloaded and verified, lists what's new, and its single button asks to
install that exact download.
"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aurantium import updater

APPCAST = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <item>
      <title>Aurantium 9.1.0</title>
      <description><![CDATA[
        <style> body { color:#141414; } </style>
        <h3>9.1.0</h3>
        <ul>
          <li>One News panel &amp; <b>Portfolio</b> news</li>
          <li>Topic &quot;Brazil&quot;
              keeps its query</li>
        </ul>
      ]]></description>
      <enclosure url="https://example.test/v9.1.0/aurantium-setup.exe"
        sparkle:version="9.1.0" sparkle:os="windows"
        sparkle:installerArguments="/VERYSILENT /NORESTART"
        length="4" sparkle:edSignature="c2ln" />
    </item>
    <item>
      <title>Aurantium 9.1.0</title>
      <description><![CDATA[<ul><li>mac notes</li></ul>]]></description>
      <enclosure url="https://example.test/v9.1.0/aurantium-mac.zip"
        sparkle:version="9.1.0" sparkle:os="macos"
        length="4" sparkle:edSignature="c2ln" />
    </item>
    <item>
      <title>Aurantium 9.0.0</title>
      <description><![CDATA[<ul><li>old</li></ul>]]></description>
      <enclosure url="https://example.test/v9.0.0/aurantium-setup.exe"
        sparkle:version="9.0.0" sparkle:os="windows" sparkle:edSignature="c2ln" />
    </item>
  </channel>
</rss>
"""


# -- appcast ----------------------------------------------------------------


def test_find_item_picks_newest_for_the_os():
    item = updater.find_item(APPCAST, "windows")
    assert item["version"] == "9.1.0"
    assert item["url"].endswith("aurantium-setup.exe")
    assert item["arguments"] == "/VERYSILENT /NORESTART"
    assert item["length"] == 4
    assert updater.find_item(APPCAST, "macos")["notes"] == ["mac notes"]


def test_find_item_none_without_a_matching_enclosure():
    assert updater.find_item(APPCAST, "linux") is None


def test_release_notes_are_plain_list_items():
    notes = updater.find_item(APPCAST, "windows")["notes"]
    assert notes == ['One News panel & Portfolio news', 'Topic "Brazil" keeps its query']


def test_release_notes_without_a_list_fall_back_to_lines():
    assert updater.release_notes("<h3>1.0</h3>First<br>Second") == ["First", "Second"]
    assert updater.release_notes("") == []


def test_parse_version_orders_numerically():
    assert updater.parse_version("1.10.0") > updater.parse_version("1.9.9")


# -- signatures ---------------------------------------------------------------


def _keypair():
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return key, base64.b64encode(pub).decode()


def test_verify_accepts_the_signed_bytes_only():
    key, pub = _keypair()
    sig = base64.b64encode(key.sign(b"installer")).decode()
    assert updater.verify(b"installer", sig, pub)
    assert not updater.verify(b"tampered", sig, pub)
    assert not updater.verify(b"installer", "not-base64!", pub)


@pytest.fixture
def signed(monkeypatch, tmp_path):
    """An item whose download is served from memory and signed by a test key."""
    key, pub = _keypair()
    real_verify = updater.verify
    monkeypatch.setattr(updater, "verify", lambda d, s: real_verify(d, s, pub))
    monkeypatch.setattr(updater, "DOWNLOAD_DIR", tmp_path)
    served = {"body": b"setup-bytes", "calls": 0}

    class _Resp:
        def __init__(self, body):
            self.content = body

        def raise_for_status(self):
            pass

    def fake_get(url, timeout):
        served["calls"] += 1
        return _Resp(served["body"])

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    item = {
        "version": "9.1.0",
        "url": "https://example.test/aurantium-setup.exe",
        "signature": base64.b64encode(key.sign(b"setup-bytes")).decode(),
        "length": len(b"setup-bytes"),
    }
    return item, served, tmp_path


def test_download_writes_a_verified_file_and_reuses_it(signed):
    item, served, folder = signed
    path = updater.Updater()._download(item)
    assert path.read_bytes() == b"setup-bytes"
    assert path.parent == folder and "9.1.0" in path.name
    updater.Updater()._download(item)
    assert served["calls"] == 1  # second launch reuses the verified copy


def test_download_rejects_a_bad_signature(signed):
    item, served, folder = signed
    served["body"] = b"setup-bytez"  # same length, different bytes
    with pytest.raises(ValueError, match="signature"):
        updater.Updater()._download(item)
    assert not any(folder.iterdir())  # nothing unverified is left behind


# -- the card -----------------------------------------------------------------


@pytest.fixture
def card(qapp):
    from PySide6.QtWidgets import QWidget

    from aurantium.update_card import UpdateCard

    host = QWidget()
    host.resize(1200, 800)
    host.show()
    c = UpdateCard(host)
    yield c
    host.close()
    host.deleteLater()


ITEM = {
    "version": "9.1.0",
    "notes": ["One News panel", "Portfolio <news>"],
    "path": "C:/tmp/setup.exe",
}


def test_card_lists_whats_new_and_offers_one_button(card):
    from PySide6.QtWidgets import QPushButton

    card.show_update(ITEM)
    assert not card.isHidden()
    assert card._title.text() == "Aurantium 9.1.0"
    assert "One News panel" in card._notes.text()
    assert "Portfolio &lt;news&gt;" in card._notes.text()  # notes are escaped
    buttons = card.findChildren(QPushButton)
    assert len(buttons) == 1 and buttons[0].text() == "Update && restart"


def test_one_click_requests_that_exact_install(card):
    seen = []
    card.install_requested.connect(seen.append)
    card.show_update(ITEM)
    card._button.click()
    assert seen == [ITEM]
    assert not card._button.isEnabled()  # no double-launch while it starts


def test_failed_install_can_be_retried(card):
    card.show_update(ITEM)
    card._button.click()
    card.install_failed("access denied")
    assert card._button.isEnabled() and card._button.text() == "Try again"
    assert "access denied" in card._error.text()


def test_card_pops_up_centred_over_the_window(card):
    card.show_update(ITEM)
    card.reposition()
    host = card.parentWidget()
    assert abs(card.geometry().center().x() - host.width() // 2) <= 1
    assert abs(card.geometry().center().y() - host.height() // 2) <= 1
