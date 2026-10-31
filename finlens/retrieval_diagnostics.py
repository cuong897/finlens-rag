"""Evaluation-only candidate coverage; relevance labels never enter retrieval."""
import csv
from pathlib import Path
import statistics

from finlens.retrieval_dataset import evidence_ranks


def candidate_coverage(hits, gold, document_id, evidence, *, limit):
    candidates = hits[:limit]
    found = {(h["document_id"], h["page"]) for h in candidates} & gold
    ranks = evidence_ranks(candidates, document_id, evidence, limit=limit) if evidence else []
    return {"limit": limit, "candidate_count": len(candidates),
            "candidate_chunk_ids": [h["id"] for h in candidates],
            "page_recall": len(found) / len(gold),
            "anchor_recall": sum(r is not None for r in ranks) / len(ranks) if ranks else None,
            "complete_evidence": all(r is not None for r in ranks) if ranks else None,
            "anchor_ranks": ranks}


def summarize_candidates(results):
    summary = {}
    for stage in results[0]["candidates"]:
        rows = [r["candidates"][stage] for r in results]
        labeled = [r for r in rows if r["anchor_recall"] is not None]
        summary[stage] = {"cases": len(rows), "limit": rows[0]["limit"],
                          "mean_candidates": round(statistics.mean(r["candidate_count"] for r in rows), 2),
                          "page_recall": round(statistics.mean(r["page_recall"] for r in rows), 4),
                          "evidence_cases": len(labeled),
                          "anchor_recall": round(statistics.mean(r["anchor_recall"] for r in labeled), 4) if labeled else None,
                          "complete_questions": sum(r["complete_evidence"] for r in labeled)}
    return summary


def export_candidates(results, output):
    fields = ["id", "company", "category", "stage", "limit", "candidate_count", "page_recall",
              "anchor_recall", "complete_evidence", "anchor_ranks"]
    with (Path(output) / "candidate-coverage.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for case in results:
            for stage, score in case["candidates"].items():
                writer.writerow({**{k: case.get(k, "") for k in fields[:3]}, "stage": stage,
                                 **{k: score[k] for k in fields[4:]}})
