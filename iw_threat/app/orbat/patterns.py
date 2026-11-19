"""전투서열 문서용 패턴: MGRS 좌표, 부대명, 제대(단대호 제대 표시 포함)."""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── MGRS ── 예) 52S CG 12345 67890 / 52SCG1234567890 / 52S CG 123 678
_MGRS_RE = re.compile(
    r"(?<![A-Z0-9])(\d{1,2})\s?([C-HJ-NP-X])\s?([A-HJ-NP-Z])\s?([A-HJ-NP-V])\s?(\d{1,5}\s\d{1,5}|\d{2,10})(?![0-9])"
)


@dataclass(frozen=True)
class MgrsHit:
    raw: str
    mgrs: str          # 공백 제거 정규형
    lat: float
    lon: float
    precision_m: int   # 자릿수에 따른 정밀도(m)
    span: tuple[int, int]


def find_mgrs(text: str, bbox: tuple[float, float, float, float] | None = None) -> list[MgrsHit]:
    """텍스트에서 MGRS 좌표를 찾아 위경도로 변환. bbox=(min_lat, min_lon, max_lat, max_lon)로 범위 검증."""
    import mgrs as _mgrs

    conv = _mgrs.MGRS()
    hits = []
    for m in _MGRS_RE.finditer(text.upper()):
        zone, band, c1, c2, digits = m.groups()
        if not 1 <= int(zone) <= 60:
            continue
        if " " in digits:
            e, n = digits.split()
            if len(e) != len(n):
                continue
        else:
            if len(digits) % 2:
                continue
            e, n = digits[: len(digits) // 2], digits[len(digits) // 2:]
        norm = f"{int(zone):02d}{band}{c1}{c2}{e}{n}"
        try:
            lat, lon = conv.toLatLon(norm)
        except Exception:  # noqa: BLE001 — 형식은 맞지만 유효하지 않은 좌표
            continue
        if bbox and not (bbox[0] <= lat <= bbox[2] and bbox[1] <= lon <= bbox[3]):
            continue
        hits.append(MgrsHit(m.group(0), norm, round(lat, 6), round(lon, 6), 10 ** (5 - len(e)), m.span()))
    return hits


# OCR 오인식 보정: 위치별로 문자 자리에 찍힌 숫자를 흔한 혼동 문자로 치환
_OCR_MGRS_RE = re.compile(r"(?<!\d)(\d{2})([A-Z0-9])\s?([A-Z0-9])([A-Z0-9])\s?(\d{5}\s?\d{5}|\d{4}\s?\d{4}|\d{6}|\d{4})(?!\d)")
_BAND_FIX = {"5": "S", "7": "T"}                                   # 한반도: S(32~40°N), T(40~48°N)
_SQ_FIX = {"0": "D", "6": "G", "8": "B", "5": "S", "2": "Z", "4": "A"}


def ocr_fix_mgrs(text: str) -> str:
    """'525DH...'(S→5), '5250H...'(D→0) 같은 OCR 혼동을 MGRS 형식으로 보정한 문자열 반환."""
    def fix(m):
        zone, band, c1, c2, digits = m.groups()
        band = _BAND_FIX.get(band, band)
        c1, c2 = _SQ_FIX.get(c1, c1), _SQ_FIX.get(c2, c2)
        if not (band.isalpha() and c1.isalpha() and c2.isalpha()):
            return m.group(0)
        return f" {zone}{band}{c1}{c2}{digits.replace(' ', '')} "
    return _OCR_MGRS_RE.sub(fix, text.upper())


# ── 제대 ──
ECHELONS = ["군", "군단", "사단", "여단", "연대", "대대", "중대", "소대"]   # 상위 → 하위
ECHELON_RANK = {e: i for i, e in enumerate(ECHELONS)}
# 단대호 제대 표시(부호 위 기호) → 제대
SYMBOL_ECHELON = {"XXXX": "군", "XXX": "군단", "XX": "사단", "X": "여단",
                  "III": "연대", "Ⅲ": "연대", "II": "대대", "Ⅱ": "대대", "I": "중대", "Ⅰ": "중대"}

# ── 부대명 ── 예) 제4군단, 4군단, 제10기계화보병사단, 제820전차군단, 제○○○군부대
_UNIT_RE = re.compile(
    r"(제?\s?\d{1,4}\s?(?:[가-힣]{0,8}?)(?:군단|사단|여단|연대|대대|중대|소대))"
    r"|(제?\s?\d{3,5}\s?군부대)"
)


@dataclass(frozen=True)
class UnitHit:
    raw: str
    key: str           # 정규화 키 (공백·'제' 제거)
    echelon: str | None
    span: tuple[int, int]


def unit_key(name: str) -> str:
    return re.sub(r"^제", "", re.sub(r"\s+", "", name))


def echelon_of(name: str) -> str | None:
    n = re.sub(r"\s+", "", name)
    for e in sorted(ECHELONS, key=len, reverse=True):   # '군단'을 '군'보다 먼저
        if n.endswith(e) and not n.endswith("군부대"):
            return e
    return None


def find_units(text: str) -> list[UnitHit]:
    out = []
    for m in _UNIT_RE.finditer(text):
        raw = m.group(0).strip()
        out.append(UnitHit(raw, unit_key(raw), echelon_of(raw), m.span()))
    return out


def symbol_echelon(text: str) -> str | None:
    """'XX', 'III' 처럼 단대호 제대 기호만 있는 짧은 문자열 → 제대."""
    t = re.sub(r"\s+", "", text.upper())
    return SYMBOL_ECHELON.get(t)
