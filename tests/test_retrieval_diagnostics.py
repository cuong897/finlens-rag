import unittest

from finlens.retrieval_dataset import score_evidence
from finlens.retrieval_diagnostics import candidate_coverage, summarize_candidates


class CandidateCoverageTests(unittest.TestCase):
    def test_wider_budget_does_not_change_top5_or_join_split_anchors(self):
        hits = [{"id": i, "document_id": "a", "page": 1, "text": "unrelated"} for i in range(65)]
        hits[5].update(page=2, text="Cash on hand")
        hits[6].update(page=2, text="1,234")
        hits[60].update(page=2, text="Cash on hand 1,234")
        evidence = [{"page": 2, "quotes": ["Cash on hand", "1,234"]}]
        gold = {("a", 2)}
        self.assertFalse(score_evidence(hits, "a", evidence)["evidence_complete_at_5"])
        sixty = candidate_coverage(hits, gold, "a", evidence, limit=60)
        self.assertEqual(sixty["page_recall"], 1)
        self.assertEqual(sixty["anchor_recall"], 0)
        wider = candidate_coverage(hits, gold, "a", evidence, limit=120)
        self.assertEqual(wider["anchor_ranks"], [61])
        self.assertEqual(wider["candidate_count"], 65)
        self.assertTrue(wider["complete_evidence"])

    def test_wrong_source_and_partial_multi_page_coverage_keep_misses(self):
        evidence = [{"page": p, "quotes": ["Revenue", str(p)]} for p in [1, 2]]
        hits = [{"id": 1, "document_id": "b", "page": 2, "text": "Revenue 2"},
                {"id": 2, "document_id": "a", "page": 1, "text": "Revenue 1"}]
        score = candidate_coverage(hits, {("a", 1), ("a", 2)}, "a", evidence, limit=120)
        self.assertEqual(score["page_recall"], .5)
        self.assertEqual(score["anchor_recall"], .5)
        summary = summarize_candidates([{"candidates": {"pool": score}}])["pool"]
        self.assertEqual(summary["complete_questions"], 0)
        self.assertEqual(summary["evidence_cases"], 1)
