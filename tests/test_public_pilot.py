import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import pymupdf

from finlens.ingest import IngestError, ingest_pdf
from finlens.public_pilot import download_report, verify_fact_anchor
from finlens.store import Store


class PublicPilotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.path = Path(self.tmp.name)

    def tearDown(self):
        assert self.path.resolve().is_relative_to(self.root.resolve())
        self.tmp.cleanup()

    def test_download_is_pinned_and_cached(self):
        content = b"%PDF-pilot-test"
        report = {"filename": "public.pdf", "url": "https://fpt.com/api/media/public.pdf",
                  "sha256": hashlib.sha256(content).hexdigest()}
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=content)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(download_report(report, self.path, client), content)
            self.assertEqual(download_report(report, self.path, client), content)
        self.assertEqual(len(calls), 1)

    def test_changed_source_is_not_imported(self):
        report = {"filename": "public.pdf", "url": "https://fpt.com/api/media/public.pdf", "sha256": "0" * 64}
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"changed source"))) as client:
            with self.assertRaises(IngestError):
                download_report(report, self.path, client)
        self.assertFalse((self.path / "public.pdf").exists())
        self.assertFalse((self.path / "public.pdf.part").exists())

    def test_source_metadata_and_duplicate_do_not_reparse_pdf(self):
        store = Store(self.path / "db")
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 50), "Net revenue 2024: 1,234 VND. Consolidated financial report.")
            content = pdf.tobytes()
        first = ingest_pdf(store, content, "report.pdf", "Pilot", 2024, "consolidated")
        document_id = first["document"]["id"]
        store.record_source(document_id, "https://fpt.com/report.pdf", "Issuer")
        with patch("finlens.ingest.pymupdf.open", side_effect=AssertionError("Duplicate PDF must not be parsed again")):
            duplicate = ingest_pdf(store, content, "again.pdf", "Pilot", 2024, "consolidated")
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["document"]["source_url"], "https://fpt.com/report.pdf")
        self.assertEqual(store.documents()[0]["source_publisher"], "Issuer")

    def test_fact_anchor_requires_amount_label_and_currency(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 50), "Net revenue 2024: 1,234 VND.")
            fact = {"page": 1, "value": "1234", "label": "Net revenue"}
            verify_fact_anchor(pdf, fact)
            with self.assertRaises(IngestError):
                verify_fact_anchor(pdf, {**fact, "value": "9999"})
