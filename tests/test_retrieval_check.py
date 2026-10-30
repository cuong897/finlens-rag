import json
from pathlib import Path
import tempfile
import unittest

from finlens.providers import ProviderError
from finlens.retrieval_check import evaluate_retrieval, score_ranking
from finlens.store import Store


class RetrievalCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.store = Store(self.tmp.name)
        for doc, company, sha in [("a", "A", "aaa"), ("b", "B", "bbb")]:
            self.store.add_document({"id": doc, "company": company, "report_year": 2024,
                                     "scope": "consolidated", "filename": doc + ".pdf", "sha256": sha,
                                     "is_demo": False, "pdf_path": None}, ["Revenue repeated revenue value.", "Net sales after deductions."])
        self.store.save_embeddings("test", [(c["id"], [0, 1] if c["page"] == 1 else [1, 0]) for c in self.store.chunks()])
        self.cases = [{"id": "q1", "company": "A", "report_year": 2024, "scope": "consolidated",
                       "filename": "a.pdf", "question": "Revenue", "gold_pages": [2]}]
        self.reports = [{"company": "A", "year": 2024, "scope": "consolidated", "filename": "a.pdf", "sha256": "aaa"}]
        class Embedding:
            calls = 0
            def embed(self, texts):
                self.calls += 1
                return [[1, 0] for _ in texts]
            def generate(self, *args):
                raise AssertionError("Evaluation must not invoke generation")
        self.embedding = Embedding()

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.root.resolve())
        self.tmp.cleanup()

    def run_report(self):
        return evaluate_retrieval(self.store, Path(self.tmp.name) / "out", cases=self.cases,
                                  reports=self.reports, embedder=self.embedding, embed_model="test")

    def test_same_scope_and_query_embedding_are_used_for_all_strategies(self):
        report = self.run_report()
        self.assertEqual(self.embedding.calls, 1)
        self.assertEqual(report["cloud_generation_calls"], 0)
        self.assertEqual(report["summary"]["bm25"]["recall_at_5"], 0)
        self.assertEqual(report["summary"]["dense"]["recall_at_5"], 1)
        for strategy in report["results"][0]["strategies"].values():
            self.assertTrue(all(p["document_id"] == "a" for p in strategy["pages"]))
        saved = json.loads((Path(self.tmp.name) / "out" / "retrieval-ablation.json").read_text())
        self.assertEqual(saved["cases"], 1)

    def test_missing_embeddings_do_not_silently_replace_dense_with_bm25(self):
        self.store.clear_embeddings("test")
        with self.assertRaisesRegex(ValueError, "embedding"):
            self.run_report()
        self.assertEqual(self.embedding.calls, 0)

    def test_excluded_gold_and_unknown_source_are_rejected(self):
        self.store.record_context("a", allowed_pages=[1])
        with self.assertRaisesRegex(ValueError, "ngoài phạm vi"):
            self.run_report()
        self.cases[0]["filename"] = "unknown.pdf"
        with self.assertRaises(ValueError):
            self.run_report()

    def test_cache_dimensions_must_match_query_embedding(self):
        self.store.save_embeddings("test", [(c["id"], [1, 0, 0]) for c in self.store.chunks()])
        with self.assertRaises(ProviderError):
            self.run_report()

    def test_duplicate_cases_are_rejected(self):
        self.cases *= 2
        with self.assertRaises(ValueError):
            self.run_report()

    def test_gold_page_recall_counts_distinct_pages_and_uses_first_relevant_chunk(self):
        hits = [{"document_id": "a", "page": 1}, {"document_id": "a", "page": 2},
                {"document_id": "a", "page": 2}, {"document_id": "b", "page": 3}]
        score = score_ranking(hits, {("a", 2), ("a", 3)})
        self.assertEqual(score["recall_at_5"], .5)
        self.assertEqual(score["reciprocal_rank"], .5)
