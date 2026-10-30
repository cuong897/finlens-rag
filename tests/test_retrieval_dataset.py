import copy
import json
from pathlib import Path
import tempfile
import unittest

from finlens.public_pilot import manifest
from finlens.retrieval_check import evaluate_retrieval, score_ranking
from finlens.retrieval_dataset import load_dataset, score_evidence, validate_cases
from finlens.store import Store


class EvidenceScoringTests(unittest.TestCase):
    def test_correct_page_is_not_enough_and_quotes_cannot_be_joined_across_chunks(self):
        anchors = [{"page": 2, "quotes": ["Net revenue", "8,200,000"]}]
        hits = [{"document_id": "a", "page": 2, "text": "Net revenue heading"},
                {"document_id": "a", "page": 2, "text": "Some other row 8,200,000"}]
        self.assertEqual(score_ranking(hits, {("a", 2)})["recall_at_5"], 1)
        self.assertEqual(score_evidence(hits, "a", anchors)["evidence_recall_at_5"], 0)
        hits.append({"document_id": "b", "page": 2, "text": "Net revenue 8,200,000"})
        self.assertFalse(score_evidence(hits, "a", anchors)["evidence_complete_at_5"])

    def test_all_anchors_must_be_present_and_rank_is_last_required_evidence(self):
        evidence = [{"page": p, "quotes": ["Profit", str(p)]} for p in [1, 2]]
        hits = [{"document_id": "a", "page": p, "text": f"Profit {p}"} for p in [1, 1, 2]]
        result = score_evidence(hits, "a", evidence)
        self.assertEqual(result["evidence_recall_at_5"], 1)
        self.assertEqual(result["evidence_reciprocal_rank"], 1 / 3)
        self.assertEqual(score_evidence(hits[:2], "a", evidence)["evidence_recall_at_5"], .5)
        self.assertEqual(score_evidence(hits[:2], "a", evidence)["evidence_reciprocal_rank"], 0)

    def test_literal_matching_folds_whitespace_and_ligatures_but_preserves_numbers_and_signs(self):
        hit = {"document_id": "a", "page": 1, "text": "Net proﬁt\n after tax (1,234)"}
        for quote, expected in [("net profit after tax", True), ("(1,234)", True), ("1.234", False), ("-1,234", False)]:
            self.assertEqual(score_evidence([hit], "a", [{"page": 1, "quotes": [quote]}])["evidence_complete_at_5"], expected)

    def test_unlabeled_cases_are_not_reported_as_zero_accuracy(self):
        self.assertIsNone(score_evidence([], "a", None)["evidence_recall_at_5"])


class DatasetIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.path = Path(self.tmp.name)
        self.store = Store(self.path)
        self.store.add_document({"id": "a", "company": "A", "report_year": 2024, "scope": "consolidated",
                                 "filename": "new.pdf", "sha256": "a" * 64, "is_demo": False, "pdf_path": None},
                                ["Revenue heading only.", "Net revenue 8,200,000 VND."])
        self.store.save_embeddings("test", [(c["id"], [1, 0]) for c in self.store.chunks()])
        self.dataset = {"version": 1, "name": "external-review", "split": "test", "label_origin": "test fixture author",
                        "cases": [{"id": "q1", "company": "A", "report_year": 2024, "scope": "consolidated",
                                   "filename": "new.pdf", "source_sha256": "a" * 64, "question": "Net revenue",
                                   "gold_pages": [2], "evidence": [{"page": 2, "quotes": ["Net revenue", "8,200,000"]}]}]}
        class Embedding:
            calls = 0
            def embed(self, texts):
                self.calls += 1
                return [[1, 0]]
        self.embedding = Embedding()

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        path = self.path / "cases.json"
        path.write_text(json.dumps(self.dataset), encoding="utf-8")
        return load_dataset(path)

    def run_report(self):
        data = self.load()
        return evaluate_retrieval(self.store, self.path / "out", cases=data["cases"], embed_model="test",
                                  embedder=self.embedding, dataset_info={k: data[k] for k in ("name", "split", "label_origin")})

    def test_external_reports_need_no_pilot_registry_and_reports_are_auditable(self):
        result = self.run_report()
        metric = result["summary"]["hybrid_context"]
        self.assertEqual(metric["evidence_cases"], 1)
        self.assertEqual(metric["evidence_complete_questions"], 1)
        self.assertEqual(result["dataset"]["split"], "test")
        self.assertEqual(self.embedding.calls, 1)
        saved = json.loads((self.path / "out/retrieval-ablation.json").read_text(encoding="utf-8"))
        self.assertIn("text", saved["results"][0]["strategies"]["dense"]["chunks"][0])
        self.assertEqual(saved["results"][0]["source_sha256"], "a" * 64)

    def test_invalid_later_quote_fails_before_any_embedding_or_report_write(self):
        second = copy.deepcopy(self.dataset["cases"][0])
        second["id"] = "q2"
        second["evidence"][0]["quotes"] = ["Invented source content"]
        self.dataset["cases"].append(second)
        with self.assertRaisesRegex(ValueError, "q2"):
            self.run_report()
        self.assertEqual(self.embedding.calls, 0)
        self.assertFalse((self.path / "out").exists())

    def test_test_split_cannot_reuse_builtin_development_pdfs(self):
        self.dataset["cases"][0]["source_sha256"] = manifest()[0]["sha256"]
        with self.assertRaisesRegex(ValueError, "trùng PDF"):
            self.load()
        with self.assertRaisesRegex(ValueError, "trùng PDF"):
            evaluate_retrieval(self.store, self.path / "out", cases=self.dataset["cases"],
                               dataset_info={k: self.dataset[k] for k in ("name", "split", "label_origin")})
        self.dataset["split"] = "development"
        self.assertEqual(self.load()["split"], "development")

    def test_missing_labels_malformed_pages_hashes_and_duplicate_ids_are_rejected(self):
        original = copy.deepcopy(self.dataset["cases"][0])
        validate_cases([{**original, "scope": "standalone"}], strict=True)
        for key, value in [("gold_pages", [True]), ("gold_pages", [2, 2]), ("source_sha256", "wrong"),
                           ("scope", "separate"),
                           ("evidence", []), ("evidence", [{"page": 1, "quotes": ["x"]}]),
                           ("evidence", [{"page": 2, "quotes": [" "]}]), ("question", "")]:
            case = {**original, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_cases([case], strict=True)
        with self.assertRaises(ValueError):
            validate_cases([original, original], strict=True)

    def test_fingerprints_ignore_runtime_latency_but_track_labels_and_corpus_changes(self):
        first = self.run_report()
        same = self.run_report()
        self.assertEqual(first["dataset"]["sha256"], same["dataset"]["sha256"])
        self.assertEqual(first["corpus_sha256"], same["corpus_sha256"])
        self.dataset["cases"][0]["question"] = "Revenue in this year"
        relabeled = self.run_report()
        self.assertNotEqual(first["dataset"]["sha256"], relabeled["dataset"]["sha256"])
        self.assertEqual(first["corpus_sha256"], relabeled["corpus_sha256"])
        self.store.save_embeddings("test", [(c["id"], [0, 1]) for c in self.store.chunks()])
        changed = self.run_report()
        self.assertNotEqual(first["corpus_sha256"], changed["corpus_sha256"])

    def test_page_scope_is_checked_before_embedding(self):
        self.store.record_context("a", allowed_pages=[1])
        with self.assertRaisesRegex(ValueError, "ngoài phạm vi"):
            self.run_report()
        self.assertEqual(self.embedding.calls, 0)
