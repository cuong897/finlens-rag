"""Explicit, reproducible import of a small public-document pilot corpus.

Original PDFs stay local. Metadata, checksums and six visually reviewed scalar
facts are versioned; this module never sends document content to a chat API.
"""

import hashlib
import json
from pathlib import Path

import httpx
import pymupdf

from finlens.ingest import MAX_PDF_BYTES, IngestError, ingest_pdf
from finlens.store import Store


def manifest():
    return json.loads((Path(__file__).parent / "fixtures" / "public_pilot.json").read_text(encoding="utf-8"))


def download_report(report: dict, cache: Path, client):
    target = cache / report["filename"]
    cache.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        temporary = target.with_suffix(".pdf.part")
        try:
            with client.stream("GET", report["url"]) as response:
                response.raise_for_status()
                size = 0
                with temporary.open("wb") as destination:
                    for block in response.iter_bytes(1024 * 1024):
                        size += len(block)
                        if size > MAX_PDF_BYTES:
                            raise IngestError("Báo cáo công khai vượt giới hạn PDF.")
                        destination.write(block)
            content = temporary.read_bytes()
            if hashlib.sha256(content).hexdigest() != report["sha256"]:
                raise IngestError("Nội dung báo cáo đã thay đổi so với bản được đối chiếu. Cần kiểm tra lại nguồn trước khi nhập.")
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    content = target.read_bytes()
    if hashlib.sha256(content).hexdigest() != report["sha256"]:
        raise IngestError("Checksum PDF lưu cục bộ không khớp. Giữ nguyên tệp và đối chiếu nguồn trước khi chạy lại.")
    return content


def verify_fact_anchor(pdf, fact):
    page = pdf[fact["page"] - 1]
    normalized = " ".join(page.get_text().split()).lower()
    expected_value = f"{int(fact['value']):,}"
    if fact["label"].lower() not in normalized or expected_value not in normalized or "vnd" not in normalized:
        raise IngestError(f"Không tìm thấy chỉ tiêu, đơn vị hoặc số liệu chuẩn trên trang {fact['page']}.")


def import_pilot(store: Store, with_reviewed_facts=False, client=None):
    reports = manifest()
    imported = []
    if client is None:
        with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as connection:
            return import_pilot(store, with_reviewed_facts, connection)
    for report in reports:
        print(f"Importing public report: {report['company']} {report['year']}", flush=True)
        content = download_report(report, store.root / "public-pilot", client)
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if len(pdf) != report["pages"]:
                raise IngestError("Số trang báo cáo không khớp bản đã kiểm tra.")
            for fact in report["facts"]:
                verify_fact_anchor(pdf, fact)
        result = ingest_pdf(store, content, report["filename"], report["company"], report["year"], report["scope"])
        document_id = result["document"]["id"]
        store.record_source(document_id, report["url"], report["publisher"])
        if with_reviewed_facts:
            for fact in report["facts"]:
                note = (f"Đối chiếu trực quan PDF bởi trợ lý ngày 27/10/2026; dòng {fact['label']}, "
                        f"cột {fact['year']}, đơn vị VND; trang PDF {fact['page']}, trang in {fact['printed_page']}. "
                        "net_income là lợi nhuận sau thuế toàn tập đoàn, gồm lợi ích không kiểm soát.")
                store.add_fact({"document_id": document_id, "page": fact["page"], "metric": fact["metric"],
                                "year": fact["year"], "value": fact["value"], "unit": "one", "currency": "VND",
                                "verified": True, "note": note})
        imported.append({"document_id": document_id, "company": report["company"], "year": report["year"],
                         "created": result["created"], "source_url": report["url"], "sha256": report["sha256"],
                         "reviewed_facts": len(report["facts"]) if with_reviewed_facts else 0})
        print(f"Imported {report['year']}: {report['pages']} pages", flush=True)
    return {"kind": "public_document_pilot", "documents": imported,
            "note": "Bộ thử nhỏ; không phải benchmark độc lập. PDF gốc và dữ liệu nhập chỉ lưu cục bộ."}
