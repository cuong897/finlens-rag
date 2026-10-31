"""Auditable retrieval result tables; no changes to ranking or relevance labels."""

import csv
import json
from pathlib import Path
import statistics


def grouped_scores(results, field):
    groups = {}
    for value in sorted({r.get(field, "unspecified") for r in results}):
        rows = [r for r in results if r.get(field, "unspecified") == value]
        groups[value] = {}
        for strategy in rows[0]["strategies"]:
            metrics = [r["strategies"][strategy] for r in rows]
            labeled = [m for m in metrics if m["evidence_recall_at_5"] is not None]
            groups[value][strategy] = {
                "cases": len(rows), "recall_at_5": round(statistics.mean(m["recall_at_5"] for m in metrics), 4),
                "mrr_at_5": round(statistics.mean(m["reciprocal_rank"] for m in metrics), 4),
                "questions_with_source": sum(m["gold_rank"] is not None for m in metrics),
                "evidence_cases": len(labeled),
                "evidence_recall_at_5": round(statistics.mean(m["evidence_recall_at_5"] for m in labeled), 4) if labeled else None,
                "evidence_mrr_at_5": round(statistics.mean(m["evidence_reciprocal_rank"] for m in labeled), 4) if labeled else None,
                "evidence_complete_questions": sum(m["evidence_complete_at_5"] for m in labeled),
            }
    return groups


def export_result_csv(report, output):
    """One row per question/strategy for checking means and individual failures."""
    fields = ["id", "company", "category", "question", "strategy", "recall_at_5", "reciprocal_rank", "gold_rank",
              "evidence_recall_at_5", "evidence_complete_at_5", "evidence_reciprocal_rank", "latency_ms",
              "gold_pages", "retrieved_pages", "evidence_ranks"]
    with (Path(output) / "retrieval-cases.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["results"]:
            for strategy, score in row["strategies"].items():
                writer.writerow({"id": row["id"], "company": row["company"], "category": row.get("category", "unspecified"),
                                 "question": row["question"], "strategy": strategy,
                                 **{k: score[k] for k in fields[5:12]},
                                 "gold_pages": json.dumps(row["gold_pages"]),
                                 "retrieved_pages": json.dumps(score["pages"]),
                                 "evidence_ranks": json.dumps(score["evidence_ranks"])})


def export_review_csv(dataset, output):
    """Leave independent human adjudication explicitly pending."""
    fields = ["id", "company", "category", "question", "filename", "source_sha256", "gold_pages", "evidence",
              "reference_answers", "assistant_review", "human_review_status", "reviewer", "review_notes"]
    with Path(output).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in dataset["cases"]:
            row = {k: case.get(k, "") for k in fields[:6]}
            row.update({k: json.dumps(case.get(k, []), ensure_ascii=False) for k in fields[6:9]})
            writer.writerow({**row, "assistant_review": case.get("annotation", {}).get("method", "unspecified"),
                             "human_review_status": "pending", "reviewer": "", "review_notes": ""})
