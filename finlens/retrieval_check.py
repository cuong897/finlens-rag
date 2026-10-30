"""Reproducible retrieval ablation on a small, assistant-authored dev corpus.

No generation, model downloads or changes to confirmed facts. All strategies
use the same metadata/page restrictions and embedding query, when applicable.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

from finlens.public_pilot import manifest
from finlens.providers import ModelConfig, Ollama, ProviderError
from finlens.retrieval import bm25, contextual_fusion, cosine, financial_query, rrf
from finlens.retrieval_dataset import check_split, fingerprint, quote_text, score_evidence, validate_cases


def score_ranking(hits, gold):
    ranks = [i for i, hit in enumerate(hits[:5], 1) if (hit["document_id"], hit["page"]) in gold]
    found = {(h["document_id"], h["page"]) for h in hits[:5]} & gold
    return {"recall_at_5": len(found) / len(gold), "reciprocal_rank": 1 / ranks[0] if ranks else 0,
            "gold_rank": ranks[0] if ranks else None,
            "pages": [{"document_id": h["document_id"], "page": h["page"]} for h in hits[:5]]}


def evaluate_retrieval(store, output_dir="artifacts/retrieval", *, cases=None, reports=None,
                       embedder=None, embed_model="bge-m3:latest", dataset_info=None):
    cases = cases if cases is not None else json.loads((Path(__file__).parent / "fixtures" / "retrieval_cases.json").read_text(encoding="utf-8"))
    reports = reports if reports is not None else manifest()
    validate_cases(cases)
    dataset_info = dataset_info or {"name": "public-pilot-development", "split": "development",
                                    "label_origin": "assistant-authored on reviewed known reports"}
    check_split(cases, dataset_info["split"])
    embedder = embedder or Ollama(ModelConfig(embed_model=embed_model))
    cached = store.embeddings(embed_model)
    prepared, cohorts, corpus = [], {}, {}
    # Validate every source/label before the first embedding request.
    for case in cases:
        source = next((r for r in reports if r["filename"] == case["filename"] and r["company"] == case["company"]
                       and r["year"] == case["report_year"] and r["scope"] == case["scope"]), None)
        sha = case.get("source_sha256") or (source["sha256"] if source else None)
        if not sha:
            raise ValueError("Câu hỏi không khớp tài liệu nguồn trong registry.")
        document = store.find_document(case["company"], case["report_year"], case["scope"], sha)
        if not document or document["is_demo"]:
            raise ValueError("Chưa nhập đủ báo cáo thật khớp checksum của bộ câu hỏi.")
        if not case["gold_pages"] or any(not store.page(document["id"], p) or not store.page_allowed(document["id"], p) for p in case["gold_pages"]):
            raise ValueError("Trang nguồn chuẩn không tồn tại hoặc nằm ngoài phạm vi đã chọn.")
        gold = {(document["id"], p) for p in case["gold_pages"]}
        for anchor in case.get("evidence", []):
            text = quote_text(store.page(document["id"], anchor["page"])["text"])
            if not all(quote_text(q) in text for q in anchor["quotes"]):
                raise ValueError(f"Trích đoạn chuẩn không có trên trang nguồn: {case['id']} trang {anchor['page']}.")
        key = (case["company"], case["report_year"], case["scope"])
        if key not in cohorts:
            cohorts[key] = store.chunks(case["company"], [case["report_year"]], case["scope"], "real")
        chunks = cohorts[key]
        if not chunks or any(c["id"] not in cached for c in chunks):
            raise ValueError("Chỉ mục embedding chưa đủ; chạy index trước khi so sánh các phương án.")
        prepared.append((case, document, gold, chunks))
    documents = {d["id"]: d for d in store.documents()}
    for chunks in cohorts.values():
        for chunk in chunks:
            doc = documents[chunk["document_id"]]
            corpus[chunk["id"]] = {"source_sha256": doc["sha256"], "company": doc["company"],
                                    "report_year": doc["report_year"], "scope": doc["scope"],
                                    "allowed_pages": doc.get("allowed_pages"), "period_start": doc.get("period_start"),
                                    "period_end": doc.get("period_end"), "page": chunk["page"], "text": chunk["text"],
                                    "embedding_sha256": fingerprint(cached[chunk["id"]])}
    results = []
    for case, document, gold, chunks in prepared:
        started = time.perf_counter()
        sparse = bm25(financial_query(case["question"]), chunks, 20)
        sparse_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        vector = embedder.embed([case["question"]])[0]
        embedding_ms = (time.perf_counter() - started) * 1000
        if not vector or any(len(cached[c["id"]]) != len(vector) for c in chunks):
            raise ProviderError("Chiều embedding không khớp; cần lập lại chỉ mục.")
        started = time.perf_counter()
        dense = [{**c, "score": cosine(vector, cached[c["id"]])} for c in chunks]
        dense = sorted((c for c in dense if c["score"] >= 0.25), key=lambda c: (-c["score"], c["id"]))[:20]
        dense_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        hybrid = rrf([sparse, dense], 5)
        fusion_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        context_ranked = contextual_fusion(case["question"], sparse, dense, store.page)
        context_ms = (time.perf_counter() - started) * 1000
        strategies = {}
        for name, hits, latency in [("bm25", sparse, sparse_ms), ("dense", dense, embedding_ms + dense_ms),
                                    ("hybrid_rrf", hybrid, embedding_ms + dense_ms + sparse_ms + fusion_ms),
                                    ("hybrid_context", context_ranked, embedding_ms + dense_ms + sparse_ms + context_ms)]:
            strategies[name] = {**score_ranking(hits, gold),
                                **score_evidence(hits, document["id"], case.get("evidence")),
                                "latency_ms": round(latency, 2),
                                "chunks": [{"document_id": h["document_id"], "page": h["page"], "text": h["text"]} for h in hits[:5]]}
        results.append({"id": case["id"], "company": case["company"], "report_year": case["report_year"],
                        "question": case["question"], "gold_pages": case["gold_pages"], "document_id": document["id"],
                        "source_sha256": document["sha256"], "evidence": case.get("evidence", []),
                        "query_embedding_sha256": fingerprint(vector),
                        "query_embedding_ms": round(embedding_ms, 2), "strategies": strategies})
    summary = {}
    for strategy in ("bm25", "dense", "hybrid_rrf", "hybrid_context"):
        metrics = [r["strategies"][strategy] for r in results]
        latencies = sorted(m["latency_ms"] for m in metrics)
        summary[strategy] = {"recall_at_5": round(statistics.mean(m["recall_at_5"] for m in metrics), 4),
                             "mrr_at_5": round(statistics.mean(m["reciprocal_rank"] for m in metrics), 4),
                             "questions_with_source": sum(m["gold_rank"] is not None for m in metrics),
                             "latency_p50_ms": round(statistics.median(latencies), 2),
                             "latency_p95_ms": latencies[max(0, int(len(latencies) * .95 + .999) - 1)]}
        labeled = [m for m in metrics if m["evidence_recall_at_5"] is not None]
        summary[strategy].update({"evidence_cases": len(labeled),
                                 "evidence_recall_at_5": round(statistics.mean(m["evidence_recall_at_5"] for m in labeled), 4) if labeled else None,
                                 "evidence_complete_questions": sum(m["evidence_complete_at_5"] for m in labeled),
                                 "evidence_mrr_at_5": round(statistics.mean(m["evidence_reciprocal_rank"] for m in labeled), 4) if labeled else None,
                                 "page_hit_without_complete_evidence": sum(m["gold_rank"] is not None and not m["evidence_complete_at_5"] for m in labeled)})
    report = {"kind": "retrieval_ablation_" + dataset_info["split"], "generated_at": datetime.now(timezone.utc).isoformat(),
              "warning": "Dataset split and label provenance are declared by its author, not certified by this tool. Development results are not an independent benchmark.",
              "latency_note": "In-process ranking; dense/hybrid include the shared local query embedding time. Cache loading and HTTP/UI overhead excluded.",
              "cloud_generation_calls": 0, "embedding_model": embed_model, "dense_threshold": 0.25,
              "dataset": {**dataset_info, "sha256": fingerprint({**dataset_info, "cases": cases})},
              "corpus_sha256": fingerprint(sorted(corpus.values(), key=lambda c: (c["company"], c["report_year"], c["scope"], c["source_sha256"], c["page"], c["text"]))),
              "eligible_chunks": len(corpus),
              "implementation_sha256": fingerprint({name: (Path(__file__).parent / name).read_text(encoding="utf-8")
                                                    for name in ("retrieval.py", "retrieval_check.py", "retrieval_dataset.py", "text.py", "store.py", "models.py", "providers.py")}),
              "ranking_config": {"top_k": 5, "shortlist_per_strategy": 20, "rrf_k": 60, "context_pool_limit": 40},
              "evidence_note": "All quotes of an anchor must occur in one top-5 chunk on its source page. Complete-evidence reciprocal rank uses the last required anchor. Literal coverage does not establish answer entailment.",
              "context_reranker": "statement_title_and_row_label; causal questions retain base ranking",
              "cases": len(results), "companies": sorted({r["company"] for r in results}),
              "summary": summary, "results": results}
    report["by_company"] = {}
    for company in report["companies"]:
        rows = [r for r in results if r["company"] == company]
        report["by_company"][company] = {}
        for strategy in summary:
            labeled = [r["strategies"][strategy] for r in rows if r["strategies"][strategy]["evidence_complete_at_5"] is not None]
            report["by_company"][company][strategy] = {
                "cases": len(rows), "questions_with_source": sum(r["strategies"][strategy]["gold_rank"] is not None for r in rows),
                "evidence_cases": len(labeled), "evidence_complete_questions": sum(m["evidence_complete_at_5"] for m in labeled)}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "retrieval-ablation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# So sánh phương án tìm nguồn", "", report["warning"], "", report["latency_note"], "",
             "| Phương án | Câu có nguồn / tổng | Recall@5 | MRR@5 | p50 ms | p95 ms |", "| --- | --- | --- | --- | --- | --- |"]
    for name, metric in summary.items():
        lines.append(f"| {name} | {metric['questions_with_source']}/{len(results)} | {metric['recall_at_5']} | {metric['mrr_at_5']} | {metric['latency_p50_ms']} | {metric['latency_p95_ms']} |")
    lines.extend(["", report["evidence_note"], "", "| Phương án | Đủ bằng chứng / có nhãn | Recall bằng chứng@5 | MRR đủ bằng chứng@5 | Đúng trang nhưng thiếu bằng chứng |",
                  "| --- | --- | --- | --- | --- |"])
    for name, metric in summary.items():
        lines.append(f"| {name} | {metric['evidence_complete_questions']}/{metric['evidence_cases']} | {metric['evidence_recall_at_5']} | {metric['evidence_mrr_at_5']} | {metric['page_hit_without_complete_evidence']} |")
    lines.extend(["", "## Các câu thiếu bằng chứng", ""])
    for row in results:
        failures = [name for name, m in row["strategies"].items() if m["evidence_complete_at_5"] is False]
        if failures:
            lines.append(f"- {row['id']}: {', '.join(failures)}")
    lines.extend(["", f"Dataset SHA-256: {report['dataset']['sha256']}", f"Corpus SHA-256: {report['corpus_sha256']}",
                  f"Implementation SHA-256: {report['implementation_sha256']}"])
    (output / "retrieval-ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
