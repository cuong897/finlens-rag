"""Geometric PDF reading: separate facing pages and navigation from table text."""

import re

import pymupdf
from finlens.ocr import needs_ocr, textpage_ocr

VERSION = "financial-layout-v3-ocr"


def _compact(text):
    return "\n".join(re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()).strip()


def read_page(page, *, ocr_dir=None):
    w, h = page.rect.width, page.rect.height
    regions = [page.rect]
    texts, warnings = [], []
    textpage = None
    if needs_ocr(page):
        textpage, notice = textpage_ocr(page, ocr_dir)
        warnings.append(notice)
    def extract(region):
        return pymupdf.utils.get_sorted_text(page, clip=region, textpage=textpage) if textpage else page.get_text(clip=region, sort=True)
    # Two actual financial headings on facing pages; width alone is insufficient.
    heading = r"(?:CONSOLIDATED )?(?:BALANCE SHEET|INCOME STATEMENT|STATEMENT OF (?:INCOME|CASH FLOWS)|CASH FLOW STATEMENT)|NOTES TO THE (?:CONSOLIDATED )?FINANCIAL"
    if w / h > 1.3:
        left = extract(pymupdf.Rect(0, 0, w / 2, h * .22))
        right = extract(pymupdf.Rect(w / 2, 0, w, h * .22))
        if all(re.search(heading, " ".join(part.split())) for part in (left, right)):
            regions = [pymupdf.Rect(0, 0, w / 2, h), pymupdf.Rect(w / 2, 0, w, h)]
        else:
            sidebar = extract(pymupdf.Rect(0, 0, w * .16, h)).casefold()
            navigation = ["report of the board", "independent auditor", "consolidated balance sheet", "notes to the"]
            if sum(label in sidebar for label in navigation) >= 3:
                regions = [pymupdf.Rect(w * .16, 0, w, h)]
    for index, region in enumerate(regions, 1):
        text = _compact(extract(region))
        try:
            for table in page.find_tables(clip=region).tables:
                rendered = "\n".join(" | ".join(_compact(str(cell or "")).replace("\n", " ") for cell in row) for row in table.extract())
                if rendered:
                    text += "\n\n[Bảng trích xuất — cần kiểm tra với PDF]\n" + rendered
        except Exception:
            warnings.append("Không trích xuất được cấu trúc bảng.")
        texts.append((f"[Vùng trang {index}]\n" if len(regions) > 1 else "") + text)
    return "\n\n".join(texts), warnings
