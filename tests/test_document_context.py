from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
import pymupdf

from finlens.api import create_app
from finlens.engine import Engine
from finlens.finance import EvidenceError, select_fact
from finlens.ingest import ingest_pdf
from finlens.models import AskRequest
from finlens.providers import ModelConfig, grounded_request
from finlens.public_pilot import verify_fact_anchor


class DocumentContextTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.app = create_app(self.tmp.name, ModelConfig())
        self.store = self.app.state.store
        with pymupdf.open() as pdf:
            for text in ["SEPARATE revenue year 2024 is 999 VND. Parent-only statement.",
                         "CONSOLIDATED revenue year 2024 is 123 VND. Group statement."]:
                pdf.new_page().insert_text((40, 50), text)
            result = ingest_pdf(self.store, pdf.tobytes(), "mixed.pdf", "Company", 2024, "consolidated")
        self.doc = result["document"]["id"]
        self.fact = {"document_id": self.doc, "page": 1, "metric": "revenue", "year": 2024,
                     "value": "999", "unit": "one", "currency": "VND", "verified": True, "note": "Reviewed source for the test."}
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        assert Path(self.tmp.name).resolve().is_relative_to(self.root.resolve())
        self.tmp.cleanup()

    def restrict(self):
        self.store.record_context(self.doc, allowed_pages=[2], period_start="2024-04-01",
                                  period_end="2025-03-31", period_label="FY2024 ends March 2025")

    def test_page_filter_applies_before_keyword_and_dense_retrieval(self):
        self.restrict()
        self.assertEqual({c["page"] for c in self.store.chunks()}, {2})
        class Dense:
            def embed(self, texts):
                return [[1, 0] for _ in texts]
        engine = Engine(self.store, ModelConfig(embed_model="test"), Dense())
        engine.index_embeddings()
        hits, mode, _ = engine.retrieve(AskRequest(question="revenue", company="Company", years=[2024]))
        self.assertEqual(mode, "hybrid_rrf")
        self.assertTrue(hits)
        self.assertTrue(all(h["page"] == 2 for h in hits))

    def test_existing_fact_on_excluded_page_is_retained_but_not_used(self):
        self.store.add_fact(self.fact)
        self.restrict()
        self.assertEqual(self.store.facts(), [])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 1)
        with self.assertRaises(EvidenceError):
            select_fact(self.store, "Company", "revenue", 2024, "consolidated", "real")

    def test_direct_and_api_confirmation_and_suggestions_reject_excluded_page(self):
        self.restrict()
        with self.assertRaises(ValueError):
            self.store.add_fact(self.fact)
        payload = {k: v for k, v in self.fact.items() if k != "verified"}
        payload["confirmed"] = True
        self.assertEqual(self.client.post("/api/facts", json=payload).status_code, 409)
        self.assertEqual(self.client.get(f"/api/documents/{self.doc}/suggestions?page=1").status_code, 409)

    def test_source_pdf_and_original_page_numbers_remain_available(self):
        self.restrict()
        self.assertEqual(self.client.get(f"/api/documents/{self.doc}/pdf").status_code, 200)
        excluded = self.client.get(f"/api/documents/{self.doc}/pages/1").json()
        self.assertFalse(excluded["in_scope"])
        self.assertIn("SEPARATE", excluded["text"])
        allowed = self.client.get(f"/api/documents/{self.doc}/pages/2").json()
        self.assertTrue(allowed["in_scope"])
        self.assertIn("CONSOLIDATED", allowed["text"])

    def test_period_metadata_survives_reopening_and_is_exposed_to_ui(self):
        self.restrict()
        from finlens.store import Store
        reopened = Store(self.tmp.name).get_document(self.doc)
        self.assertEqual(reopened["period_end"], "2025-03-31")
        self.assertEqual(reopened["allowed_pages"], [2])
        listed = self.client.get("/api/documents").json()[0]
        self.assertEqual(listed["period_label"], "FY2024 ends March 2025")

    def test_unrestricted_documents_keep_all_pages_and_bad_context_is_rejected(self):
        self.assertEqual({c["page"] for c in self.store.chunks()}, {1, 2})
        for pages in [[], [0], [3], [True], [1.0]]:
            with self.assertRaises(ValueError):
                self.store.record_context(self.doc, allowed_pages=pages)
        with self.assertRaises(ValueError):
            self.store.record_context(self.doc, period_start="2025-01-01")
        with self.assertRaises(ValueError):
            self.store.record_context(self.doc, period_start="2025-01-01", period_end="2024-01-01")

    def test_period_context_is_sent_with_grounded_evidence(self):
        import json
        _, _, prompt = grounded_request("Revenue?", [{"id": "S1", "excerpt": "Statement", "report_year": 2024,
                                                     "period_label": "FY2024 ends March 2025"}])
        self.assertEqual(json.loads(prompt)["evidence"][0]["period_label"], "FY2024 ends March 2025")

    def test_new_document_is_committed_with_scope_already_attached(self):
        with pymupdf.open() as pdf:
            for text in ["Parent-only revenue statement, must be excluded.", "Group revenue statement, eligible for retrieval."]:
                pdf.new_page().insert_text((40, 50), text)
            result = ingest_pdf(self.store, pdf.tobytes(), "atomic.pdf", "Atomic", 2024, "consolidated",
                                context={"allowed_pages": [2], "period_label": "Group only"})
        document = result["document"]
        self.assertEqual(document["allowed_pages"], [2])
        self.assertEqual({c["page"] for c in self.store.chunks(company="Atomic")}, {2})

    def test_anchor_accepts_dotted_thousands_and_normalizes_ligatures(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 50), "Net profit after tax 1.234.567 VND")
            verify_fact_anchor(pdf, {"page": 1, "label": "Net profit after tax", "value": "1234567"})
