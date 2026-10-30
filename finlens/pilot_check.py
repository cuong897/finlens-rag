"""Reproducible source retrieval and arithmetic checks on the public pilot.

Only local embeddings are called. This check never invokes cloud generation.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

from finlens.engine import Engine
from finlens.finance import compare
from finlens.models import AskRequest
from finlens.providers import ModelConfig
from finlens.public_pilot import manifest

QUERIES = {
    "revenue": "Doanh thu thuần trên báo cáo kết quả kinh doanh hợp nhất năm {year}",
    "net_income": "Lợi nhuận sau thuế toàn tập đoàn năm {year} trong báo cáo kết quả kinh doanh hợp nhất",
    "total_assets": "Tổng tài sản năm {year} trên bảng cân đối kế toán hợp nhất",
}


def check_pilot(store, output_dir="artifacts/public-pilot"):
    engine = Engine(store, ModelConfig(embed_model="bge-m3:latest"))
    results = []
    for source in manifest():
        document = store.find_document(source["company"], source["year"], source["scope"], source["sha256"])
        if not document:
            raise ValueError("Chưa nhập đủ báo cáo. Chạy public-pilot và index trước.")
        for fact in source["facts"]:
            if fact["metric"] not in QUERIES or fact["year"] != source["year"]:
                continue
            question = QUERIES[fact["metric"]].format(year=source["year"])
            hits, mode, warnings = engine.retrieve(AskRequest(question=question, company=source["company"],
                                                            years=[source["year"]], scope=source["scope"], dataset="real"))
            rank = next((i for i, hit in enumerate(hits, 1)
                         if hit["document_id"] == document["id"] and hit["page"] == fact["page"]), None)
            results.append({"question": question, "document_id": document["id"], "gold_page": fact["page"],
                            "rank": rank, "retrieval": mode, "warnings": warnings,
                            "pages": [h["page"] for h in hits], "passed": rank is not None and mode == "hybrid_rrf"})
    comparisons = [compare(store, "FPT", metric, 2023, 2024, dataset="real") for metric in QUERIES]
    report = {"kind": "curated_public_document_pilot_check", "generated_at": datetime.now(timezone.utc).isoformat(),
              "warning": "Curated development queries on known reports; not an independent quality benchmark.",
              "cloud_generation_used": False, "embedding_model": "bge-m3:latest",
              "query_expansion": "deterministic_financial_vi_en",
              "cases": len(results), "passed": sum(r["passed"] for r in results),
              "recall_at_5": sum(r["rank"] is not None for r in results) / len(results),
              "mrr_at_5": statistics.mean(1 / r["rank"] if r["rank"] else 0 for r in results),
              "results": results, "comparisons": comparisons}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "pilot-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
