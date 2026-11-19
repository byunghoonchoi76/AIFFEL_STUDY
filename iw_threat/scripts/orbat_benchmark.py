"""전투서열 파서 정확도 실측 — 정답을 아는 가상 전투서열 PDF를 생성해 추출 결과와 비교.
사용: python scripts/orbat_benchmark.py --units 60 --seed 1 [--scan]
  --scan : 쪽을 이미지로 바꿔 스캔 문서처럼 만들어 OCR 경로까지 측정
측정: 부대 식별 재현율, 좌표 정확도(위치 오차), 제대·상급부대 정확도, 처리 시간
※ 부대명·좌표는 모두 가상 (실제 부대와 무관)
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz  # noqa: E402
import mgrs as _mgrs  # noqa: E402

from app.orbat.pdf_extract import extract_pdf  # noqa: E402
from app.orbat.records import _km, build_records  # noqa: E402

KR = "korea"
TYPES = ["가상보병", "가상기계화", "가상전차", "가상포병", "가상공병", "가상정찰"]
SQUARES = ["CH", "DH", "CG", "DG"]          # 52S 구역 내 100km 격자 (한반도 중부)
SYMBOL = {"사단": "XX", "여단": "X", "연대": "III", "대대": "II"}


def gen_units(n: int, rnd: random.Random) -> list[dict]:
    units, num = [], 9000
    divisions = []
    for _ in range(max(2, n // 6)):
        num += 1
        divisions.append({"name": f"제{num}{rnd.choice(TYPES)}사단", "echelon": "사단", "parent": None})
    units += divisions
    while len(units) < n:
        num += 1
        ech = rnd.choice(["여단", "연대", "대대"])
        parent = rnd.choice(divisions if ech != "대대" else units)
        units.append({"name": f"제{num}{rnd.choice(TYPES)}{ech}", "echelon": ech, "parent": parent["name"]})
    for u in units:
        e, nn = rnd.randint(0, 99999), rnd.randint(0, 99999)
        u["mgrs"] = f"52S{rnd.choice(SQUARES)}{e:05d}{nn:05d}"
        u["lat"], u["lon"] = _mgrs.MGRS().toLatLon(u["mgrs"])
    return units


def fmt_mgrs(m: str, rnd: random.Random) -> str:
    z, sq, e, n = m[:3], m[3:5], m[5:10], m[10:]
    return rnd.choice([f"{z} {sq} {e} {n}", m, f"{z}{sq} {e} {n}"])


def make_pdf(units: list[dict], path: Path, rnd: random.Random) -> None:
    doc = fitz.open()
    half = len(units) // 2
    # 1) 서술형 쪽: 부대명+좌표(같은 줄 또는 다음 줄), 단대호 제대 기호
    page, y = doc.new_page(), 60
    for u in units[:half]:
        if y > 760:
            page, y = doc.new_page(), 60
        if rnd.random() < 0.5:
            page.insert_text((50, y), f"{u['name']} 사령부 {fmt_mgrs(u['mgrs'], rnd)} 일대 주둔", fontname=KR, fontsize=9)
            y += 18
        else:
            page.insert_text((50, y), u["name"], fontname=KR, fontsize=9)
            page.insert_text((50, y + 14), f"주둔지 {fmt_mgrs(u['mgrs'], rnd)}", fontname=KR, fontsize=9)
            y += 32
        page.insert_text((470, y - 6), SYMBOL.get(u["echelon"], ""), fontname=KR, fontsize=8)
        y += 8
    # 2) 표 쪽
    hdr = ["부대명", "상급부대", "좌표", "장비", "수량"]
    widths, rh = [130, 130, 110, 70, 45], 18
    xs = [40]
    for w in widths:
        xs.append(xs[-1] + w)
    rows = [[u["name"], u["parent"] or "", u["mgrs"], rnd.choice(["전차", "자주포", "장갑차", ""]), str(rnd.randint(4, 40))]
            for u in units[half:]]
    for start in range(0, len(rows), 35):
        page = doc.new_page()
        for ri, row in enumerate([hdr] + rows[start:start + 35]):
            for ci, val in enumerate(row):
                r = fitz.Rect(xs[ci], 50 + ri * rh, xs[ci + 1], 50 + (ri + 1) * rh)
                page.draw_rect(r, color=(0, 0, 0), width=0.6)
                if val:
                    page.insert_text((r.x0 + 2, r.y1 - 5), val, fontname=KR, fontsize=6.5)
    doc.save(path)


def to_scan(src: Path, dst: Path, dpi: int = 200) -> None:
    with fitz.open(src) as d, fitz.open() as out:
        for p in d:
            pix = p.get_pixmap(dpi=dpi)
            np = out.new_page(width=p.rect.width, height=p.rect.height)
            np.insert_image(np.rect, pixmap=pix)
        out.save(dst)


def key(name: str) -> str:
    return name.replace(" ", "").removeprefix("제")


def evaluate(units: list[dict], recs) -> dict:
    truth = {key(u["name"]): u for u in units}
    found: dict[str, list] = {}
    for r in recs:
        if r.unit_key:
            found.setdefault(r.unit_key, []).append(r)
    hit = [k for k in truth if k in found]
    with_coord = [k for k in hit if any(r.lat is not None for r in found[k])]
    errs = []
    for k in with_coord:
        u = truth[k]
        best = min(_km(type("T", (), {"lat": u["lat"], "lon": u["lon"]})(), r) for r in found[k] if r.lat is not None)
        errs.append(best)
    ech_ok = sum(1 for k in hit if any(r.echelon == truth[k]["echelon"] for r in found[k]))
    par_cand = [k for k in hit if truth[k]["parent"] and any(r.source in ("table", "ocr_row") for r in found[k])]
    par_ok = sum(1 for k in par_cand if any(r.parent_key == key(truth[k]["parent"]) for r in found[k]))
    false_units = [k for k in found if k not in truth]
    n = len(truth)
    return {
        "units_total": n,
        "unit_recall": round(len(hit) / n, 3),
        "coord_recall": round(len(with_coord) / n, 3),
        "coord_within_10m": round(sum(e <= 0.01 for e in errs) / n, 3),
        "coord_err_km_max": round(max(errs), 4) if errs else None,
        "echelon_accuracy": round(ech_ok / max(len(hit), 1), 3),
        "parent_accuracy(table)": round(par_ok / max(len(par_cand), 1), 3),
        "false_units": len(false_units),
        "records_with_issues": sum(1 for r in recs if r.issues),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--out", default="data/orbat/benchmark")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    units = gen_units(a.units, rnd)
    pdf = out / "synthetic_orbat.pdf"
    make_pdf(units, pdf, rnd)
    if a.scan:
        scan = out / "synthetic_orbat_scan.pdf"
        to_scan(pdf, scan)
        pdf = scan
    (out / "ground_truth.json").write_text(json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8")

    t0 = time.perf_counter()
    content = extract_pdf(pdf, image_dir=out / "images")
    recs, conflicts = build_records(content)
    elapsed = time.perf_counter() - t0

    res = {"pdf": str(pdf), "pages": content.pages, "ocr_pages": len(content.ocr_pages),
           "elapsed_s": round(elapsed, 2), "s_per_page": round(elapsed / content.pages, 2),
           **evaluate(units, recs), "conflicts": len(conflicts)}
    (out / "benchmark_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
