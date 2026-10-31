"""Held-out public reports, kept separate from the development pilot registry."""

import json
from pathlib import Path

import httpx
import pymupdf

from finlens.ingest import IngestError, ingest_pdf
from finlens.public_pilot import download_report, manifest
from finlens.retrieval_dataset import check_split, fingerprint, quote_text, validate_cases


def benchmark_reports():
    return json.loads((Path(__file__).parent / "fixtures/benchmark_reports.json").read_text(encoding="utf-8"))


def audit_benchmark(store, dataset):
    """Check source/label consistency, not independent semantic correctness."""
    cases = dataset["cases"]
    validate_cases(cases, strict=True)
    check_split(cases, dataset["split"])
    dev_companies = {r["company"] for r in manifest()}
    if dev_companies & {c["company"] for c in cases}:
        raise ValueError("Benchmark trùng doanh nghiệp phát triển.")
    questions, targets, pages, documents = set(), set(), set(), {}
    summaries = {d["id"]: d for d in store.documents()}
    categories = {}
    for case in cases:
        question = quote_text(case["question"])
        if question in questions:
            raise ValueError("Câu hỏi benchmark trùng nội dung.")
        questions.add(question)
        document = store.find_document(case["company"], case["report_year"], case["scope"], case["source_sha256"])
        if not document or document["is_demo"] or document["filename"] != case["filename"]:
            raise ValueError(f"Sai nguồn benchmark: {case['id']}.")
        if document.get("allowed_pages"):
            raise ValueError("Benchmark cần toàn bộ tài liệu, không lọc trang theo đáp án.")
        documents[document["id"]] = summaries[document["id"]]
        for anchor in case["evidence"]:
            source = store.page(document["id"], anchor["page"])
            if not source or any(quote_text(q) not in quote_text(source["text"]) for q in anchor["quotes"]):
                raise ValueError(f"Trích đoạn không khớp: {case['id']} trang {anchor['page']}.")
            pages.add((case["company"], anchor["page"]))
        for answer in case["reference_answers"]:
            raw = answer["raw_value"]
            expected = ("-" + raw[1:-1] if raw.startswith("(") and raw.endswith(")") else raw).replace(",", "")
            if expected != answer["value"] or not any(raw in a["quotes"] for a in case["evidence"]):
                raise ValueError(f"Giá trị tham chiếu không khớp trích đoạn: {case['id']}.")
        targets.update((case["company"], key) for key in case["target_ids"])
        category = case["category"]
        categories[category] = categories.get(category, 0) + 1
    return {"cases": len(cases), "unique_lookup_targets": len(targets), "reference_pages": len(pages),
            "companies": sorted({c["company"] for c in cases}), "categories": categories,
            "full_report_pages": sum(d["page_count"] for d in documents.values()),
            "dataset_sha256": fingerprint(dataset), "literal_source_check": "passed",
            "independent_human_review": False}


def import_benchmark(store, *, reports=None, client=None):
    reports = benchmark_reports() if reports is None else reports
    development = manifest()
    if any(r["company"] in {d["company"] for d in development} or r["sha256"] in {d["sha256"] for d in development} for r in reports):
        raise IngestError("Nguồn benchmark trùng doanh nghiệp hoặc PDF phát triển.")
    if client is None:
        with httpx.Client(timeout=90, follow_redirects=True) as transport:
            return import_benchmark(store, reports=reports, client=transport)
    imported = []
    for report in reports:
        print(f"Importing held-out report: {report['company']} {report['year']}", flush=True)
        content = download_report(report, store.root / "benchmark-sources", client)
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if len(pdf) != report["pages"]:
                raise IngestError("Số trang nguồn benchmark đã thay đổi.")
        context = {key: report.get(key) for key in ("period_start", "period_end", "period_label")}
        result = ingest_pdf(store, content, report["filename"], report["company"], report["year"], report["scope"], context=context)
        document_id = result["document"]["id"]
        store.record_source(document_id, report["url"], report["publisher"])
        imported.append({"company": report["company"], "document_id": document_id, "created": result["created"],
                         "sha256": report["sha256"], "pages": report["pages"]})
    return {"kind": "held_out_benchmark_import", "documents": imported, "confirmed_facts_added": 0}
