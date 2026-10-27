import tempfile
import unittest
from pathlib import Path

import pymupdf

from finlens.ingest import IngestError, ingest_pdf, seed_demo
from finlens.retrieval import bm25
from finlens.store import Store
from finlens.text import chunk_page


class DataTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.store = Store(self.tmp.name)

    def tearDown(self):
        assert Path(self.tmp.name).resolve().is_relative_to(self.test_root.resolve())
        self.tmp.cleanup()

    def test_seed_idempotent_and_metadata_isolation(self):
        self.assertEqual(seed_demo(self.store)["created"], 6)
        self.assertEqual(seed_demo(self.store)["created"], 0)
        self.assertEqual(len(self.store.facts()), 30)
        chunks = self.store.chunks(company="An Phát Demo", years=[2024], scope="consolidated", dataset="demo")
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(c["company"] == "An Phát Demo" and c["report_year"] == 2024 for c in chunks))
        self.assertEqual(self.store.chunks(dataset="real"), [])

    def test_vietnamese_search_retains_page_citation(self):
        seed_demo(self.store)
        hits = bm25("nguyen nhan chi phi lai vay tang", self.store.chunks("An Phát Demo", [2024]))
        self.assertEqual(hits[0]["page"], 2)
        self.assertEqual(hits[0]["document_id"], "demo-ANPHAT-2024")
        self.assertEqual(bm25("xyznonexistent", self.store.chunks()), [])

    def test_invalid_and_scan_pdf_rejected_without_artifacts(self):
        with self.assertRaises(IngestError):
            ingest_pdf(self.store, b"not a pdf", "bad.pdf", "Test", 2024, "consolidated")
        pdf = pymupdf.open()
        pdf.new_page()
        with self.assertRaises(IngestError):
            ingest_pdf(self.store, pdf.tobytes(), "scan.pdf", "Test", 2024, "consolidated")
        pdf.close()
        self.assertEqual(list((self.store.root / "uploads").iterdir()), [])

    def test_real_pdf_duplicate_and_filename_safety(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 50), "Revenue in 2024 is 1200 billion VND. Financial report.")
            content = pdf.tobytes()
        first = ingest_pdf(self.store, content, "../../unsafe.pdf", "Test", 2024, "consolidated")
        second = ingest_pdf(self.store, content, "again.pdf", "Test", 2024, "consolidated")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        document = first["document"]
        self.assertEqual(document["filename"], "unsafe.pdf")
        self.assertTrue(self.store.pdf_path(document["id"]).exists())
        self.assertEqual(len(list((self.store.root / "uploads").iterdir())), 1)
        self.assertIn("Revenue", self.store.page(document["id"], 1)["text"])

    def test_chunks_bounded_without_losing_end(self):
        chunks = chunk_page(("Một dòng văn bản có nội dung.\n" * 300) + "KẾT THÚC")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1600 for chunk in chunks))
        self.assertTrue(chunks[-1].endswith("KẾT THÚC"))


if __name__ == "__main__":
    unittest.main()
