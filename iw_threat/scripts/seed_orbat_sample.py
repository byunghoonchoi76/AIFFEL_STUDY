"""가상 전투서열 샘플 생성·적재 (AREA-01 주변, 실제 부대와 무관).
사용: python scripts/seed_orbat_sample.py --api http://localhost:8080
산출: data/orbat/sample/records.csv (파서 산출물과 같은 형식) → /orbat/load 로 적재
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402
import mgrs as _mgrs  # noqa: E402

from app.orbat.patterns import echelon_of, unit_key  # noqa: E402
from app.orbat.records import OrbatRecord, to_rows  # noqa: E402

# (부대명, 상급부대, 위도, 경도, 위치유형, 장비, 수량)
UNITS = [
    ("제9001가상보병사단", None, 38.050, 127.000, "사령부", "지휘차량", "6"),
    ("제9002가상전차여단", "제9001가상보병사단", 38.020, 127.030, "주둔지", "전차", "40"),
    ("제9003가상포병연대", "제9001가상보병사단", 37.990, 126.980, "주둔지", "야포", "18"),
    ("제9004가상공병대대", "제9001가상보병사단", 38.010, 126.960, "주둔지", "도하장비", "4"),
    ("제9005가상수송대대", "제9001가상보병사단", 37.980, 127.040, "주둔지", "트럭", "60"),
    ("제9006가상의무대대", "제9001가상보병사단", 38.030, 126.990, "주둔지", "야전병원", "2"),
]


def build() -> list[dict]:
    conv = _mgrs.MGRS()
    recs = []
    for name, parent, lat, lon, loc, eq, cnt in UNITS:
        m = conv.toMGRS(lat, lon, MGRSPrecision=5)
        r = OrbatRecord("sample_orbat", 1, "table", name, unit_key(name), echelon_of(name), "name",
                        parent_raw=parent, parent_key=unit_key(parent) if parent else None,
                        mgrs=m, lat=lat, lon=lon, mgrs_precision_m=1, location_type=loc,
                        equipment=eq, count=cnt, as_of="2026-11", quote="가상 전투서열 샘플")
        recs.append(r)
    return to_rows(recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--csv-only", action="store_true")
    a = ap.parse_args()
    rows = build()
    out = Path(__file__).resolve().parents[1] / "data/orbat/sample/records.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"CSV: {out} ({len(rows)}개 부대)")
    if not a.csv_only:
        with out.open("rb") as f:
            r = httpx.post(f"{a.api}/orbat/load", files={"file": ("records.csv", f)}, data={"mode": "auto"}, timeout=120)
        print(r.status_code, r.text[:1000])


if __name__ == "__main__":
    main()
