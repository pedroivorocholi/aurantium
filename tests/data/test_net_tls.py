"""aurantium.net: the CA bundle is parsed once, and verification stays strict."""
import ssl

import requests
from requests.adapters import HTTPAdapter

from aurantium import net


def _pool(verify, url="https://example.com/", proxies=None):
    adapter = HTTPAdapter()
    req = requests.Request("GET", url).prepare()
    conn = adapter.get_connection_with_tls_context(req, verify, proxies=proxies)
    adapter.cert_verify(conn, url, verify, None)
    return conn


def test_default_verify_uses_the_shared_context_not_the_bundle_path():
    net.install()
    conn = _pool(True)
    assert conn.conn_kw.get("ssl_context") is net.shared_context()
    assert conn.cert_reqs == "CERT_REQUIRED"
    assert conn.ca_certs is None and conn.ca_cert_dir is None  # no per-connection parse


def test_a_proxied_request_uses_the_shared_context_too():
    net.install()
    conn = _pool(True, proxies={"https": "http://127.0.0.1:9"})
    assert conn.conn_kw.get("ssl_context") is net.shared_context()


def test_shared_context_verifies_and_is_built_once():
    ctx = net.shared_context()
    assert ctx is net.shared_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 50  # the bundle is loaded


def test_explicit_verify_choices_keep_requests_handling(tmp_path):
    net.install()
    ctx = net.shared_context()
    before = ctx.cert_store_stats()
    bundle = tmp_path / "ca.pem"
    bundle.write_text("")
    conn = _pool(str(bundle))
    assert conn.ca_certs == str(bundle)
    assert conn.conn_kw.get("ssl_context") is not ctx  # never written into
    conn = _pool(False)  # used to raise: CERT_NONE on a hostname-checking context
    assert conn.cert_reqs == "CERT_NONE"
    assert conn.conn_kw.get("ssl_context") is not ctx
    assert ctx.cert_store_stats() == before


def test_install_is_idempotent():
    net.install()
    fn = HTTPAdapter.build_connection_pool_key_attributes
    net.install()
    assert HTTPAdapter.build_connection_pool_key_attributes is fn
