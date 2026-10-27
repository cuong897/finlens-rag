from pathlib import Path
import tempfile
import unittest

from finlens.engine import Engine
from finlens.finance import debt_capacity
from finlens.ingest import seed_demo
from finlens.models import AskRequest
from finlens.providers import ModelConfig
from finlens.store import Store


class DebtCapacityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.store = Store(self.tmp.name)
        seed_demo(self.store)
        for year, assets in [(2023, "200"), (2024, "300")]:
            self.add("current_assets", year, assets, "billion")
            self.add("current_liabilities", year, "100000", "million")

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.root.resolve())
        self.tmp.cleanup()

    def add(self, metric, year, value, unit="billion", currency="VND"):
        self.store.add_fact({"document_id": f"demo-ANPHAT-{year}", "page": 1, "metric": metric,
                             "year": year, "value": value, "unit": unit, "currency": currency,
                             "verified": True, "note": "confirmed test input"})

    def analysis(self, **kwargs):
        return debt_capacity(self.store, "An Phát Demo", [2023, 2024], dataset="demo", **kwargs)

    def test_ratio_converts_units_and_cites_every_component(self):
        result = self.analysis()
        current = result["ratios"][0]
        self.assertEqual([v["value"] for v in current["values"]], ["2.00", "3.00"])
        self.assertEqual(len({c["id"] for c in result["citations"]}), len(result["citations"]))
        sources = {c["id"] for c in result["citations"]}
        self.assertTrue(all(set(v["sources"]) <= sources for r in result["ratios"] for v in r["values"]))

    def test_question_from_screenshot_works_without_any_ai_calls(self):
        class NoCalls:
            def generate(self, *args):
                raise AssertionError("No model needed for arithmetic")
            embed = generate
        engine = Engine(self.store, ModelConfig(chat_model="gemini-3.8-flash", chat_provider="gemini"), NoCalls())
        answer = engine.answer(AskRequest(question="So sánh khả năng trả nợ năm 2023 và 2024", company="An Phát Demo", dataset="demo"))
        self.assertEqual(answer["mode"], "calculated")
        self.assertFalse(answer["generation_attempted"])
        self.assertTrue(answer["ratios"])
        self.assertIn("DSCR", " ".join(answer["warnings"]))

    def test_missing_inputs_are_actionable_and_incomplete_year_not_invented(self):
        with self.store.connection() as db:
            db.execute("DELETE FROM facts WHERE metric='current_assets' AND year=2024")
        result = self.analysis()
        self.assertNotIn("current_ratio", [r["metric"] for r in result["ratios"]])
        self.assertTrue(any("2024" in message and "Tài sản ngắn hạn" in message for message in result["missing_inputs"]))

    def test_conflicting_values_and_zero_denominator_refuse_affected_ratio(self):
        self.add("current_assets", 2024, "999")
        result = self.analysis()
        self.assertNotIn("current_ratio", [r["metric"] for r in result["ratios"]])
        self.assertTrue(any("mâu thuẫn" in message for message in result["missing_inputs"]))
        with self.store.connection() as db:
            db.execute("UPDATE facts SET value='0' WHERE metric='current_liabilities'")
        result = self.analysis()
        self.assertNotIn("operating_cash_coverage", [r["metric"] for r in result["ratios"]])
        self.assertTrue(any("mẫu số" in message for message in result["missing_inputs"]))

    def test_currency_scope_and_real_corpus_isolation(self):
        self.add("current_liabilities", 2024, "100000", "million", "USD")
        result = self.analysis()
        self.assertNotIn("current_ratio", [r["metric"] for r in result["ratios"]])
        self.assertEqual(self.analysis(scope="standalone")["ratios"], [])
        self.assertEqual(debt_capacity(self.store, "An Phát Demo", [2023, 2024], dataset="real")["ratios"], [])
