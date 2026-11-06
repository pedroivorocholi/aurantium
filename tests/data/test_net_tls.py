"""aurantium.net: the CA bundle is parsed once, and verification stays strict."""
import ssl

import requests
from requests.adapters import HTTPAdapter

from aurantium import net


class _Conn:
    cert_reqs = ca_certs = ca_cert_dir = cert_file = key_file = None


def test_default_verify_uses_the_shared_context_not_the_bundle_path():
    net.install()
    adapter = HTTPAdapter()
    conn = _Conn()
    adapter.cert_verify(conn, "https://example.com/", True, None)
    assert conn.cert_reqs == "CERT_REQUIRED"
    assert conn.ca_certs is None and conn.ca_cert_dir is None  # no per-connection parse
    pool = adapter.poolmanager.connection_from_url("https://example.com/")
    assert pool.conn_kw.get("ssl_context") is net.shared_context()


def test_shared_context_verifies_and_is_built_once():
    ctx = net.shared_context()
    assert ctx is net.shared_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 50  # the bundle is loaded


def test_explicit_verify_choices_keep_requests_handling(tmp_path):
    net.install()
    adapter = HTTPAdapter()
    bundle = tmp_path / "ca.pem"
    bundle.write_text("")
    conn = _Conn()
    adapter.cert_verify(conn, "https://example.com/", str(bundle), None)
    assert conn.ca_certs == str(bundle)
    conn = _Conn()
    adapter.cert_verify(conn, "https://example.com/", False, None)
    assert conn.cert_reqs == "CERT_NONE"


def test_install_is_idempotent():
    net.install()
    fn = HTTPAdapter.cert_verify
    net.install()
    assert HTTPAdapter.cert_verify is fn
