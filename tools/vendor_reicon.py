"""Vendor the Reicon glyphs aurantium uses into ``aurantium/reicon_icons.py``.

Reicon (https://reicon.dev, https://github.com/dqev/reicon) is MIT licensed.
Its single source of truth is ``data/icon-data.json``; this script pulls the
Outline weight of each icon named in ``ICONS`` out of it and writes them into a
generated Python module, so the frozen build carries them with no data files
to collect.

Do not hand-edit the generated module. Change ``ICONS`` and re-run:

    .venv/Scripts/python.exe tools/vendor_reicon.py [path/to/icon-data.json]

Without a path the dataset is downloaded from the repository's main branch.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
OUT = APP_ROOT / "aurantium" / "reicon_icons.py"
DATA_URL = "https://raw.githubusercontent.com/dqev/reicon/main/data/icon-data.json"

#: aurantium glyph kind -> Reicon "category/name"
ICONS = {
    "close": "ui/xmark",
    "expand": "arrows-action/maximize",
    "maximize": "newicons/stop3",
    "restore": "newicons/copy3",
    "menu": "ui/menu",
    "link": "text-formatting/link",
    "pin": "map/pin-tack",
    "grip": "ui/reorder",
    "minimize": "newicons/minus3",
    "panels": "newicons/element-plus",
    "settings": "settings/settings2",
    "search": "search/search4",
    "help": "ui/help-circle",
    "plus": "ui/plus",
    "more": "ui/more-h",
    "export-image": "video/image-down",
    "chevron-down": "arrows/chevron-down",
    "chevron-up": "arrows/chevron-up",
    "chevron-left": "arrows/chevron-left",
    "chevron-right": "arrows/chevron-right",
}

LICENSE = """MIT License

Copyright (c) 2025 REICON

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def load(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(DATA_URL, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str]) -> int:
    data = load(argv[1] if len(argv) > 1 else None)
    cats = data["categories"]
    lines = [
        '"""Reicon glyphs (https://reicon.dev), vendored by tools/vendor_reicon.py.',
        "",
        "GENERATED FILE: do not edit. Change ICONS in tools/vendor_reicon.py and",
        "re-run it. Each value is the inner markup of a 24x24 SVG, Outline weight,",
        'painted in ``currentColor``.',
        "",
        "Reicon is distributed under the MIT License, reproduced here:",
        "",
        *LICENSE.splitlines(),
        '"""',
        "",
        "VIEWBOX = 24",
        "",
        "SVG = {",
    ]
    for kind, ref in ICONS.items():
        cat, name = ref.split("/", 1)
        code = cats[cat]["icons"][name]["weights"]["Outline"]["code"]
        lines.append(f"    # {ref}")
        lines.append(f"    {kind!r}: {code!r},")
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(APP_ROOT)} ({len(ICONS)} glyphs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
