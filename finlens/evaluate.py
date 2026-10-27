"""Small synthetic mechanism check, not a financial-domain quality benchmark."""

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import tempfile

from finlens.engine import Engine
from finlens.ingest import seed_demo
from finlens.models import AskRequest
from finlens.providers import ModelConfig
from finlens.store import Store


def evaluate(output_dir: str | Path = "artifacts"):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).parent / "fixtures" / "evaluation.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    results, latencies, reciprocal_ranks = [], [], []
    with tempfile.TemporaryDirectory(prefix="evaluation-", dir=output) as work:
        store = Store(work)
        seed_demo(store)
        engine = Engine(store, ModelConfig())  # Never silently use an env-configured model.
        for case in cases:
            request = AskRequest(question=case["question"], company=case["company"], years=case["years"],
                                 scope=case.get("scope", "consolidated"), dataset="demo")
            answer = engine.answer(request)
            checks = {"expected_status": answer["status"] == case["status"]}
            if "growth" in case:
                checks["exact_growth"] = [c["growth_percent"] for c in answer["comparisons"]] == case["growth"]
            if "contains" in case:
                checks["lookup_value"] = case["contains"] in answer["answer"]
            checks["valid_sources"] = all(store.page(c["document_id"], c["page"]) is not None for c in answer["citations"])
            checks["company_isolation"] = all(c["company"] == case["company"] for c in answer["citations"])
            rank = None
            if "gold" in case:
                hits, _, _ = engine.retrieve(request)
                rank = next((i for i, h in enumerate(hits, 1) if [h["document_id"], h["page"]] == case["gold"]), None)
                reciprocal_ranks.append(1 / rank if rank else 0)
                checks["gold_in_top5"] = rank is not None and rank <= 5
            results.append({"id": case["id"], "passed": all(checks.values()), "checks": checks,
                            "expected_status": case["status"], "actual_status": answer["status"],
                            "gold_rank": rank, "latency_ms": answer["latency_ms"]})
            latencies.append(answer["latency_ms"])
        # The temporary evaluation DB is confined to output_dir before cleanup.
        assert Path(work).resolve().is_relative_to(output)
    sorted_latency = sorted(latencies)
    report = {
        "kind": "synthetic_smoke_evaluation", "generated_at": datetime.now(timezone.utc).isoformat(),
        "warning": "Dữ liệu giả lập nhỏ, dùng kiểm tra cơ chế. Không đại diện hiệu quả trên báo cáo tài chính thật.",
        "configuration": {"retrieval": "BM25", "generation": "extractive_and_deterministic", "models_used": False},
        "cases": len(results), "passed": sum(r["passed"] for r in results),
        "retrieval_cases": len(reciprocal_ranks),
        "recall_at_5": round(sum(r > 0 for r in reciprocal_ranks) / len(reciprocal_ranks), 4),
        "mrr_at_5": round(statistics.mean(reciprocal_ranks), 4),
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": sorted_latency[max(0, int(len(sorted_latency) * .95 + .999) - 1)],
        "results": results,
    }
    (output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Đánh giá smoke trên dữ liệu giả lập", "", report["warning"], "",
             f"- Cấu hình: BM25 + trích xuất nguồn / tính toán Decimal, không gọi mô hình.",
             f"- Kiểm tra đạt: {report['passed']}/{report['cases']}.",
             f"- Recall@5: {report['recall_at_5']} trên {report['retrieval_cases']} câu có trang nguồn chuẩn.",
             f"- MRR@5: {report['mrr_at_5']}.",
             f"- Độ trễ p50/p95: {report['latency_p50_ms']}/{report['latency_p95_ms']} ms (trong tiến trình, không tính mạng/giao diện).",
             "", "| Câu | Kết quả |", "| --- | --- |"]
    lines.extend(f"| {r['id']} | {'Đạt' if r['passed'] else 'LỖI: ' + ', '.join(k for k,v in r['checks'].items() if not v)} |" for r in results)
    (output / "evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
