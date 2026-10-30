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
from finlens.retrieval import bm25, cosine, financial_query, rrf


def score_ranking(hits, gold):
    ranks = [i for i, hit in enumerate(hits[:5], 1) if (hit["document_id"], hit["page"]) in gold]
    found = {(h["document_id"], h["page"]) for h in hits[:5]} & gold
    return {"recall_at_5": len(found) / len(gold), "reciprocal_rank": 1 / ranks[0] if ranks else 0,
            "gold_rank": ranks[0] if ranks else None,
            "pages": [{"document_id": h["document_id"], "page": h["page"]} for h in hits[:5]]}


def evaluate_retrieval(store, output_dir="artifacts/retrieval", *, cases=None, reports=None,
                       embedder=None, embed_model="bge-m3:latest"):
    cases = cases if cases is not None else json.loads((Path(__file__).parent / "fixtures" / "retrieval_cases.json").read_text(encoding="utf-8"))
    reports = reports if reports is not None else manifest()
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Bộ câu hỏi trống hoặc có mã câu bị trùng.")
    embedder = embedder or Ollama(ModelConfig(embed_model=embed_model))
    cached = store.embeddings(embed_model)
    results = []
    for case in cases:
        source = next((r for r in reports if r["filename"] == case["filename"] and r["company"] == case["company"]
                       and r["year"] == case["report_year"] and r["scope"] == case["scope"]), None)
        if not source:
            raise ValueError("Câu hỏi không khớp tài liệu nguồn trong registry.")
        document = store.find_document(source["company"], source["year"], source["scope"], source["sha256"])
        if not document:
            raise ValueError("Chưa nhập đủ báo cáo; chạy public-pilot trước.")
        if not case["gold_pages"] or any(not store.page(document["id"], p) or not store.page_allowed(document["id"], p) for p in case["gold_pages"]):
            raise ValueError("Trang nguồn chuẩn không tồn tại hoặc nằm ngoài phạm vi đã chọn.")
        gold = {(document["id"], p) for p in case["gold_pages"]}
        chunks = store.chunks(case["company"], [case["report_year"]], case["scope"], "real")
        if not chunks or any(c["id"] not in cached for c in chunks):
            raise ValueError("Chỉ mục embedding chưa đủ; chạy index trước khi so sánh ba phương án.")
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
        strategies = {}
        for name, hits, latency in [("bm25", sparse, sparse_ms), ("dense", dense, embedding_ms + dense_ms),
                                    ("hybrid_rrf", hybrid, embedding_ms + dense_ms + sparse_ms + fusion_ms)]:
            strategies[name] = {**score_ranking(hits, gold), "latency_ms": round(latency, 2)}
        results.append({"id": case["id"], "company": case["company"], "report_year": case["report_year"],
                        "question": case["question"], "gold_pages": case["gold_pages"], "document_id": document["id"],
                        "query_embedding_ms": round(embedding_ms, 2), "strategies": strategies})
    summary = {}
    for strategy in ("bm25", "dense", "hybrid_rrf"):
        metrics = [r["strategies"][strategy] for r in results]
        latencies = sorted(m["latency_ms"] for m in metrics)
        summary[strategy] = {"recall_at_5": round(statistics.mean(m["recall_at_5"] for m in metrics), 4),
                             "mrr_at_5": round(statistics.mean(m["reciprocal_rank"] for m in metrics), 4),
                             "questions_with_source": sum(m["gold_rank"] is not None for m in metrics),
                             "latency_p50_ms": round(statistics.median(latencies), 2),
                             "latency_p95_ms": latencies[max(0, int(len(latencies) * .95 + .999) - 1)]}
    report = {"kind": "retrieval_ablation_development", "generated_at": datetime.now(timezone.utc).isoformat(),
              "warning": "Small assistant-authored development set on reviewed known reports; not an independent benchmark.",
              "latency_note": "In-process ranking; dense/hybrid include the shared local query embedding time. Cache loading and HTTP/UI overhead excluded.",
              "cloud_generation_calls": 0, "embedding_model": embed_model, "dense_threshold": 0.25,
              "cases": len(results), "companies": sorted({r["company"] for r in results}),
              "summary": summary, "results": results}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "retrieval-ablation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# So sánh phương án tìm nguồn", "", report["warning"], "", report["latency_note"], "",
             "| Phương án | Câu có nguồn / tổng | Recall@5 | MRR@5 | p50 ms | p95 ms |", "| --- | --- | --- | --- | --- | --- |"]
    for name, metric in summary.items():
        lines.append(f"| {name} | {metric['questions_with_source']}/{len(results)} | {metric['recall_at_5']} | {metric['mrr_at_5']} | {metric['latency_p50_ms']} | {metric['latency_p95_ms']} |")
    (output / "retrieval-ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
