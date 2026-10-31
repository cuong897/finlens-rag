import hashlib
from pathlib import Path
import tempfile
import unittest

import pymupdf

from finlens.benchmark import audit_benchmark, benchmark_reports, import_benchmark
from finlens.ingest import IngestError
from finlens.public_pilot import manifest
from finlens.retrieval_dataset import load_dataset
from finlens.store import Store


class BenchmarkImportTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.store = Store(self.tmp.name)
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((70, 70), "Consolidated revenue 123456 VND; fictional test only.")
            self.content = pdf.tobytes()
        self.report = {"company": "TEST", "year": 2024, "scope": "consolidated", "filename": "test.pdf",
                       "sha256": hashlib.sha256(self.content).hexdigest(), "pages": 1,
                       "url": "https://example.com/test.pdf", "publisher": "Test fixture",
                       "period_start": "2024-01-01", "period_end": "2024-12-31", "period_label": "2024"}
        cache = self.store.root / "benchmark-sources"
        cache.mkdir()
        (cache / "test.pdf").write_bytes(self.content)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_is_idempotent_retains_full_corpus_and_never_confirms_facts(self):
        first = import_benchmark(self.store, reports=[self.report], client=object())
        second = import_benchmark(self.store, reports=[self.report], client=object())
        self.assertTrue(first["documents"][0]["created"])
        self.assertFalse(second["documents"][0]["created"])
        self.assertEqual(self.store.facts(), [])
        doc = self.store.documents()[0]
        self.assertIsNone(doc["allowed_pages"])
        self.assertEqual(doc["source_url"], self.report["url"])
        self.assertEqual(self.store.source_path(doc["id"]).read_bytes(), self.content)

    def test_changed_content_page_count_and_development_company_fail_before_import(self):
        for change in [{"company": manifest()[0]["company"]}, {"sha256": "0" * 64}, {"pages": 2}]:
            with self.subTest(change=change), self.assertRaises(IngestError):
                import_benchmark(self.store, reports=[{**self.report, **change}], client=object())
        self.assertEqual(self.store.documents(), [])

    def test_registry_is_disjoint_from_development_and_contains_two_new_companies(self):
        reports = benchmark_reports()
        self.assertEqual({r["company"] for r in reports}, {"HPG", "PNJ"})
        self.assertFalse({r["sha256"] for r in reports} & {r["sha256"] for r in manifest()})
        self.assertTrue(all(not r.get("allowed_pages") for r in reports))

    def test_versioned_questions_have_distinct_text_and_declared_label_provenance(self):
        path = Path(__file__).resolve().parents[1] / "finlens/fixtures/benchmark_development_v1.json"
        dataset = load_dataset(path)
        self.assertEqual(dataset["split"], "development")
        cases = dataset["cases"]
        self.assertEqual(len(cases), 108)
        self.assertEqual(len({c["question"] for c in cases}), 108)
        self.assertEqual({c["company"] for c in cases}, {r["company"] for r in benchmark_reports()})
        self.assertEqual({c["category"] for c in cases}, {"statement_lookup", "note_lookup", "multi_value", "year_comparison"})
        self.assertEqual(dataset["protocol"]["frozen_retrieval_commit"], "ed4a45f")
        self.assertTrue(all(c["annotation"]["independent_review"] is False for c in cases))

    def test_label_audit_detects_wrong_values_duplicate_questions_and_page_filtering(self):
        import copy
        result = import_benchmark(self.store, reports=[self.report], client=object())
        case = {"id": "q1", "company": "TEST", "report_year": 2024, "scope": "consolidated", "filename": "test.pdf",
                "source_sha256": self.report["sha256"], "question": "Revenue?", "gold_pages": [1],
                "evidence": [{"page": 1, "quotes": ["revenue", "123456"]}], "category": "statement_lookup",
                "target_ids": ["revenue"], "reference_answers": [{"raw_value": "123456", "value": "123456"}]}
        dataset = {"cases": [case], "split": "test"}
        self.assertEqual(audit_benchmark(self.store, dataset)["cases"], 1)
        bad = copy.deepcopy(dataset); bad["cases"][0]["reference_answers"][0]["value"] = "1"
        with self.assertRaisesRegex(ValueError, "Giá trị"):
            audit_benchmark(self.store, bad)
        bad = copy.deepcopy(dataset); bad["cases"].append({**case, "id": "q2"})
        with self.assertRaisesRegex(ValueError, "trùng nội dung"):
            audit_benchmark(self.store, bad)
        self.store.record_context(result["documents"][0]["document_id"], allowed_pages=[1])
        with self.assertRaisesRegex(ValueError, "toàn bộ"):
            audit_benchmark(self.store, dataset)
