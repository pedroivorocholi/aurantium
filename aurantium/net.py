"""One TLS trust store for every HTTPS request the app makes.

requests hands urllib3 the CA bundle *path* on every new connection, and urllib3
then calls ``SSLContext.load_verify_locations`` on it: about half a second of
OpenSSL 3 parsing, during which the GIL is held. Nearly every call site uses a
bare ``requests.get`` (a fresh session, so a fresh connection), and a cold start
fires dozens at once — enough back-to-back GIL holds to starve the UI thread
for minutes. Here the bundle is parsed once into a shared context and requests'
adapter is taught to use it, so a connection costs a handshake, not a parse.

Only ``verify=True`` (the default) is touched, direct or through a proxy:
same bundle, CERT_REQUIRED, hostname checked. ``verify=False`` and
``verify=<path>`` get no shared context and keep requests' own handling, so the
shared trust store is never written to.
"""
from __future__ import annotations

import ssl
import threading
from typing import Any, Optional

_lock = threading.Lock()
_ctx: Optional[ssl.SSLContext] = None
_installed = False


def shared_context() -> ssl.SSLContext:
    """The app's TLS context, with the CA bundle parsed once (lazily)."""
    global _ctx
    with _lock:
        if _ctx is None:
            from requests.utils import DEFAULT_CA_BUNDLE_PATH

            _ctx = ssl.create_default_context(cafile=DEFAULT_CA_BUNDLE_PATH)
        return _ctx


def _default_verify(url: str, verify: Any) -> bool:
    return verify is True and url.lower().startswith("https")


def install() -> None:
    """Patch requests' HTTPAdapter to use :func:`shared_context`. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True

    from requests.adapters import HTTPAdapter

    orig_key_attrs = HTTPAdapter.build_connection_pool_key_attributes
    orig_cert_verify = HTTPAdapter.cert_verify

    def build_connection_pool_key_attributes(self, request: Any, verify: Any, cert: Any = None):
        # the one place both direct and proxied pools take their TLS settings
        host_params, pool_kwargs = orig_key_attrs(self, request, verify, cert)
        if _default_verify(request.url, verify):
            pool_kwargs["ssl_context"] = shared_context()
        return host_params, pool_kwargs

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        orig_cert_verify(self, conn, url, verify, cert)
        if _default_verify(url, verify):
            # the shared context already trusts the bundle; naming it again
            # is what makes urllib3 re-parse it per connection
            conn.ca_certs = None
            conn.ca_cert_dir = None

    HTTPAdapter.build_connection_pool_key_attributes = build_connection_pool_key_attributes
    HTTPAdapter.cert_verify = cert_verify
