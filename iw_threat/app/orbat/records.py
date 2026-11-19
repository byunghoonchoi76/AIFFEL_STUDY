"""추출 결과 → 전투서열 후보 레코드(검토 대기). 규칙 기반이며 모든 레코드에 원문 위치를 남김."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from .patterns import ECHELON_RANK, find_mgrs, find_units, fix_mgrs_cell, ocr_fix_mgrs, symbol_echelon, unit_key
from .pdf_extract import Box, Image, Line, PdfContent, Table

KOREA_BBOX = (33.0, 124.0, 43.5, 131.5)   # 한반도 주변 위경도 범위(좌표 오인식 차단)

LOCATION_TYPES = ["사령부", "주둔지", "예비진지", "진지", "집결지", "집결", "훈련장", "기지"]

HEADER_KEYS = {
    "unit": ["부대명", "부대", "단위부대", "명칭"],
    "parent": ["상급부대", "상급", "소속", "예속"],
    "echelon": ["제대", "규모"],
    "location": ["좌표", "위치", "mgrs", "주둔지", "주둔"],
    "equipment": ["장비", "무기", "보유장비"],
    "count": ["수량", "대수", "보유수"],
    "as_of": ["기준일", "일자", "확인일", "날짜"],
    "remark": ["비고", "특이사항"],
}


@dataclass
class OrbatRecord:
    doc: str
    page: int
    source: str                      # table | text
    unit_raw: str
    unit_key: str
    echelon: str | None = None
    echelon_from: str | None = None  # name | symbol | column
    parent_raw: str | None = None
    parent_key: str | None = None
    mgrs: str | None = None
    lat: float | None = None
    lon: float | None = None
    mgrs_precision_m: int | None = None
    location_type: str | None = None    # 사령부·주둔지·예비진지 등 (같은 유형끼리만 위치 충돌 비교)
    equipment: str | None = None
    count: str | None = None
    as_of: str | None = None
    remark: str | None = None
    quote: str = ""
    bbox: Box | None = None
    images: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _map_header(row: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ci, cell in enumerate(row):
        c = re.sub(r"\s+", "", cell).lower()
        for key, words in HEADER_KEYS.items():
            if key not in out and any(w in c for w in words):
                out[key] = ci
                break
    return out


def _dist(a: Box, b: Box) -> float:
    """두 상자 사이 최단 간격(pt). 겹치면 0 — 긴 줄과 옆 이미지도 가깝게 판정."""
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return math.hypot(dx, dy)


def _near_images(page: int, bbox: Box, images: list[Image], radius: float) -> list[str]:
    return sorted({im.path for im in images if im.page == page and _dist(bbox, im.bbox) <= radius})


def from_tables(doc: str, tables: list[Table], images: list[Image], radius: float) -> list[OrbatRecord]:
    recs = []
    for t in tables:
        if not t.rows:
            continue
        hdr = _map_header(t.rows[0])
        if "unit" not in hdr:
            continue
        for row in t.rows[1:]:
            cell = lambda k: row[hdr[k]].strip() if k in hdr and hdr[k] < len(row) and row[hdr[k]].strip() else None  # noqa: E731
            name = cell("unit")
            if not name:
                continue
            units = find_units(name)
            r = OrbatRecord(doc, t.page, "table", name, units[0].key if units else unit_key(name),
                            quote=" | ".join(row), bbox=t.bbox)
            if units and units[0].echelon:
                r.echelon, r.echelon_from = units[0].echelon, "name"
            if cell("echelon") and not r.echelon:
                r.echelon = symbol_echelon(cell("echelon")) or cell("echelon")
                r.echelon_from = "column"
            if cell("parent"):
                r.parent_raw = cell("parent")
                pu = find_units(r.parent_raw)
                r.parent_key = pu[0].key if pu else unit_key(r.parent_raw)
            loc = " ".join(c for c in row if c)
            src = cell("location") or loc
            # 셀 안 줄바꿈으로 좌표가 끊긴 경우 대비: 공백 제거 후 재시도
            hits = find_mgrs(src, KOREA_BBOX) or find_mgrs(re.sub(r"\s+", "", src), KOREA_BBOX)
            if not hits and t.ocr:
                hits = (find_mgrs(fix_mgrs_cell(cell("location") or ""), KOREA_BBOX)
                        or find_mgrs(ocr_fix_mgrs(re.sub(r"\s+", "", src)), KOREA_BBOX))
            if hits:
                h = hits[0]
                r.mgrs, r.lat, r.lon, r.mgrs_precision_m = h.mgrs, h.lat, h.lon, h.precision_m
                if len(hits) > 1:
                    r.issues.append(f"좌표 {len(hits)}개 — 첫 좌표 사용")
            elif cell("location"):
                r.issues.append("위치 열에 유효한 MGRS 없음")
            r.equipment, r.count, r.as_of, r.remark = cell("equipment"), cell("count"), cell("as_of"), cell("remark")
            r.images = _near_images(t.page, t.bbox, images, radius)
            if t.ocr:
                r.issues.append("OCR 표 추출 — 원문 대조 필요")
            recs.append(r)
    return recs


def from_text(doc: str, lines: list[Line], images: list[Image], radius: float, look_back: int = 2) -> list[OrbatRecord]:
    """좌표가 있는 줄 → 같은 줄(없으면 바로 위 줄들)의 부대명과 연결. 근처 단대호 제대 기호도 반영."""
    recs = []
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)
    for page, pls in by_page.items():
        pls.sort(key=lambda l: (round(l.bbox[1], 0), l.bbox[0]))
        for i, ln in enumerate(pls):
            hits = find_mgrs(ln.text, KOREA_BBOX)
            ocr_fixed = False
            if not hits and ln.ocr:
                hits = find_mgrs(ocr_fix_mgrs(ln.text), KOREA_BBOX)
                ocr_fixed = bool(hits)
            if not hits:
                continue
            units = find_units(ln.text)
            # OCR로 읽은 표 행: 한 줄에 부대가 2개 이상이면 첫째=부대, 둘째=상급부대
            ocr_row = ln.ocr and len(units) >= 2
            ctx = ln.text
            if not units:
                for prev in reversed(pls[max(0, i - look_back):i]):
                    units = find_units(prev.text)
                    if units:
                        ctx = f"{prev.text} / {ln.text}"
                        break
            for h in hits:
                if ocr_row:
                    u = units[0]
                    r = OrbatRecord(doc, page, "ocr_row", u.raw, u.key, u.echelon, "name" if u.echelon else None,
                                    parent_raw=units[1].raw, parent_key=units[1].key)
                elif units:
                    # 좌표 바로 앞에 나온 부대명 우선
                    before = [u for u in units if u.span[0] < h.span[0]] if ctx == ln.text else units
                    u = (before or units)[-1]
                    r = OrbatRecord(doc, page, "text", u.raw, u.key, u.echelon, "name" if u.echelon else None)
                else:
                    r = OrbatRecord(doc, page, "text", "", "", issues=["부대명 미식별 — 수동 지정 필요"])
                r.mgrs, r.lat, r.lon, r.mgrs_precision_m = h.mgrs, h.lat, h.lon, h.precision_m
                r.quote, r.bbox = ctx, ln.bbox
                if ln.ocr:
                    r.issues.append("OCR 추출 — 원문 대조 필요" + (" (좌표 문자 보정)" if ocr_fixed else ""))
                compact = re.sub(r"\s+", "", ln.text[: h.span[0]] or ln.text)
                r.location_type = next((t for t in LOCATION_TYPES if t in compact), None)
                if not r.echelon:
                    sym = [symbol_echelon(o.text) for o in pls if _dist(o.bbox, ln.bbox) <= radius]
                    sym = [s for s in sym if s]
                    if sym:
                        r.echelon, r.echelon_from = sym[0], "symbol"
                r.images = _near_images(page, ln.bbox, images, radius)
                recs.append(r)
    return recs


def _km(a: OrbatRecord, b: OrbatRecord) -> float:
    if None in (a.lat, a.lon, b.lat, b.lon):
        return 0.0
    dlat, dlon = math.radians(b.lat - a.lat), math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat)) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _flag_outliers(recs: list[OrbatRecord], max_km: float) -> None:
    """문서 내 좌표 중앙값에서 max_km 이상 떨어진 좌표 표시 (OCR 오인식으로 엉뚱한 곳이 된 좌표 검출)."""
    pts = sorted((r.lat, r.lon) for r in recs if r.lat is not None)
    if len(pts) < 5:
        return
    lat = sorted(p[0] for p in pts)[len(pts) // 2]
    lon = sorted(p[1] for p in pts)[len(pts) // 2]
    center = OrbatRecord("", 0, "", "", "", lat=lat, lon=lon)
    for r in recs:
        if r.lat is not None and _km(center, r) > max_km:
            r.issues.append(f"좌표 이상치: 문서 중심에서 {_km(center, r):.0f}km")


def validate(recs: list[OrbatRecord], conflict_km: float = 1.0, outlier_km: float = 150.0) -> list[dict]:
    """레코드별 문제 표시 + 같은 부대 간 충돌 목록 반환."""
    _flag_outliers(recs, outlier_km)
    by_key: dict[str, list[OrbatRecord]] = {}
    for r in recs:
        if r.unit_key:
            by_key.setdefault(r.unit_key, []).append(r)
    ech = {k: next((x.echelon for x in v if x.echelon), None) for k, v in by_key.items()}
    for r in recs:
        if r.parent_key:
            pe, ce = ech.get(r.parent_key), r.echelon
            if pe and ce and pe in ECHELON_RANK and ce in ECHELON_RANK and ECHELON_RANK[pe] >= ECHELON_RANK[ce]:
                r.issues.append(f"제대 역전: 상급 {pe} ≤ 하급 {ce}")
            if r.parent_key == r.unit_key:
                r.issues.append("자기 자신을 상급부대로 지정")
    conflicts = []
    for k, v in by_key.items():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                a, b = v[i], v[j]
                d = _km(a, b)
                if d > conflict_km and a.location_type == b.location_type:
                    conflicts.append({"unit_key": k, "type": "위치", "a": f"p{a.page} {a.mgrs}", "b": f"p{b.page} {b.mgrs}", "km": round(d, 2)})
                if a.parent_key and b.parent_key and a.parent_key != b.parent_key:
                    conflicts.append({"unit_key": k, "type": "소속", "a": f"p{a.page} {a.parent_raw}", "b": f"p{b.page} {b.parent_raw}", "km": None})
    # 상하 관계 순환 검사
    parent = {r.unit_key: r.parent_key for r in recs if r.unit_key and r.parent_key}
    for start in parent:
        seen, cur = set(), start
        while cur in parent:
            if cur in seen:
                conflicts.append({"unit_key": start, "type": "순환", "a": " → ".join(seen), "b": cur, "km": None})
                break
            seen.add(cur)
            cur = parent[cur]
    return conflicts


def build_records(content: PdfContent, image_radius_pt: float = 40) -> tuple[list[OrbatRecord], list[dict]]:
    doc = content.path.rsplit("/", 1)[-1]
    recs = from_tables(doc, content.tables, content.images, image_radius_pt)
    recs += from_text(doc, content.lines, content.images, image_radius_pt)
    return recs, validate(recs)


def to_rows(recs: list[OrbatRecord]) -> list[dict]:
    rows = []
    for r in recs:
        d = asdict(r)
        d["images"] = ";".join(r.images)
        d["issues"] = ";".join(r.issues)
        d["bbox"] = ",".join(f"{v:.0f}" for v in r.bbox) if r.bbox else ""
        rows.append(d)
    return rows
