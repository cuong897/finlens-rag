"""Conservative PDF ingestion and an explicitly synthetic reproducible corpus."""

import hashlib
import json
from pathlib import Path
import uuid

import pymupdf

from finlens.store import Store

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 250
MAX_TEXT_CHARS = 2_000_000


class IngestError(ValueError):
    pass


def ingest_pdf(store: Store, content: bytes, filename: str, company: str, year: int, scope: str):
    if len(content) > MAX_PDF_BYTES:
        raise IngestError("PDF vượt giới hạn 20 MB.")
    if not content.startswith(b"%PDF-"):
        raise IngestError("Tệp không có định dạng PDF hợp lệ.")
    digest = hashlib.sha256(content).hexdigest()
    existing = store.find_document(company.strip(), year, scope, digest)
    if existing:
        return {"document": existing, "created": False}
    pages, warnings = [], []
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise IngestError("PDF có mật khẩu; hãy cung cấp bản đã mở khóa.")
            if not 1 <= len(pdf) <= MAX_PAGES:
                raise IngestError("PDF phải có từ 1 đến 250 trang.")
            total_chars = 0
            for i, page in enumerate(pdf):
                text = page.get_text(sort=True).strip()
                try:
                    tables = page.find_tables().tables
                    for table in tables:
                        rows = table.extract()
                        table_text = "\n".join(" | ".join(str(c or "").replace("\n", " ") for c in row) for row in rows)
                        if table_text:
                            text += "\n\n[Bảng trích xuất — cần kiểm tra với PDF]\n" + table_text
                except Exception:
                    warnings.append(f"Không trích xuất được cấu trúc bảng trang {i + 1}.")
                if len(text) < 20:
                    warnings.append(f"Trang {i + 1} có ít văn bản; có thể cần OCR.")
                total_chars += len(text)
                if total_chars > MAX_TEXT_CHARS:
                    raise IngestError("Nội dung PDF vượt giới hạn xử lý 2 triệu ký tự.")
                pages.append(text)
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError("Không đọc được PDF. Hãy kiểm tra tệp gốc.") from exc
    if not any(len(p.strip()) >= 20 for p in pages):
        raise IngestError("PDF không có đủ văn bản để tìm kiếm. Bản MVP chưa có OCR.")
    document_id = uuid.uuid4().hex
    relative_path = f"uploads/{document_id}.pdf"
    target = store.root / relative_path
    target.write_bytes(content)
    try:
        document, created = store.add_document({
            "id": document_id, "company": company.strip(), "report_year": year, "scope": scope,
            "filename": filename.replace("\\", "/").split("/")[-1][:180],
            "sha256": digest, "is_demo": False,
            "pdf_path": relative_path, "warnings": warnings,
        }, pages)
        if not created:
            target.unlink(missing_ok=True)
        return {"document": document, "created": created}
    except Exception:
        target.unlink(missing_ok=True)
        raise


def seed_demo(store: Store):
    fixture = Path(__file__).parent / "fixtures" / "demo.json"
    reports = json.loads(fixture.read_text(encoding="utf-8"))
    created_count = 0
    for report in reports:
        content = "\n".join(report["pages"]).encode()
        document_id = f"demo-{report['code']}-{report['year']}"
        document, created = store.add_document({
            "id": document_id, "company": report["company"], "report_year": report["year"],
            "scope": "consolidated", "filename": f"DEMO_{report['code']}_{report['year']}.txt",
            "sha256": hashlib.sha256(content).hexdigest(), "is_demo": True,
            "pdf_path": None, "warnings": ["Dữ liệu giả lập. Không phải báo cáo doanh nghiệp thật."],
        }, report["pages"])
        created_count += int(created)
        for metric, value in report["facts"].items():
            store.add_fact({"document_id": document["id"], "page": 1, "metric": metric,
                            "year": report["year"], "value": str(value), "unit": "billion",
                            "currency": "VND", "verified": True,
                            "note": "Chỉ tiêu giả lập có kiểm tra tự động với fixture."})
    return {"created": created_count, "total_demo_documents": len(reports), "is_demo": True}
