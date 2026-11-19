"""전투서열 PDF 파서 테스트 — 가상 부대·가상 좌표로 만든 합성 PDF 사용."""
from pathlib import Path

import fitz
import pytest

from app.orbat.patterns import echelon_of, find_mgrs, find_units, symbol_echelon
from app.orbat.pdf_extract import extract_pdf
from app.orbat.records import build_records

KR = "korea"


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    p = doc.new_page()
    # 서술형: 같은 줄에 부대명+좌표 / 다음 줄에 좌표만 / 단대호 제대 기호
    p.insert_text((50, 60), "제901가상보병사단 사령부 52S CH 20000 40000 일대 주둔", fontname=KR, fontsize=10)
    p.insert_text((50, 90), "제902가상전차여단", fontname=KR, fontsize=10)
    p.insert_text((50, 105), "주둔지 52SDH1000020000", fontname=KR, fontsize=10)
    p.insert_text((50, 120), "예비 진지 52S CH 300 450", fontname=KR, fontsize=10)          # 같은 부대의 추가 좌표
    p.insert_text((50, 520), "미상 부대 집결 52S CH 30000 45000", fontname=KR, fontsize=10)   # 부대명 없음
    p.insert_text((330, 520), "III", fontname=KR, fontsize=10)                              # 단대호 제대 기호
    # 표: 선으로 그린 격자
    hdr = ["부대명", "상급부대", "좌표", "장비", "수량"]
    rows = [["제903가상포병연대", "제901가상보병사단", "52SCH1500035000", "자주포", "18"],
            ["제904가상보병사단", "제903가상포병연대", "52SDG5000050000", "", ""],
            ["제905가상공병대대", "제901가상보병사단", "좌표 미상", "도하장비", "4"]]
    widths, y0, rh = [100, 100, 120, 70, 50], 200, 22
    xs = [50]
    for w in widths:
        xs.append(xs[-1] + w)
    for ri, row in enumerate([hdr] + rows):
        for ci, val in enumerate(row):
            r = fitz.Rect(xs[ci], y0 + ri * rh, xs[ci + 1], y0 + (ri + 1) * rh)
            p.draw_rect(r, color=(0, 0, 0), width=0.8)
            if val:
                p.insert_text((r.x0 + 3, r.y1 - 7), val, fontname=KR, fontsize=7)
    # 단대호 이미지(가상): 첫 줄 근처
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pix.set_rect(pix.irect, (200, 220, 255))
    p.insert_image(fitz.Rect(420, 45, 460, 75), pixmap=pix)
    doc.save(path)


def test_patterns():
    assert [h.mgrs for h in find_mgrs("위치 52S CH 20000 40000, 52SDH1000020000")] == ["52SCH2000040000", "52SDH1000020000"]
    assert find_mgrs("52S CH 300 450")[0].precision_m == 100
    assert find_mgrs("52S CH 1234 567") == []            # 자릿수 불일치
    assert find_mgrs("12SVC0000000000", bbox=(33, 124, 43.5, 131.5)) == []   # 한반도 밖
    assert echelon_of("제4군단") == "군단" and echelon_of("제2사단") == "사단" and echelon_of("제123군부대") is None
    assert symbol_echelon("XX") == "사단" and symbol_echelon(" III ") == "연대"
    assert find_units("제10 기계화보병사단과 820전차군단")[1].key == "820전차군단"


def test_parse_synthetic_pdf(tmp_path):
    pdf = tmp_path / "orbat.pdf"
    _make_pdf(pdf)
    content = extract_pdf(pdf, image_dir=tmp_path / "img")
    assert len(content.tables) == 1 and content.tables[0].rows[0][0] == "부대명"
    assert len(content.images) == 1

    recs, conflicts = build_records(content)
    by = {(r.source, r.unit_key): r for r in recs}
    multi: dict = {}
    for r in recs:
        multi.setdefault((r.source, r.unit_key), []).append(r.mgrs)

    t = by[("table", "903가상포병연대")]
    assert t.echelon == "연대" and t.parent_key == "901가상보병사단" and t.mgrs == "52SCH1500035000"
    assert t.equipment == "자주포" and t.count == "18"
    assert by[("table", "904가상보병사단")].issues and "제대 역전" in by[("table", "904가상보병사단")].issues[0]
    assert "MGRS 없음" in by[("table", "905가상공병대대")].issues[0]

    s = by[("text", "901가상보병사단")]
    assert s.mgrs == "52SCH2000040000" and s.lat == pytest.approx(38.29, abs=0.01) and s.images  # 근처 이미지 연결
    assert multi[("text", "902가상전차여단")] == ["52SDH1000020000", "52SCH300450"]           # 윗줄 부대명 연결
    unknown = [r for r in recs if r.source == "text" and not r.unit_key]
    assert unknown and unknown[0].echelon == "연대" and unknown[0].echelon_from == "symbol"   # 단대호 기호

    locs = {r.location_type for r in recs if r.unit_key == "902가상전차여단"}
    assert locs == {"주둔지", "예비진지"}
    assert not [c for c in conflicts if c["type"] == "위치"]          # 유형이 다른 위치는 충돌 아님


def test_ocr_normalization_and_mgrs_fix():
    from app.orbat.patterns import ocr_fix_mgrs
    from app.orbat.pdf_extract import normalize_ocr_text

    assert normalize_ocr_text("제 9 0 4 4 가 상 전 차 연대") == "제9044가상전차연대"
    assert normalize_ocr_text("제9 0.01가상기계화사단") == "제9001가상기계화사단"
    fixed = ocr_fix_mgrs("주둔지 525DH1000020000")                      # S→5 오인식
    assert [h.mgrs for h in find_mgrs(fixed)] == ["52SDH1000020000"]
    assert [h.mgrs for h in find_mgrs(ocr_fix_mgrs("5250H1000020000"))] == ["52SDH1000020000"]   # D→0


def test_fix_mgrs_cell_from_ocr_confusions():
    from app.orbat.patterns import fix_mgrs_cell
    assert fix_mgrs_cell("528CG7418210714") == "52SCG7418210714"     # S→8
    assert fix_mgrs_cell("52S8CH7133280136") == "52SCH7133280136"    # S 중복 인식
    assert fix_mgrs_cell("528SDG8063376992") == "52SDG8063376992"
    assert fix_mgrs_cell("52S0H1000020000") == "52SDH1000020000"     # D→0
    assert fix_mgrs_cell("좌표 미상") == "좌표 미상"
