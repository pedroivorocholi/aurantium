"""Throwaway: probe every rates upstream, record fixtures, report shapes.

Run:  .venv/Scripts/python.exe tools/probe_rates.py
Writes tests/fixtures/rates/*.  Delete this script once fixtures are recorded
and tools/verify_rates.py covers ongoing verification.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "rates"

# (name, [candidate urls], suffix)
CANDIDATES = [
    (
        "bis_cbpol",
        [
            "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/M..?format=csv&lastNObservations=24",
            "https://stats.bis.org/api/v1/data/WS_CBPOL/M../all?format=csv",
            "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/all?format=csv&lastNObservations=24",
        ],
        "csv",
    ),
    (
        "ecb_yc_10y",
        [
            "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?format=csvdata&lastNObservations=1",
        ],
        "csv",
    ),
    (
        "ust_curve",
        [
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/daily_treasury_yield_curve?sort=-record_date&page[size]=1",
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv",
        ],
        "raw",
    ),
    (
        "mof_jgb",
        [
            "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv",
            "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcme.csv",
        ],
        "csv",
    ),
]

HEADERS = {"User-Agent": "aurantium-probe/1.0"}


def probe(name: str, urls: list[str], suffix: str) -> dict:
    for url in urls:
        try:
            resp = requests.get(url, timeout=20, headers=HEADERS)
        except Exception as exc:
            print(f"  {url}\n    ERROR {exc}")
            continue
        body = resp.text or ""
        print(f"  {url}\n    HTTP {resp.status_code}, {len(body)} bytes")
        if resp.status_code == 200 and body.strip():
            ext = "json" if body.lstrip().startswith(("{", "[")) else suffix
            path = FIXTURES / f"{name}.{ext}"
            path.write_text(body, encoding="utf-8")
            print(f"    -> recorded {path.name}")
            return {"name": name, "url": url, "bytes": len(body), "file": path.name}
    return {"name": name, "url": None, "bytes": 0, "file": None}


def probe_fred() -> dict:
    """FRED needs a key. Records one international series plus its metadata,
    so Task 4 can see exactly what a `notes` field looks like."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("  FRED_API_KEY not set - skipping (Task 4 will need it)")
        return {"name": "fred", "url": None, "bytes": 0, "file": None}
    out = {}
    for label, url, params in [
        (
            "fred_series",
            "https://api.stlouisfed.org/fred/series",
            {"series_id": "IRLTLT01GBM156N", "api_key": key, "file_type": "json"},
        ),
        (
            "fred_observations",
            "https://api.stlouisfed.org/fred/series/observations",
            {
                "series_id": "IRLTLT01GBM156N",
                "api_key": key,
                "file_type": "json",
                "limit": 12,
                "sort_order": "desc",
            },
        ),
    ]:
        resp = requests.get(url, params=params, timeout=20, headers=HEADERS)
        print(f"  {label}: HTTP {resp.status_code}, {len(resp.text)} bytes")
        if resp.status_code == 200:
            # scrub the key out of anything we write to disk
            (FIXTURES / f"{label}.json").write_text(resp.text, encoding="utf-8")
            out[label] = len(resp.text)
    return {"name": "fred", "url": "api.stlouisfed.org", "bytes": sum(out.values()), "file": "fred_*.json"}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    results = []
    for name, urls, suffix in CANDIDATES:
        print(f"\n=== {name} ===")
        results.append(probe(name, urls, suffix))
    print("\n=== fred ===")
    results.append(probe_fred())

    print("\n\n=== SUMMARY ===")
    failed = []
    for r in results:
        status = "OK  " if r["url"] else "FAIL"
        print(f"{status} {r['name']:16} {r['file'] or '-'}")
        if not r["url"]:
            failed.append(r["name"])
    print(json.dumps(results, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
