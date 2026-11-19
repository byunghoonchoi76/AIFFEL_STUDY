"""가상 시나리오 표적 데이터 생성·등록 (AREA-01: 평시 30일 → 최근 7일 집결).
사용: python scripts/seed_sample.py --api http://localhost:8080
"""
import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

import httpx

BASE = {"tank": 12, "apc": 8, "truck": 30, "artillery": 6}
SURGE = {"tank": 40, "apc": 22, "truck": 70, "artillery": 14, "bridging": 4, "field_hospital": 2, "command_vehicle": 3}


def build(as_of: datetime) -> list[dict]:
    rows = []
    for d in range(37, 7, -3):           # 평시 관측 (기준기간)
        for eq, n in BASE.items():
            rows.append(_row(as_of - timedelta(days=d), eq, n + (d % 3)))
    for d in (5, 2):                      # 최근 창 관측
        for eq, n in SURGE.items():
            rows.append(_row(as_of - timedelta(days=d), eq, n if d == 2 else n // 2))
    return rows


def _row(t: datetime, eq: str, n: int) -> dict:
    return {"area_id": "AREA-01", "observed_at": t.isoformat(), "equipment_type": eq, "count": n,
            "lon": 127.0, "lat": 38.0, "image_ref": f"SAMPLE-{t:%Y%m%d}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--as-of", default=datetime.now().astimezone().replace(microsecond=0).isoformat())
    ap.add_argument("--csv-only", action="store_true")
    a = ap.parse_args()
    rows = build(datetime.fromisoformat(a.as_of))
    out = Path(__file__).resolve().parents[1] / "data/samples/targets_sample.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"CSV: {out} ({len(rows)}행)")
    if not a.csv_only:
        r = httpx.post(f"{a.api}/targets", json=rows, timeout=60)
        print(r.status_code, r.text[:2000])


if __name__ == "__main__":
    main()
