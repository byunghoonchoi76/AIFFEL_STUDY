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


# 장비별 관측 위치(가상): seed_orbat_sample.py의 가상 부대 주둔지 부근
POS = {"tank": (38.022, 127.028), "apc": (38.018, 127.033), "artillery": (37.991, 126.983),
       "bridging": (38.012, 126.962), "truck": (37.982, 127.037), "field_hospital": (38.031, 126.992),
       "command_vehicle": (38.048, 127.002)}


def _row(t: datetime, eq: str, n: int) -> dict:
    lat, lon = POS.get(eq, (38.0, 127.0))
    return {"area_id": "AREA-01", "observed_at": t.isoformat(), "equipment_type": eq, "count": n,
            "lon": lon, "lat": lat, "image_ref": f"SAMPLE-{t:%Y%m%d}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--as-of", default=datetime.now().astimezone().replace(microsecond=0).isoformat())
    ap.add_argument("--csv-only", action="store_true")
    ap.add_argument("--reset", action="store_true", help="등록 전에 AREA-01 표적 삭제")
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
        if a.reset:
            print("삭제:", httpx.delete(f"{a.api}/targets", params={"area_id": "AREA-01"}, timeout=60).json())
        r = httpx.post(f"{a.api}/targets", json=rows, timeout=60)
        print(r.status_code, r.text[:2000])


if __name__ == "__main__":
    main()
