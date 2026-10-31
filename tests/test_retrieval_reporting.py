import csv
import json
from pathlib import Path
import tempfile
import unittest

from finlens.retrieval_reporting import export_result_csv, export_review_csv, grouped_scores


class RetrievalReportingTests(unittest.TestCase):
    def rows(self):
        rows = []
        for index, (recall, rr, complete) in enumerate([(1, .5, True), (.5, .2, False), (0, 0, False)]):
            score = {"recall_at_5": recall, "reciprocal_rank": rr, "gold_rank": 2 if recall else None,
                     "evidence_recall_at_5": 1 if complete else 0, "evidence_complete_at_5": complete,
                     "evidence_reciprocal_rank": rr if complete else 0, "latency_ms": 10,
                     "pages": [{"document_id": "a", "page": 1}], "evidence_ranks": [2] if complete else [None]}
            rows.append({"id": f"q{index}", "company": "A" if index < 2 else "B", "category": "lookup",
                         "question": "Doanh thu, năm nào?", "gold_pages": [1], "strategies": {"bm25": score}})
        return rows

    def test_group_means_include_misses_and_use_question_not_page_weighting(self):
        groups = grouped_scores(self.rows(), "company")
        self.assertEqual(groups["A"]["bm25"]["recall_at_5"], .75)
        self.assertEqual(groups["A"]["bm25"]["mrr_at_5"], .35)
        self.assertEqual(groups["A"]["bm25"]["evidence_complete_questions"], 1)
        self.assertEqual(groups["B"]["bm25"]["mrr_at_5"], 0)

    def test_csv_preserves_zeros_misses_utf8_questions_and_exact_per_case_scores(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            export_result_csv({"results": self.rows()}, tmp)
            with (Path(tmp) / "retrieval-cases.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[2]["gold_rank"], "")
            self.assertEqual(rows[2]["reciprocal_rank"], "0")
            self.assertEqual(rows[0]["question"], "Doanh thu, năm nào?")
            self.assertEqual(json.loads(rows[0]["retrieved_pages"])[0]["page"], 1)

    def test_review_export_does_not_claim_an_independent_human_review(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            path = Path(tmp) / "review.csv"
            export_review_csv({"cases": [{"id": "q1", "question": "Doanh thu?", "evidence": [{"page": 1}]}]}, path)
            with path.open(encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))
            self.assertEqual(row["human_review_status"], "pending")
            self.assertEqual(row["reviewer"], "")
