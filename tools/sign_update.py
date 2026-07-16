"""Sign a release file for the WinSparkle appcast.

    python tools/sign_update.py dist/findash-setup.exe

Prints the file length and the base64 EdDSA signature. Copy both into
``appcast.xml`` on the matching ``<enclosure>`` (``length`` and
``sparkle:edSignature``). WinSparkle verifies this signature with the public
key embedded in the app before running any downloaded installer.

Requires ``tools/eddsa_private.key`` (see ``gen_keys.py``) and ``cryptography``.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PRIVATE_KEY_FILE = Path(__file__).resolve().parent / "eddsa_private.key"


def sign(path: Path) -> tuple[int, str]:
    """Return (length, base64 Ed25519 signature) for ``path``."""
    priv_raw = base64.b64decode(PRIVATE_KEY_FILE.read_text().strip())
    priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
    data = path.read_bytes()
    return len(data), base64.b64encode(priv.sign(data)).decode()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python tools/sign_update.py <file>")
        return 2
    target = Path(argv[0])
    if not target.is_file():
        print(f"No such file: {target}")
        return 1
    if not PRIVATE_KEY_FILE.is_file():
        print(f"Missing private key: {PRIVATE_KEY_FILE}")
        print("Run  python tools/gen_keys.py  first.")
        return 1

    length, signature = sign(target)
    print(f"file:    {target}")
    print(f"length:  {length}")
    print(f'sparkle:edSignature="{signature}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
