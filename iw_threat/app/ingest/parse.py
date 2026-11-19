"""문서 → 텍스트. PDF(텍스트층 없으면 OCR), HWPX(XML), TXT/MD.
HWP(바이너리)는 미지원: 한글에서 HWPX 또는 PDF로 저장 후 투입."""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path


def parse_hwpx(path: str | Path) -> str:
    texts = []
    with zipfile.ZipFile(path) as z:
        sections = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
        for name in sections:
            xml = z.read(name).decode("utf-8")
            xml = re.sub(r"</hp:p>", "\n", xml)
            xml = re.sub(r"<hp:tab[^>]*/>", "\t", xml)
            texts.append(html.unescape(re.sub(r"<[^>]+>", "", xml)))
    lines = [ln.rstrip() for ln in "\n".join(texts).splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def parse_pdf(path: str | Path, ocr_lang: str = "kor+eng") -> str:
    import fitz  # PyMuPDF

    pages = []
    with fitz.open(path) as doc:
        for page in doc:
            t = page.get_text().strip()
            if len(t) < 20:  # 스캔 문서 → OCR
                import pytesseract
                from PIL import Image

                pix = page.get_pixmap(dpi=300)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                t = pytesseract.image_to_string(img, lang=ocr_lang)
            pages.append(t)
    return "\n".join(pages)


def parse_file(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".hwpx":
        return parse_hwpx(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in {".txt", ".md"}:
        return Path(path).read_text(encoding="utf-8")
    if suffix == ".hwp":
        raise ValueError("HWP 바이너리는 미지원 — 한글에서 HWPX 또는 PDF로 저장 후 투입")
    raise ValueError(f"지원하지 않는 형식: {suffix}")
