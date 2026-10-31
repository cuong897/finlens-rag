"""Geometric PDF reading: separate facing pages and navigation from table text."""

import re

import pymupdf

VERSION = "financial-layout-v2"


def _compact(text):
    return "\n".join(re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()).strip()


def read_page(page):
    w, h = page.rect.width, page.rect.height
    regions = [page.rect]
    # Two actual financial headings on facing pages; width alone is insufficient.
    heading = r"CONSOLIDATED (?:BALANCE SHEET|INCOME STATEMENT|STATEMENT|CASH FLOW)|NOTES TO THE (?:CONSOLIDATED )?FINANCIAL"
    if w / h > 1.3:
        left = page.get_text(clip=pymupdf.Rect(0, 0, w / 2, h * .22), sort=True)
        right = page.get_text(clip=pymupdf.Rect(w / 2, 0, w, h * .22), sort=True)
        if all(re.search(heading, " ".join(part.split())) for part in (left, right)):
            regions = [pymupdf.Rect(0, 0, w / 2, h), pymupdf.Rect(w / 2, 0, w, h)]
        else:
            sidebar = page.get_text(clip=pymupdf.Rect(0, 0, w * .16, h), sort=True).casefold()
            navigation = ["report of the board", "independent auditor", "consolidated balance sheet", "notes to the"]
            if sum(label in sidebar for label in navigation) >= 3:
                regions = [pymupdf.Rect(w * .16, 0, w, h)]
    texts, warnings = [], []
    for index, region in enumerate(regions, 1):
        text = _compact(page.get_text(clip=region, sort=True))
        try:
            for table in page.find_tables(clip=region).tables:
                rendered = "\n".join(" | ".join(_compact(str(cell or "")).replace("\n", " ") for cell in row) for row in table.extract())
                if rendered:
                    text += "\n\n[Bảng trích xuất — cần kiểm tra với PDF]\n" + rendered
        except Exception:
            warnings.append("Không trích xuất được cấu trúc bảng.")
        texts.append((f"[Vùng trang {index}]\n" if len(regions) > 1 else "") + text)
    return "\n\n".join(texts), warnings
