from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from finlens.pdf_text import read_page
from finlens.store import Store
from finlens.text import chunk_page


class LayoutTextTests(unittest.TestCase):
    def test_facing_financial_pages_are_separated_without_cross_column_rows(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=1200, height=800)
            for x, title, label, value in [(40, "CONSOLIDATED BALANCE SHEET", "Total assets", "123456"),
                                            (650, "CONSOLIDATED INCOME STATEMENT", "Net revenue", "987654")]:
                page.insert_text((x, 70), title)
                page.insert_text((x, 140), f"{label}     {value}")
            text, _ = read_page(page)
        self.assertIn("[Vùng trang 1]", text)
        self.assertLess(text.index("123456"), text.index("[Vùng trang 2]"))
        self.assertGreater(text.index("987654"), text.index("[Vùng trang 2]"))

    def test_single_wide_table_is_not_split_and_numeric_columns_survive(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=1200, height=800)
            page.insert_text((40, 70), "CONSOLIDATED BALANCE SHEET")
            page.insert_text((40, 150), "Total assets")
            page.insert_text((1000, 150), "123456789")
            text, _ = read_page(page)
        self.assertNotIn("[Vùng trang", text)
        self.assertIn("Total assets", text)
        self.assertIn("123456789", text)

    def test_chunks_compact_padding_and_repeat_header_without_losing_final_rows(self):
        text = "CONSOLIDATED INCOME STATEMENT\n" + "\n".join(f"Row {i}" + " " * 250 + f"{i},123,456" for i in range(100))
        chunks = chunk_page(text)
        self.assertTrue(all(len(c) <= 1600 for c in chunks))
        self.assertTrue(all("CONSOLIDATED INCOME STATEMENT" in c for c in chunks))
        self.assertIn("99,123,456", chunks[-1])
        self.assertTrue(all(" " * 10 not in c for c in chunks))

    def test_region_boundaries_never_merge_two_facing_statements(self):
        chunks = chunk_page("[Vùng trang 1]\nLeft statement 123\n[Vùng trang 2]\nRight statement 456")
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(not ("Left" in c and "Right" in c) for c in chunks))


class RefreshIndexTests(unittest.TestCase):
    def test_refresh_preserves_facts_pages_and_original_and_invalidates_only_derived_vectors(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            store = Store(tmp)
            for doc in ["a", "b"]:
                store.add_document({"id": doc, "company": doc, "report_year": 2024, "scope": "consolidated", "filename": "original.pdf",
                                    "sha256": doc, "is_demo": False, "pdf_path": None}, ["Old source 123456"])
            store.add_fact({"document_id": "a", "page": 1, "metric": "revenue", "year": 2024, "value": "123456", "unit": "one", "currency": "VND", "verified": True, "note": "Reviewed original"})
            store.save_embeddings("test", [(c["id"], [1, 0]) for c in store.chunks()])
            facts = store.facts()
            store.replace_derived_text("a", ["New layout 123456"], [], "v2")
            self.assertEqual(store.facts(), facts)
            self.assertEqual(store.processing_version("a"), "v2")
            self.assertEqual(len(store.embeddings("test")), 1)
            self.assertEqual(store.page("a", 1)["text"], "New layout 123456")
            with patch("finlens.store.chunk_page", side_effect=RuntimeError("chunk failure")), self.assertRaises(RuntimeError):
                store.replace_derived_text("a", ["Bad refresh"], [], "bad")
            self.assertEqual(store.page("a", 1)["text"], "New layout 123456")
            with self.assertRaises(ValueError):
                store.replace_derived_text("a", ["wrong", "page count"], [], "bad")
