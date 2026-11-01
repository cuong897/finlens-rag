"""Optional local Tesseract data for MuPDF's integrated OCR; no chat/API calls."""

import hashlib
import math
from pathlib import Path
import re

import httpx

MODELS = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "vie": "79df64caf7bcfb2a27df5042ecb6121e196eada34da774956995747636d5bfa1",
}
BASE = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/"


def prepare_ocr(data_dir):
    target = Path(data_dir) / "ocr/tessdata"
    target.mkdir(parents=True, exist_ok=True)
    for language, digest in MODELS.items():
        path = target / f"{language}.traineddata"
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            continue
        response = httpx.get(BASE + path.name, timeout=60, follow_redirects=True)
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != digest:
            raise ValueError("Checksum dữ liệu OCR không khớp phiên bản đã ghim.")
        temporary = path.with_suffix(".part")
        temporary.write_bytes(response.content)
        temporary.replace(path)
    return {"ready": True, "languages": list(MODELS), "data_dir": str(target.resolve())}


def needs_ocr(page):
    text = page.get_text()
    financial = re.search(r"consolidated (?:balance|income|cash|statement)|balance sheet|income statement|cash flow statement|notes to|form b\s*0[1239]|báo cáo tài chính", " ".join(text[:1500].split()), re.I)
    # A native footer/watermark does not make the scanned body searchable.
    # Large images still need inspection even if a few native words exist.
    # Some publishers turn table characters into vector outlines, not images.
    # Filled path segments distinguish outlined glyphs from ordinary table borders.
    if financial and sum(len(path["items"]) for path in page.get_drawings() if path.get("fill") is not None) > 3000:
        return True
    for image in page.get_image_info():
        rect = page.rect & image["bbox"]
        if rect.get_area() < page.rect.get_area() * (.025 if financial else .5):
            continue
        content = page.get_text(clip=rect)
        if len(content.strip()) < 30:
            return True
    return False


def textpage_ocr(page, data_dir):
    if not data_dir or not (Path(data_dir) / "eng.traineddata").is_file():
        return None, "Bảng ảnh cần OCR cục bộ; chạy prepare-ocr rồi reprocess để đọc số liệu."
    if hashlib.sha256((Path(data_dir) / "eng.traineddata").read_bytes()).hexdigest() != MODELS["eng"]:
        return None, "Checksum dữ liệu OCR không khớp; chạy prepare-ocr để chuẩn bị lại."
    dpi = min(300, int(72 * math.sqrt(20_000_000 / page.rect.get_area())))
    if dpi < 100:
        return None, "Trang quá lớn cho OCR tự động; hãy cung cấp PDF có văn bản."
    try:
        vie = Path(data_dir) / "vie.traineddata"
        # A scan has no native text from which its language can be inferred.
        # Use both pinned language packs when available, including pure scans.
        bilingual = vie.is_file() and hashlib.sha256(vie.read_bytes()).hexdigest() == MODELS["vie"]
        language = "eng+vie" if bilingual else "eng"
        textpage = page.get_textpage_ocr(language=language, dpi=dpi, full=False, tessdata=str(Path(data_dir).resolve()))
        notice = f"OCR cục bộ ({language}); cần kiểm tra dấu, chữ số, cột năm và đơn vị với PDF gốc."
        if not bilingual:
            notice += " Chưa có bộ tiếng Việt hợp lệ; chạy prepare-ocr nếu tài liệu dùng tiếng Việt."
        return textpage, notice
    except Exception:
        return None, "OCR cục bộ chưa đọc được vùng ảnh; cần kiểm tra tệp hoặc chuẩn bị lại dữ liệu OCR."
