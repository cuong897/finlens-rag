from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient
import pymupdf

from finlens.api import create_app
from finlens.engine import Engine, valid_citations
from finlens.finance import EvidenceError, compare
from finlens.ingest import seed_demo
from finlens.models import AskRequest
from finlens.providers import ModelConfig, Ollama, ProviderError
from finlens.store import Store


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.store = Store(self.tmp.name)
        seed_demo(self.store)
        self.engine = Engine(self.store, ModelConfig())

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.test_root.resolve())
        self.tmp.cleanup()

    def request(self, question, **kwargs):
        data = {"question": question, "company": "An Phát Demo", "dataset": "demo"}
        data.update(kwargs)
        return AskRequest(**data)

    def add_fact(self, metric="revenue", year=2024, value="0", unit="billion", currency="VND"):
        return self.store.add_fact({"document_id": f"demo-ANPHAT-{year}", "page": 1,
                                   "metric": metric, "year": year, "value": value,
                                   "unit": unit, "currency": currency, "verified": True, "note": "test conflict"})

    def test_decimal_comparison_and_sources(self):
        result = compare(self.store, "An Phát Demo", "revenue", 2023, 2024, dataset="demo")
        self.assertEqual(result["growth_percent"], "25.00")
        self.assertEqual(result["delta"], "250")
        self.assertTrue(result["is_demo"])
        for citation in result["citations"]:
            self.assertIsNotNone(self.store.page(citation["document_id"], citation["page"]))

    def test_missing_or_wrong_scope_refuses(self):
        for scope, dataset in [("standalone", "demo"), ("consolidated", "real")]:
            with self.assertRaises(EvidenceError):
                compare(self.store, "An Phát Demo", "revenue", 2023, 2024, scope=scope, dataset=dataset)

    def test_conflicting_verified_values_refuse(self):
        self.add_fact(value="999")
        with self.assertRaisesRegex(EvidenceError, "mâu thuẫn"):
            compare(self.store, "An Phát Demo", "revenue", 2023, 2024, dataset="demo")

    def test_units_are_normalized_before_comparing(self):
        self.add_fact(value="1250000", unit="million")
        result = compare(self.store, "An Phát Demo", "revenue", 2023, 2024, dataset="demo")
        self.assertEqual(Decimal(result["to_value"]), Decimal("1250"))

    def test_currency_conflict_refuses(self):
        self.add_fact(value="1250", currency="USD")
        with self.assertRaises(EvidenceError):
            compare(self.store, "An Phát Demo", "revenue", 2023, 2024, dataset="demo")

    def test_zero_and_negative_base_no_growth_percentage(self):
        for base in ["0", "-12"]:
            with self.store.connection() as db:
                db.execute("UPDATE facts SET value=? WHERE document_id='demo-ANPHAT-2023' AND metric='revenue'", (base,))
            result = compare(self.store, "An Phát Demo", "revenue", 2023, 2024, dataset="demo")
            self.assertIsNone(result["growth_percent"])
            self.assertIsNone(result["formula"])

    def test_multimetric_question_calculates_without_llm(self):
        result = self.engine.answer(self.request("So sánh doanh thu và lợi nhuận sau thuế năm 2023 và 2024"))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["mode"], "calculated")
        self.assertEqual([c["growth_percent"] for c in result["comparisons"]], ["25.00", "-10.00"])
        self.assertEqual(len({c["id"] for c in result["citations"]}), 4)

    def test_single_year_value_and_filter_conflict(self):
        result = self.engine.answer(self.request("Doanh thu năm 2024 là bao nhiêu?"))
        self.assertEqual(result["mode"], "calculated")
        self.assertIn("1250", result["answer"])
        conflict = self.engine.answer(self.request("Doanh thu năm 2024?", years=[2023]))
        self.assertEqual(conflict["status"], "insufficient_evidence")
        self.assertEqual(conflict["citations"], [])

    def test_unknown_question_no_evidence(self):
        result = self.engine.answer(self.request("xyzunknown kryptonite?", years=[2024]))
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_year_alone_does_not_count_as_semantic_evidence(self):
        result = self.engine.answer(self.request("CEO nhận lương bao nhiêu năm 2024?", years=[2024]))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["citations"], [])

    def test_why_question_not_misrouted_to_two_year_comparison(self):
        result = self.engine.answer(self.request("Vì sao tăng khoản phải thu khách hàng?", years=[2024]))
        self.assertEqual(result["status"], "evidence_found")
        self.assertTrue(any(c["page"] == 2 for c in result["citations"]))

    def test_extractive_response_is_explicit(self):
        result = self.engine.answer(self.request("Vì sao chi phí lãi vay tăng?", years=[2024]))
        self.assertEqual(result["status"], "evidence_found")
        self.assertEqual(result["mode"], "extractive")
        self.assertIn("Các đoạn nguồn", result["answer"])
        self.assertTrue(result["citations"])
        self.assertTrue(all(c["company"] == "An Phát Demo" and c["report_year"] == 2024 for c in result["citations"]))
        self.assertTrue(all(c["page"] == 2 for c in result["citations"]), "Do not cite generic table headers as explanations")

    def test_invalid_model_citation_falls_back(self):
        class Fake:
            def generate(self, question, evidence):
                return "Công ty tăng trưởng 900%. [S99]"
        engine = Engine(self.store, ModelConfig(chat_model="fake"), Fake())
        result = engine.answer(self.request("Vì sao chi phí lãi vay tăng?", years=[2024]))
        self.assertEqual(result["mode"], "extractive")
        self.assertNotIn("900%", result["answer"])
        self.assertTrue(result["warnings"])

    def test_valid_generated_response(self):
        class Fake:
            def generate(self, question, evidence):
                return "Thuyết minh ghi nhận chi phí lãi vay tăng. [S1]"
        engine = Engine(self.store, ModelConfig(chat_model="fake"), Fake())
        result = engine.answer(self.request("Vì sao chi phí lãi vay tăng?", years=[2024]))
        self.assertEqual(result["mode"], "generated")
        self.assertEqual(len(result["citations"]), 1)

    def test_citation_validation_requires_each_paragraph(self):
        evidence = [{"id": "S1"}]
        self.assertFalse(valid_citations("Unsupported.\n\nSupported. [S1]", evidence))
        self.assertFalse(valid_citations("Made up. [S2]", evidence))
        self.assertTrue(valid_citations("Supported. [S1]", evidence))

    def test_hybrid_index_and_query_with_fake_provider(self):
        class Fake:
            def embed(self, texts):
                return [[1.0, 0.5] for _ in texts]
        engine = Engine(self.store, ModelConfig(embed_model="fake"), Fake())
        first = engine.index_embeddings()
        self.assertGreater(first["indexed"], 0)
        self.assertEqual(engine.index_embeddings()["indexed"], 0)
        hits, mode, warnings = engine.retrieve(self.request("chi phí", years=[2024]))
        self.assertEqual(mode, "hybrid_rrf")
        self.assertTrue(hits)
        self.assertEqual(warnings, [])
        self.assertTrue(all(h["report_year"] == 2024 and h["company"] == "An Phát Demo" for h in hits))

    def test_api_upload_confirm_calculate_and_revoke(self):
        with TestClient(create_app(self.tmp.name, ModelConfig())) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.post("/api/demo", headers={"Origin": "https://elsewhere.example"}).status_code, 403)
            self.assertEqual(client.post("/api/documents?company=Test&year=2024", content=b"bad").status_code, 415)
            ids = []
            for year, value in [(2023, "10.25"), (2024, "12.30")]:
                with pymupdf.open() as pdf:
                    page = pdf.new_page()
                    page.insert_text((40, 50), f"Revenue in {year} is {value} billion VND. Financial statement.")
                    response = client.post(f"/api/documents?company=Test&year={year}", content=pdf.tobytes(), headers={"Content-Type": "application/pdf"})
                self.assertEqual(response.status_code, 200, response.text)
                did = response.json()["document"]["id"]
                ids.append(did)
                payload = {"document_id": did, "page": 1, "metric": "revenue", "year": year,
                           "value": value, "unit": "billion", "currency": "VND", "confirmed": True,
                           "note": "Manually checked against source page 1"}
                response = client.post("/api/facts", json=payload)
                self.assertEqual(response.status_code, 201, response.text)
                fact_id = response.json()["id"]
            response = client.post("/api/compare", json={"company": "Test", "metric": "revenue", "from_year": 2023, "to_year": 2024})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["growth_percent"], "20.00")
            self.assertEqual(client.get(f"/api/documents/{ids[0]}/pdf").headers["content-type"], "application/pdf")
            self.assertEqual(client.delete(f"/api/facts/{fact_id}").status_code, 200)
            self.assertEqual(client.post("/api/compare", json={"company": "Test", "metric": "revenue", "from_year": 2023, "to_year": 2024}).status_code, 409)

    def test_api_rejects_invalid_fact_and_missing_source(self):
        with TestClient(create_app(self.tmp.name, ModelConfig())) as client:
            base = {"document_id": "missing", "page": 1, "metric": "revenue", "year": 2024,
                    "value": "12.2", "unit": "billion", "currency": "VND", "confirmed": True,
                    "note": "Checked source document"}
            self.assertEqual(client.post("/api/facts", json=base).status_code, 404)
            for change in [{"value": "NaN"}, {"value": "1,200"}, {"confirmed": False}, {"metric": "invented"}]:
                self.assertEqual(client.post("/api/facts", json={**base, **change}).status_code, 422)
            self.assertEqual(client.get("/api/documents/unknown/pages/1").status_code, 404)

    def test_api_blocks_foreign_host_and_serves_web_assets(self):
        with TestClient(create_app(self.tmp.name, ModelConfig())) as client:
            self.assertEqual(client.get("/", headers={"Host": "malicious.example"}).status_code, 400)
            for route, marker in [("/", "FinLens"), ("/app.js", "renderAnswer"), ("/styles.css", ".sidebar")]:
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.text)
            self.assertEqual(client.get("/openapi.json").status_code, 200)


class ProviderTests(unittest.TestCase):
    def test_ollama_payload_and_embed_response(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "test-embed")
            self.assertFalse(payload["truncate"])
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = Ollama(ModelConfig(embed_model="test-embed"), client)
            self.assertEqual(provider.embed(["text"]), [[0.1, 0.2]])

    def test_bad_provider_vector_rejected(self):
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"embeddings": [[0, 0]]}))) as client:
            with self.assertRaises(ProviderError):
                Ollama(ModelConfig(embed_model="test"), client).embed(["text"])

    def test_unavailable_provider_rejected(self):
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))) as client:
            with self.assertRaises(ProviderError):
                Ollama(ModelConfig(embed_model="test"), client).embed(["text"])


if __name__ == "__main__":
    unittest.main()
