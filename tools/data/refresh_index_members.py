"""Regenerate the index member lists in ``aurantium/heatmap_universes.py``.

Scrapes the public Wikipedia constituents tables (S&P 500, Dow 30, FTSE 100,
DAX, CAC 40, Euro Stoxx 50, Hang Seng, S&P/TSX 60; the Nikkei 225 from the
Japanese article, whose tables carry the stock codes). Two lists come from the
index owner instead, because Wikipedia has no current ticker table for them:
the Nasdaq-100 from Nasdaq's public list endpoint and the Ibovespa from B3's
public portfolio endpoint. Rewrites the block between the ``BEGIN MEMBERS`` /
``END MEMBERS`` markers, stamping ``MEMBERS_AS_OF``.

Every symbol is written in Yahoo's spelling: share classes take a dash
(BRK.B -> BRK-B, RCI.B -> RCI-B.TO, BT.A -> BT-A.L), each exchange its suffix
(.SA .L .DE .PA .T .HK .TO ...), and Hong Kong codes are padded to four digits
(SEHK: 700 -> 0700.HK). Run on each release::

    .venv/Scripts/python.exe tools/data/refresh_index_members.py

Needs network, plus pandas and lxml (dev venv only; the app does not).
Refuses to write anything if a list looks wrong, so a changed page layout
cannot silently ship an empty or truncated universe.
"""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

TARGET = Path(__file__).resolve().parents[2] / "aurantium" / "heatmap_universes.py"
UA = "aurantium-tools/1.0 (index member refresh)"

_SYMBOL_COLS = ("symbol", "ticker")


def normalise(sym: str) -> str:
    return re.sub(r"[^A-Z0-9-]", "", str(sym).strip().upper().replace(".", "-"))


def _yahoo(sym: str) -> str:
    """A symbol already in Yahoo's form (``ADS.DE``, ``NDA-FI.HE``): tidied only."""
    return re.sub(r"[^A-Z0-9.-]", "", str(sym).strip().upper())


def _local(suffix: str):
    """Exchange ticker -> Yahoo: class dot to dash, trailing dot dropped, suffix."""
    def to_yahoo(sym: str) -> str:
        base = normalise(str(sym).strip().rstrip("."))
        return base + suffix if base else ""
    return to_yahoo


def _hong_kong(sym: str) -> str:
    digits = re.sub(r"\D", "", str(sym))
    return f"{int(digits):04d}.HK" if digits else ""


def _get(url: str, browser: bool = False) -> bytes:
    ua = "Mozilla/5.0 " + UA if browser else UA
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=30).read()


def _unique(syms) -> tuple[str, ...]:
    return tuple(dict.fromkeys(s for s in syms if s))


def fetch_members(page: str, map_symbol=normalise, cols=_SYMBOL_COLS,
                  host: str = "en", union: bool = False) -> tuple[str, ...]:
    """The symbol column of a Wikipedia article: the longest such table, or
    (``union``) every such table in page order, for articles that split the
    constituents by sector."""
    html = _get(f"https://{host}.wikipedia.org/wiki/{page}").decode("utf-8")
    best: list[str] = []
    every: list[str] = []
    # keep_default_na=False: pandas otherwise reads the ticker "NA" (National
    # Bank of Canada) as a missing value and the row silently disappears
    for table in pd.read_html(io.StringIO(html), keep_default_na=False):
        names = {str(c).strip().lower(): c for c in table.columns}
        col = next((names[c.lower()] for c in cols if c.lower() in names), None)
        if col is None:
            continue
        syms = [map_symbol(s) for s in table[col] if str(s).strip()]
        syms = [s for s in syms if s]
        every += syms
        if len(syms) > len(best):
            best = syms
    return _unique(every if union else best)


def fetch_nasdaq100() -> tuple[str, ...]:
    req = urllib.request.Request(
        "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    rows = json.load(urllib.request.urlopen(req, timeout=30))["data"]["data"]["rows"]
    return _unique(normalise(r["symbol"]) for r in rows if r.get("symbol"))


def fetch_ibovespa() -> tuple[str, ...]:
    """Today's Ibovespa portfolio from B3, the index owner."""
    query = {"language": "en-us", "pageNumber": 1, "pageSize": 200,
             "index": "IBOV", "segment": "1"}
    token = base64.b64encode(json.dumps(query).encode()).decode()
    url = ("https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/"
           "GetPortfolioDay/" + token)
    rows = json.loads(_get(url, browser=True))["results"]
    return _unique(_local(".SA")(r["cod"]) for r in rows if r.get("cod"))


# name -> (fetch, accepted member-count range); written in this order
SOURCES = {
    "_SP500": (lambda: fetch_members("List_of_S%26P_500_companies"), (480, 520)),
    # not on Wikipedia any more: Nasdaq's own list
    "_NDX": (fetch_nasdaq100, (95, 110)),
    "_DOW": (lambda: fetch_members("List_of_Dow_Jones_Industrial_Average_companies"),
             (30, 30)),
    # no current ticker table on Wikipedia: B3's own portfolio
    "_IBOV": (fetch_ibovespa, (70, 95)),
    "_FTSE": (lambda: fetch_members("FTSE_100_Index", _local(".L"), ("Ticker",)),
              (95, 105)),
    "_DAX": (lambda: fetch_members("DAX", _yahoo, ("Ticker",)), (40, 40)),
    "_CAC": (lambda: fetch_members("CAC_40", _yahoo, ("Ticker",)), (40, 40)),
    "_SX5E": (lambda: fetch_members("EURO_STOXX_50", _yahoo, ("Ticker",)), (50, 50)),
    # the Japanese article: its sector tables carry the four-character codes
    "_N225": (lambda: fetch_members(
        "%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87%E6%A0%AA%E4%BE%A1", _local(".T"),
        ("証券コード",), host="ja", union=True), (225, 225)),
    "_HSI": (lambda: fetch_members("Hang_Seng_Index", _hong_kong, ("Ticker",)), (70, 105)),
    "_TSX60": (lambda: fetch_members("S%26P/TSX_60", _local(".TO"), ("Symbol",)),
               (60, 60)),
}


def render(stamp: str, lists: dict[str, tuple[str, ...]]) -> str:
    out = [
        "# BEGIN MEMBERS (generated by tools/data/refresh_index_members.py)",
        f'MEMBERS_AS_OF = "{stamp}"',
    ]
    for name, members in lists.items():
        out.append(f"{name}: tuple[str, ...] = (")
        line = "   "
        for s in members:
            item = f' "{s}",'
            if len(line) + len(item) > 88:
                out.append(line)
                line = "   "
            line += item
        out.append(line)
        out.append(")")
    out.append("# END MEMBERS")
    return "\n".join(out)


def main() -> int:
    lists = {}
    for name, (fetch, (lo, hi)) in SOURCES.items():
        try:
            members = fetch()
        except Exception as exc:  # network / parse
            print(f"BLOCKED: could not fetch {name}: {exc}", file=sys.stderr)
            return 1
        if not lo <= len(members) <= hi:
            print(f"BLOCKED: {name} has {len(members)} members, expected {lo}-{hi}",
                  file=sys.stderr)
            return 1
        lists[name] = members
        print(f"{name}: {len(members)} members")
    text = TARGET.read_text(encoding="utf-8")
    block = render(date.today().isoformat(), lists)
    new, n = re.subn(r"# BEGIN MEMBERS.*?# END MEMBERS", lambda _m: block, text, flags=re.S)
    if n != 1:
        print("BLOCKED: member markers not found in heatmap_universes.py", file=sys.stderr)
        return 1
    TARGET.write_text(new, encoding="utf-8", newline="\n")
    print("wrote", TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
