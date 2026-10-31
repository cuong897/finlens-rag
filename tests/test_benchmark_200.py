"""Data integrity and separation checks for the additional 200-case benchmark."""
from collections import Counter
import json
from pathlib import Path
import unittest

from finlens.benchmark import reference_integer
from finlens.retrieval_dataset import load_dataset
from scripts.build_benchmark_200 import build_dataset

FIXTURES = Path(__file__).resolve().parents[1] / "finlens/fixtures"


class ReferenceIntegerTests(unittest.TestCase):
    def test_both_printed_thousands_conventions_and_parentheses_keep_sign(self):
        for raw, expected in [("3.964.316.764.607", "3964316764607"),
                              ("(2.870.318.244)", "-2870318244"),
                              ("(248,318,241,716)", "-248318241716"), ("0", "0"), ("-123", "-123")]:
            with self.subTest(raw=raw):
                self.assertEqual(reference_integer(raw), expected)

    def test_malformed_groups_decimals_and_dash_are_not_guessed(self):
        for raw in ("1.234,567", "1234,567", "12.34", "1,234.56", "-", "(1,234", "(-1,234)", "NaN", "12 345"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                reference_integer(raw)


class Benchmark200IntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(FIXTURES / "benchmark_holdout_v3_200.json")

    def test_dataset_is_reproducible_from_source_row_ledger(self):
        self.assertEqual(self.dataset, build_dataset())

    def test_200_questions_are_balanced_and_have_distinct_requirements(self):
        cases = self.dataset["cases"]
        self.assertEqual(len(cases), 200)
        self.assertEqual(len({c["question"].casefold() for c in cases}), 200)
        self.assertEqual(set(Counter(c["company"] for c in cases).values()), {50})
        self.assertEqual(Counter(c["category"] for c in cases),
                         {"statement_lookup": 88, "note_lookup": 32, "prior_year_lookup": 32,
                          "multi_value": 32, "year_comparison": 16})
        self.assertEqual(len({(c["company"], t) for c in cases for t in c["target_ids"]}), 156)
        self.assertEqual(len({(c["company"], p) for c in cases for p in c["gold_pages"]}), 34)
        self.assertTrue(all(len(c["evidence"]) >= 2 for c in cases if c["category"] in {"multi_value", "year_comparison"}))

    def test_new_sources_and_companies_do_not_overlap_any_previous_evaluation(self):
        old = []
        for name in ("public_pilot.json", "benchmark_reports.json", "benchmark_holdout_reports_v2.json"):
            old.extend(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
        reports = self.dataset["reports"]
        self.assertEqual(sum(r["pages"] for r in reports), 427)
        self.assertFalse({r["company"] for r in reports} & {r["company"] for r in old})
        self.assertFalse({r["sha256"] for r in reports} & {r["sha256"] for r in old})
        self.assertTrue(all(not r.get("allowed_pages") and not r["facts"] for r in reports))

    def test_transcribed_columns_balance_and_retain_original_signs(self):
        ledger = json.loads((FIXTURES / "benchmark_targets_v3.json").read_text(encoding="utf-8"))["targets"]
        for company, rows in ledger.items():
            rows = {r["id"]: r for r in rows}
            for year in ("2024", "2023"):
                with self.subTest(company=company, year=year):
                    value = lambda key: int(reference_integer(rows[key]["values"][year]))
                    self.assertEqual(value("total_assets"), value("liabilities") + value("equity"))
                    self.assertEqual(value("total_assets"), value("current_assets") + value("noncurrent_assets"))
                    self.assertEqual(value("gross_profit"), value("revenue") - abs(value("cogs")))
        self.assertLess(int(reference_integer(ledger["GMD"][18]["values"]["2023"])), 0)
