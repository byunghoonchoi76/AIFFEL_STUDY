"""PDF에서 텍스트 줄(위치 포함)·표·이미지(단대호·사진) 추출. 스캔 쪽은 OCR."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

Box = tuple[float, float, float, float]   # x0, y0, x1, y1 (PDF 좌표, pt)


@dataclass
class Line:
    page: int
    text: str
    bbox: Box


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
                tabs = list(page.find_tables(strategy="text"))
                tabs = [t for t in tabs if t.row_count >= 2 and t.col_count >= 2]
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


def _ocr_lines(page, pno: int, lang: str) -> list[Line]:
    import pytesseract
    from PIL import Image as PILImage

    scale = 300 / 72
    pix = page.get_pixmap(dpi=300)
    img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
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
        lines.append(Line(pno, " ".join(data["text"][i] for i in idx), (x0, y0, x1, y1)))
    return lines
