from pathlib import Path
import tempfile
import unittest

from finlens.engine import Engine
from finlens.ingest import seed_demo
from finlens.models import AskRequest
from finlens.providers import ModelConfig
from finlens.retrieval import contextual_fusion, financial_context_rank, financial_query, net_sales_intent
from finlens.store import Store


class ContextRankingTests(unittest.TestCase):
    def test_statement_with_actual_row_precedes_title_only_and_unrelated_notes(self):
        hits = [{"id": 1, "document_id": "a", "page": 1, "score": 1, "text": "Statement heading without the requested row."},
                {"id": 2, "document_id": "a", "page": 2, "score": .8, "text": "Profit after tax in earnings-per-share notes."},
                {"id": 3, "document_id": "a", "page": 3, "score": .1, "text": "Net profit after tax 99,252,768,162 VND."}]
        pages = {("a", 1): "CONSOLIDATED INCOME STATEMENT", ("a", 2): "NOTES TO THE FINANCIAL STATEMENTS",
                 ("a", 3): "CONSOLIDATED INCOME STATEMENT"}
        ranked = financial_context_rank("Lợi nhuận sau thuế toàn tập đoàn", hits, pages)
        self.assertEqual([h["id"] for h in ranked], [3, 1, 2])
        self.assertEqual(ranked[0]["score"], .1)
        self.assertIn("99,252,768,162", ranked[0]["text"])
        self.assertNotIn("context_rank", hits[0])

    def test_causal_and_unknown_questions_keep_base_ranking_without_page_reads(self):
        hit = {"id": 1, "document_id": "a", "page": 1, "score": 1, "text": "Revenue grew because transport costs fell."}
        def no_read(*args):
            self.fail("Causal questions must keep note ranking without table promotion")
        for question in ["Vì sao doanh thu tăng?", "Giải thích chi phí lãi vay", "What drives revenue?", "CEO nhận lương bao nhiêu?"]:
            self.assertEqual(contextual_fusion(question, [hit], [], no_read)[0]["id"], 1)

    def test_context_cannot_add_another_companys_source_or_exceed_limit(self):
        hit = {"id": 1, "document_id": "a", "page": 1, "score": 1, "text": "Total assets 120."}
        pages = {("a", 1): "Notes", ("b", 2): "CONSOLIDATED BALANCE SHEET Total assets 999."}
        ranked = financial_context_rank("Tổng tài sản", [hit], pages, 1)
        self.assertEqual([(h["document_id"], h["page"]) for h in ranked], [("a", 1)])

    def test_sales_after_deductions_is_distinguished_from_gross_or_ambiguous_sales(self):
        for question in ["Doanh số thuần năm 2024", "Quy mô doanh số bán hàng sau khi trừ các khoản giảm trừ", "Net sales in 2024"]:
            self.assertTrue(net_sales_intent(question))
            self.assertIn("net revenue", financial_query(question))
        for question in ["Doanh số trước giảm trừ", "So sánh doanh số và các khoản giảm trừ", "Gross and net sales"]:
            self.assertFalse(net_sales_intent(question))


class ContextEngineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.store = Store(self.tmp.name)

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.root.resolve())
        self.tmp.cleanup()

    def test_net_sales_lookup_uses_verified_arithmetic_branch_without_cloud_call(self):
        seed_demo(self.store)
        class NoGeneration:
            def generate(self, *args):
                raise AssertionError("Known net sales should use confirmed facts")
        engine = Engine(self.store, ModelConfig(chat_model="fake"), NoGeneration())
        result = engine.answer(AskRequest(question="Quy mô doanh số bán hàng sau khi đã trừ các khoản giảm trừ năm 2024",
                                         company="An Phát Demo", years=[2024], dataset="demo"))
        self.assertEqual(result["mode"], "calculated")
        self.assertIn("1250", result["answer"])
        self.assertFalse(result["generation_attempted"])

    def test_engine_uses_same_context_fusion_as_evaluation_and_retains_row_evidence(self):
        for doc, company in [("a", "A"), ("b", "B")]:
            self.store.add_document({"id": doc, "company": company, "report_year": 2024, "scope": "consolidated",
                                     "filename": doc + ".pdf", "sha256": doc, "is_demo": False, "pdf_path": None},
                                    ["NOTES: Profit after tax, profit after tax in the notes.",
                                     "CONSOLIDATED INCOME STATEMENT. Net profit after tax 99,252,768,162 VND."])
        class Embedding:
            def embed(self, texts):
                return [[1, 0] for _ in texts]
        engine = Engine(self.store, ModelConfig(embed_model="test"), Embedding())
        engine.index_embeddings()
        hits, mode, _ = engine.retrieve(AskRequest(question="Lợi nhuận sau thuế", company="A", years=[2024]))
        self.assertEqual(mode, "hybrid_rrf")
        self.assertEqual(hits[0]["page"], 2)
        self.assertEqual(hits[0]["context_rank"], 2)
        self.assertTrue(all(h["company"] == "A" for h in hits))
