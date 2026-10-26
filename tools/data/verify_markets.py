"""One-off checker for new commodity listings: verifies each candidate's
Yahoo contract symbols quote and its CFTC Socrata market-name pattern
matches exactly one market family. Run manually before extending
``commodities_meta.COMMODITIES`` / ``econ._CFTC_MARKETS``.

Usage: python tools/data/verify_markets.py
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date

HEADERS = {"User-Agent": "Mozilla/5.0 (aurantium market verifier)"}

# (label, root, exchange, months, cftc_pattern)
CANDIDATES = [
    ("Live Cattle", "LE", "CME", "GJMQVZ", "LIVE CATTLE - CHICAGO MERCANTILE EXCHANGE"),
    ("Feeder Cattle", "GF", "CME", "FHJKQUVX", "FEEDER CATTLE - CHICAGO MERCANTILE EXCHANGE"),
    ("Lean Hogs", "HE", "CME", "GJKMNQVZ", "LEAN HOGS%"),
    ("Orange Juice", "OJ", "NYB", "FHKNUX", "FRZN CONCENTRATED ORANGE JUICE%"),
    ("KC Wheat (HRW)", "KE", "CBT", "HKNUZ", "WHEAT-HRW - CHICAGO BOARD OF TRADE"),
    ("Oats", "ZO", "CBT", "HKNUZ", "OATS - CHICAGO BOARD OF TRADE"),
    ("Soybean Oil", "ZL", "CBT", "FHKNQUVZ", "SOYBEAN OIL - CHICAGO BOARD OF TRADE"),
    ("Soybean Meal", "ZM", "CBT", "FHKNQUVZ", "SOYBEAN MEAL - CHICAGO BOARD OF TRADE"),
    ("Rough Rice", "ZR", "CBT", "FHKNUX", "ROUGH RICE - CHICAGO BOARD OF TRADE"),
    ("Aluminum", "ALI", "CMX", "FGHJKMNQUVXZ", "ALUMINUM MWP%"),
]

MONTH_CODES = "FGHJKMNQUVXZ"


def next_contracts(root: str, exchange: str, months: str, count: int = 2):
    today = date.today()
    year, month = today.year, today.month
    out = []
    while len(out) < count:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        code = MONTH_CODES[month - 1]
        if code not in months:
            continue
        out.append(f"{root}{code}{year % 100:02d}.{exchange}")
    return out


def fetch_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_yahoo(symbol: str) -> str:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
    try:
        data = fetch_json(url)
        result = (data.get("chart") or {}).get("result")
        if not result:
            return f"NO DATA ({(data.get('chart') or {}).get('error')})"
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        return f"ok price={price}" if price is not None else "ok (no price)"
    except Exception as exc:  # noqa: BLE001 - report anything
        return f"ERROR {exc}"


def check_cftc(pattern: str) -> str:
    where = f"market_and_exchange_names like '{pattern}'"
    url = (
        "https://publicreporting.cftc.gov/resource/6dca-aqww.json?"
        + urllib.parse.urlencode(
            {
                "$select": "market_and_exchange_names,report_date_as_yyyy_mm_dd",
                "$where": where,
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": "5",
            }
        )
    )
    try:
        rows = fetch_json(url)
        if not rows:
            return "NO MATCH"
        names = sorted({r["market_and_exchange_names"] for r in rows})
        latest = rows[0]["report_date_as_yyyy_mm_dd"][:10]
        return f"ok latest={latest} names={names}"
    except Exception as exc:  # noqa: BLE001 - report anything
        return f"ERROR {exc}"


def main() -> None:
    for label, root, exchange, months, pattern in CANDIDATES:
        print(f"== {label} ({root}.{exchange}) ==")
        for sym in next_contracts(root, exchange, months):
            print(f"  yahoo {sym}: {check_yahoo(sym)}")
        print(f"  cftc  {pattern!r}: {check_cftc(pattern)}")


if __name__ == "__main__":
    main()
