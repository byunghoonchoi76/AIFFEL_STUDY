"""전투서열 PDF 파싱 → 검토용 산출물 (DB 적재 전 단계).
사용: python scripts/parse_orbat_pdf.py 문서.pdf --out data/orbat/문서명
산출: records.csv(후보 레코드), conflicts.csv(충돌), images/(단대호·사진), review.html(원문 위치·이미지 대조), summary.json
※ 비밀 문서는 MS-C931(폐쇄망) 안에서만 실행
"""
import argparse
import csv
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.orbat.pdf_extract import extract_pdf  # noqa: E402
from app.orbat.records import build_records, to_rows  # noqa: E402


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:   # 엑셀에서 한글 깨짐 방지
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_review(path: Path, rows: list[dict], out: Path) -> None:
    cols = ["page", "source", "unit_raw", "echelon", "parent_raw", "mgrs", "lat", "lon", "equipment", "count", "as_of", "issues", "quote"]
    trs = []
    for r in rows:
        imgs = "".join(f'<img src="{html.escape(str(Path(p).relative_to(out)))}" height="56">' for p in r["images"].split(";") if p)
        cls = ' class="warn"' if r["issues"] else ""
        trs.append(f"<tr{cls}>" + "".join(f"<td>{html.escape(str(r[c] if r[c] is not None else ''))}</td>" for c in cols) + f"<td>{imgs}</td></tr>")
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>전투서열 추출 검토</title>"
        "<style>body{font-family:sans-serif;font-size:13px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px;vertical-align:top}"
        "tr.warn{background:#fff3cd}th{background:#eef}</style>"
        f"<h2>전투서열 추출 검토 ({len(rows)}건)</h2><p>노란 행: 확인 필요</p><table><tr>"
        + "".join(f"<th>{c}</th>" for c in cols) + "<th>단대호·사진</th></tr>" + "".join(trs) + "</table>",
        encoding="utf-8",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None)
    ap.add_argument("--image-radius", type=float, default=40, help="부대 줄·표와 이미지 사이 최대 간격(pt)")
    a = ap.parse_args()
    out = Path(a.out or f"data/orbat/{Path(a.pdf).stem}")
    out.mkdir(parents=True, exist_ok=True)

    content = extract_pdf(a.pdf, image_dir=out / "images")
    recs, conflicts = build_records(content, a.image_radius)
    rows = to_rows(recs)
    write_csv(out / "records.csv", rows)
    write_csv(out / "conflicts.csv", conflicts)
    write_review(out / "review.html", rows, out)
    summary = {
        "pdf": a.pdf, "pages": content.pages, "ocr_pages": content.ocr_pages,
        "tables": len(content.tables), "text_lines": len(content.lines), "images": len(content.images),
        "records": len(recs), "records_with_mgrs": sum(1 for r in recs if r.mgrs),
        "records_with_issues": sum(1 for r in recs if r.issues), "conflicts": len(conflicts),
        "unmapped_tables": [f"p{t.page}#{t.index}: {t.rows[0] if t.rows else []}" for t in content.tables
                            if t.rows and not any(k in "".join(t.rows[0]) for k in ("부대", "명칭"))],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"검토: {out / 'review.html'}")


if __name__ == "__main__":
    main()
