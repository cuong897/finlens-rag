from io import BytesIO
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pymupdf

from finlens.api import create_app
from finlens.ingest import IngestError
from finlens.providers import ModelConfig, ProviderError
from finlens.report_url import download_pdf, PinnedHTTPSConnection, public_target


class Response(BytesIO):
    def __init__(self, content=b"%PDF-fixture", status=200, headers=None):
        super().__init__(content)
        self.status = status
        self.headers = headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


class DownloadTests(unittest.TestCase):
    def test_credentials_private_addresses_and_other_protocols_are_rejected(self):
        invalid = ["http://example.com/a.pdf", "file:///a.pdf", "https://user:pass@example.com/a.pdf",
                   "https://example.com:8443/a.pdf", "https://example.com/\na.pdf"]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(IngestError):
                public_target(url)
        for address in ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1"]:
            with patch("finlens.report_url.socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]):
                with self.subTest(ip=address), self.assertRaises(IngestError):
                    public_target("https://example.com/a.pdf")

    def test_mixed_public_private_dns_answers_are_rejected(self):
        with patch("finlens.report_url.socket.getaddrinfo", return_value=PUBLIC + [(2, 1, 6, "", ("10.0.0.1", 443))]):
            with self.assertRaises(IngestError):
                public_target("https://example.com/a.pdf")

    def test_connect_pins_ip_but_tls_checks_original_hostname(self):
        raw, secured = MagicMock(), MagicMock()
        with patch("finlens.report_url.ssl.create_default_context") as context, patch("finlens.report_url.socket.create_connection", return_value=raw) as connect:
            context.return_value.wrap_socket.return_value = secured
            connection = PinnedHTTPSConnection("reports.example", "8.8.8.8", 12)
            connection.connect()
            connect.assert_called_once_with(("8.8.8.8", 443), timeout=12)
            context.return_value.wrap_socket.assert_called_once_with(raw, server_hostname="reports.example")
            self.assertIs(connection.sock, secured)

    def fake_download(self, responses, url="https://reports.example/report.pdf"):
        with patch("finlens.report_url.socket.getaddrinfo", return_value=PUBLIC), patch("finlens.report_url.PinnedHTTPSConnection") as factory:
            factory.return_value.getresponse.side_effect = responses
            return download_pdf(url)

    def test_pdf_download_follows_relative_redirect_and_retains_final_source(self):
        result = self.fake_download([Response(status=302, headers={"Location": "/final.pdf"}), Response()])
        self.assertEqual(result, (b"%PDF-fixture", "final.pdf", "https://reports.example/final.pdf", "reports.example"))

    def test_redirect_to_private_network_is_blocked_before_connect(self):
        with patch("finlens.report_url.socket.getaddrinfo", side_effect=[PUBLIC, [(2, 1, 6, "", ("127.0.0.1", 443))]]), patch("finlens.report_url.PinnedHTTPSConnection") as factory:
            factory.return_value.getresponse.return_value = Response(status=302, headers={"Location": "https://localhost/private.pdf"})
            with self.assertRaises(IngestError):
                download_pdf("https://reports.example/report.pdf")
            self.assertEqual(factory.call_count, 1)

    def test_html_truncated_oversized_and_compressed_responses_are_not_imported(self):
        for response in [Response(b"<html>not a PDF"), Response(headers={"Content-Length": "30000000"}),
                         Response(headers={"Content-Length": "500"}), Response(headers={"Content-Encoding": "gzip"}),
                         Response(status=403)]:
            with self.subTest(headers=response.headers, status=response.status), self.assertRaises(IngestError):
                self.fake_download([response])
        with patch("finlens.report_url.MAX_PDF_BYTES", 8), self.assertRaises(IngestError):
            self.fake_download([Response(b"%PDF-toolarge")])

    def test_redirect_loop_has_a_bound_and_connection_errors_are_sanitized(self):
        with self.assertRaises(IngestError):
            self.fake_download([Response(status=302, headers={"Location": "/again"}) for _ in range(4)])
        with patch("finlens.report_url.socket.getaddrinfo", return_value=PUBLIC), patch("finlens.report_url.PinnedHTTPSConnection", side_effect=OSError("private detail")):
            with self.assertRaises(IngestError) as caught:
                download_pdf("https://reports.example/report.pdf")
            self.assertNotIn("private detail", str(caught.exception))


class ImportAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.app = create_app(self.tmp.name, ModelConfig(embed_model="test"))
        self.client = TestClient(self.app)
        self.store = self.app.state.store
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 50), "Consolidated net revenue 2024: 125000 VND. Source for import tests.")
            self.pdf = pdf.tobytes()
        self.body = {"company": "Example", "year": 2024, "scope": "consolidated", "url": "https://reports.example/report.pdf"}

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_url_import_retains_source_deduplicates_and_never_confirms_facts(self):
        with patch("finlens.report_url.download_pdf", return_value=(self.pdf, "report.pdf", self.body["url"], "reports.example")):
            first = self.client.post("/api/import-url", json=self.body)
            second = self.client.post("/api/import-url", json=self.body)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(len(self.store.documents()), 1)
        self.assertEqual(self.store.facts(), [])
        self.assertEqual(first.json()["document"]["source_url"], self.body["url"])

    def test_invalid_metadata_does_not_download_and_failed_pdf_does_not_block_next_import(self):
        with patch("finlens.report_url.download_pdf") as download:
            self.assertEqual(self.client.post("/api/import-url", json={**self.body, "company": " "}).status_code, 422)
            download.assert_not_called()
        query = "?company=Example&year=2024&scope=consolidated&filename=report.pdf"
        self.assertEqual(self.client.post("/api/documents" + query, content=b"not PDF", headers={"Content-Type": "application/pdf"}).status_code, 422)
        self.assertEqual(self.client.post("/api/documents" + query, content=self.pdf, headers={"Content-Type": "application/pdf"}).status_code, 200)
        self.assertEqual(len(self.store.documents()), 1)

    def test_document_index_only_embeds_selected_document_and_reuses_cache(self):
        selected = self.client.post("/api/documents?company=Example&year=2024", content=self.pdf, headers={"Content-Type": "application/pdf"}).json()["document"]["id"]
        self.client.post("/api/documents?company=Other&year=2024", content=self.pdf, headers={"Content-Type": "application/pdf"})
        class Embedding:
            calls = 0
            def embed(self, texts):
                self.calls += 1
                return [[1, 0] for _ in texts]
        embedder = Embedding()
        self.app.state.engine.provider = embedder
        first = self.client.post(f"/api/documents/{selected}/index")
        second = self.client.post(f"/api/documents/{selected}/index")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["indexed"], 1)
        self.assertEqual(second.json()["indexed"], 0)
        self.assertEqual(embedder.calls, 1)
        saved = self.store.embeddings("test")
        self.assertEqual(set(saved), {c["id"] for c in self.store.chunks() if c["document_id"] == selected})
        self.assertEqual(self.client.post("/api/documents/missing/index").status_code, 404)

    def test_embedding_outage_keeps_imported_pdf_available_for_keyword_search(self):
        document = self.client.post("/api/documents?company=Example&year=2024", content=self.pdf,
                                    headers={"Content-Type": "application/pdf"}).json()["document"]
        with patch.object(self.app.state.engine.provider, "embed", side_effect=ProviderError("Embedding unavailable")):
            response = self.client.post(f"/api/documents/{document['id']}/index")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get(f"/api/documents/{document['id']}/pdf").content, self.pdf)
        self.assertTrue(self.store.chunks("Example", [2024], "consolidated", "real"))
        self.assertEqual(self.store.facts(), [])
