"""Generate the Ed25519 key pair WinSparkle uses to sign and verify updates.

Run this ONCE, ever:

    python tools/gen_keys.py

- The PRIVATE key is written to ``tools/eddsa_private.key`` (base64). Keep it
  secret and back it up — anyone with it can sign updates your users will
  install. It is gitignored; never commit it.
- The PUBLIC key is printed. Paste it into ``findash/updater.py`` as
  ``EDDSA_PUBLIC_KEY``. It is safe to publish.

Requires ``cryptography`` (``pip install -r requirements-dev.txt``).
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PRIVATE_KEY_FILE = Path(__file__).resolve().parent / "eddsa_private.key"


def main() -> int:
    if PRIVATE_KEY_FILE.exists():
        print(f"A key already exists at {PRIVATE_KEY_FILE}.")
        print(
            "Refusing to overwrite it — generating a new key would invalidate "
            "every update your current users can verify.\n"
            "Delete the file manually only if you are certain."
        )
        return 1

    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    PRIVATE_KEY_FILE.write_text(base64.b64encode(priv_raw).decode())

    print("Ed25519 key pair generated.\n")
    print(f"  Private key -> {PRIVATE_KEY_FILE}")
    print("  (KEEP SECRET - BACK UP - never commit; it's gitignored)\n")
    print("Paste this line into findash/updater.py:\n")
    print(f'    EDDSA_PUBLIC_KEY = "{base64.b64encode(pub_raw).decode()}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
