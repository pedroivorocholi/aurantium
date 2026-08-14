"""Regenerate aurantium/rates_allowlist.py — the vetted list of FRED series
the rates provider is permitted to fetch.

FRED hosts third-party copyrighted series whose commercial redistribution is
not permitted. They are identifiable: their `notes` field contains the word
"Copyright". This script asks FRED about every series rates_meta.py wants,
drops any that match, and writes the survivors.

Output is a .py module rather than a JSON data file on purpose: there is no
data/ directory in app/, aurantium.spec bundles only ("layouts", "layouts")
and .env.example, so a new data path would need a spec entry whose failure
mode is silent. A module is bundled automatically and fails loudly.

Run:  .venv/Scripts/python.exe tools/verify_rates.py
Re-run as part of release prep.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurantium.rates_meta import COUNTRIES  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "aurantium" / "rates_allowlist.py"
FRED_SERIES = "https://api.stlouisfed.org/fred/series"

HEADER = '''"""GENERATED FILE - do not edit by hand.

Vetted FRED series the rates provider may fetch. Series whose FRED metadata
notes contain "Copyright" are excluded: they are third-party copyrighted and
their commercial redistribution is not permitted.

Regenerate with:  .venv/Scripts/python.exe tools/verify_rates.py
"""

from __future__ import annotations

#: ISO date this list was last verified against FRED
CHECKED = "{checked}"

#: series ids the provider is permitted to request
ALLOWED = frozenset({allowed})
'''


def candidate_series() -> list[str]:
    out: list[str] = []
    for c in COUNTRIES:
        for sid in (c.fred_short, c.fred_long):
            if sid:
                out.append(sid)
    return sorted(set(out))


def is_copyrighted(series_id: str, key: str) -> tuple[bool, str]:
    resp = requests.get(
        FRED_SERIES,
        params={"series_id": series_id, "api_key": key, "file_type": "json"},
        timeout=15,
    )
    if resp.status_code != 200:
        # unknown status = treat as copyrighted. Fail closed.
        return True, f"HTTP {resp.status_code}"
    entries = resp.json().get("seriess", [])
    if not entries:
        return True, "no metadata"
    notes = entries[0].get("notes") or ""
    if "copyright" in notes.lower():
        return True, "notes mention Copyright"
    return False, "clear"


def main() -> int:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("FRED_API_KEY not set. Get a free key at fred.stlouisfed.org.")
        return 2

    allowed, refused = [], []
    for sid in candidate_series():
        blocked, reason = is_copyrighted(sid, key)
        print(f"{'REFUSE' if blocked else 'ALLOW '} {sid:20} {reason}")
        (refused if blocked else allowed).append(sid)

    body = HEADER.format(
        checked=dt.date.today().isoformat(),
        allowed="{\n    " + ",\n    ".join(repr(s) for s in allowed) + ",\n}"
        if allowed
        else "()",
    )
    OUT.write_text(body, encoding="utf-8")

    print(f"\nwrote {OUT.name}: {len(allowed)} allowed, {len(refused)} refused")
    if refused:
        print("\nREFUSED - demote these countries to curve_source='none' in")
        print("rates_meta.py if they now have no usable series:")
        for sid in refused:
            print(f"  {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
