"""PDF에서 텍스트 줄(위치 포함)·표·이미지(단대호·사진) 추출. 스캔 쪽은 OCR."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

Box = tuple[float, float, float, float]   # x0, y0, x1, y1 (PDF 좌표, pt)


@dataclass
class Line:
    page: int
    text: str
    bbox: Box
    ocr: bool = False


@dataclass
class Table:
    page: int
    index: int
    rows: list[list[str]]
    bbox: Box


@dataclass
class Image:
    page: int
    path: str
    bbox: Box
    width: int
    height: int


@dataclass
class PdfContent:
    path: str
    pages: int
    lines: list[Line] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)


HEADER_HINTS = ("부대", "명칭", "좌표", "제대", "상급", "주둔")


def _looks_like_orbat_header(t) -> bool:
    rows = t.extract()
    head = "".join((c or "") for c in (rows[0] if rows else [])).replace(" ", "")
    return sum(h in head for h in HEADER_HINTS) >= 2


def _inside(inner: Box, outer: Box, tol: float = 1.0) -> bool:
    return inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol


def extract_pdf(path: str | Path, image_dir: str | Path | None = None, min_image_px: int = 24,
                ocr_lang: str = "kor+eng") -> PdfContent:
    import fitz

    out = PdfContent(str(path), 0)
    if image_dir:
        Path(image_dir).mkdir(parents=True, exist_ok=True)
    with fitz.open(path) as doc:
        out.pages = len(doc)
        for pno, page in enumerate(doc, 1):
            # 표 (선 기반 → 실패 시 텍스트 정렬 기반)
            tabs = list(page.find_tables())
            if not tabs:
                # 선 없는 표: 텍스트 정렬 기반 탐지는 서술형 쪽을 표로 오인하기 쉬우므로
                # 머리행에 전투서열 표 열 이름이 있을 때만 채택
                tabs = [t for t in page.find_tables(strategy="text")
                        if t.row_count >= 2 and t.col_count >= 2 and _looks_like_orbat_header(t)]
            table_boxes = []
            for ti, t in enumerate(tabs):
                rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in t.extract()]
                out.tables.append(Table(pno, ti, rows, tuple(t.bbox)))
                table_boxes.append(tuple(t.bbox))

            # 텍스트 줄 (표 내부 줄은 제외 — 표로 따로 처리)
            d = page.get_text("dict")
            n_chars = 0
            for b in d["blocks"]:
                for ln in b.get("lines", []):
                    text = "".join(s["text"] for s in ln["spans"]).strip()
                    if not text:
                        continue
                    n_chars += len(text)
                    bbox = tuple(ln["bbox"])
                    if any(_inside(bbox, tb) for tb in table_boxes):
                        continue
                    out.lines.append(Line(pno, text, bbox))

            # 스캔 쪽: 텍스트층이 거의 없으면 OCR (줄 단위 위치 포함)
            if n_chars < 20:
                out.ocr_pages.append(pno)
                out.lines.extend(_ocr_lines(page, pno, ocr_lang))

            # 이미지 (단대호·사진 후보)
            if image_dir:
                for ii, info in enumerate(page.get_images(full=True)):
                    xref = info[0]
                    rects = page.get_image_rects(xref)
                    if not rects:
                        continue
                    pix = fitz.Pixmap(doc, xref)
                    if pix.width < min_image_px or pix.height < min_image_px:
                        continue
                    if pix.n - pix.alpha >= 4:   # CMYK → RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    fp = Path(image_dir) / f"p{pno:03d}_{ii:02d}.png"
                    pix.save(fp)
                    for r in rects:
                        out.images.append(Image(pno, str(fp), tuple(r), pix.width, pix.height))
    return out


def _remove_rules(gray, min_run: int):
    """표 괘선 제거: 가로·세로로 min_run 픽셀 이상 이어진 어두운 선을 흰색으로 지움 (OCR 잡음 감소)."""
    import numpy as np

    a = np.array(gray)
    dark = a < 128
    out = a.copy()
    for axis in (1, 0):                      # 1: 가로선(행 방향), 0: 세로선
        d = dark if axis == 1 else dark.T
        o = out if axis == 1 else out.T
        pad = np.zeros((d.shape[0], 1), dtype=bool)
        edges = np.diff(np.hstack([pad, d, pad]).astype(np.int8), axis=1)
        for r in np.where((edges != 0).any(axis=1))[0]:
            starts = np.where(edges[r] == 1)[0]
            ends = np.where(edges[r] == -1)[0]
            for st, en in zip(starts, ends):
                if en - st >= min_run:
                    o[r, st:en] = 255
    return out


def normalize_ocr_text(t: str) -> str:
    """Tesseract 한국어의 글자 사이 공백 보정: 한글-한글, '제'-숫자, 한 자리 숫자 연속 사이 공백 제거."""
    t = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", t)
    t = re.sub(r"제\s*([\d\s.,']+?)(?=[가-힣])", lambda m: "제" + re.sub(r"\D", "", m.group(1)), t)   # '제9 0.01' → '제9001'
    t = re.sub(r"제\s+(?=\d)", "제", t)
    t = re.sub(r"(?<=\b\d)\s(?=\d\b)", "", t)          # '9 0 4 4' → '9044'
    t = re.sub(r"(?<=\d)\s+(?=[가-힣])", "", t)            # '9044 가상' → '9044가상'
    return t


def _ocr_lines(page, pno: int, lang: str, dpi: int = 300) -> list[Line]:
    import pytesseract
    from PIL import Image as PILImage

    scale = dpi / 72
    pix = page.get_pixmap(dpi=dpi)
    img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    img = PILImage.fromarray(_remove_rules(img, min_run=int(dpi * 0.6)))   # 약 0.6인치 이상 선
    data = pytesseract.image_to_data(img, lang=lang, config="--psm 6", output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list[int]] = {}
    for i, w in enumerate(data["text"]):
        if w.strip():
            groups.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    lines = []
    for idx in groups.values():
        x0 = min(data["left"][i] for i in idx) / scale
        y0 = min(data["top"][i] for i in idx) / scale
        x1 = max(data["left"][i] + data["width"][i] for i in idx) / scale
        y1 = max(data["top"][i] + data["height"][i] for i in idx) / scale
        text = normalize_ocr_text(" ".join(data["text"][i] for i in idx))
        lines.append(Line(pno, text, (x0, y0, x1, y1), ocr=True))
    return lines
