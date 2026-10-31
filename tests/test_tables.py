from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from openpyxl import Workbook

from finlens.api import create_app
from finlens.ingest import IngestError
from finlens.models import AskRequest, TableOptions
from finlens.providers import ModelConfig
from finlens.store import Store
from finlens.tables import ingest_table, preview_table, table_amount, table_suggestions


def xlsx_fixture():
    workbook = Workbook()
    ws = workbook.active
    ws.title = "KQKD"
    ws.append(["Chỉ tiêu", 2023, 2024])
    ws.append(["Doanh thu thuần", 1000, 1250])
    ws.append(["Lợi nhuận sau thuế", -10.1234, "=SUM(B2:C2)"])
    other = workbook.create_sheet("CĐKT")
    other.append(["Chỉ tiêu", 2023, 2024])
    other.append(["Tổng tài sản", 5000, 6000])
    workbook.create_sheet("Ẩn").sheet_state = "hidden"
    output = BytesIO(); workbook.save(output); workbook.close()
    return output.getvalue()


CSV = "Chỉ tiêu;2023;2024\nDoanh thu thuần;1.000,25;1.250,50\nLợi nhuận sau thuế;(12,50);0\n".encode("utf-8-sig")
OPTIONS = {"sheet": "KQKD", "year_columns": {"B": 2023, "C": 2024}, "unit": "million"}


class TableReaderTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.store = Store(self.tmp.name)
        self.xlsx = xlsx_fixture()

    def tearDown(self):
        self.tmp.cleanup()

    def test_excel_preview_preserves_cell_addresses_and_formula_text_without_evaluating(self):
        result = preview_table(self.xlsx, "report.xlsx")
        self.assertEqual(result["sheets"], ["KQKD", "CĐKT"])
        self.assertEqual(result["rows"][2]["cells"][2], {"address": "C3", "value": "=SUM(B2:C2)", "numeric": False, "formula": True})
        self.assertEqual(result["rows"][1]["cells"][1]["address"], "B2")
        self.assertEqual(self.store.documents(), [])

    def test_csv_bom_separator_locale_negatives_zero_and_high_precision(self):
        document = ingest_table(self.store, CSV, "report.csv", "A", 2024, "consolidated", TableOptions(**{**OPTIONS, "sheet": "CSV"}))["document"]
        self.assertEqual(document["file_type"], "csv")
        row = self.store.table_row(document["id"], 1)
        drafts = table_suggestions(row, document["id"], 1)["candidates"]
        self.assertEqual([c["value"] for c in drafts], ["1000.25", "1250.50"])
        self.assertTrue(all(not c["verified"] for c in drafts))
        drafts = table_suggestions(self.store.table_row(document["id"], 2), document["id"], 2)["candidates"]
        self.assertEqual([c["value"] for c in drafts], ["-12.50", "0"])
        self.assertEqual(table_amount("900719925474099312345", "en"), "900719925474099312345")
        self.assertEqual(table_amount("1,234", "vi"), "1.234")
        self.assertEqual(table_amount("1,234", "en"), "1234")
        for value in ["-", "=SUM(A1:A2)", "1.23.456", "(-10)"]:
            self.assertIsNone(table_amount(value, "vi"))

    def test_distinct_sheet_views_deduplicate_by_file_and_mapping_not_filename(self):
        args = (self.store, self.xlsx, "report.xlsx", "A", 2024, "consolidated")
        first = ingest_table(*args, TableOptions(**OPTIONS))
        duplicate = ingest_table(*args, TableOptions(**OPTIONS))
        other = ingest_table(*args, TableOptions(**{**OPTIONS, "sheet": "CĐKT"}))
        self.assertTrue(first["created"] and other["created"])
        self.assertFalse(duplicate["created"])
        self.assertNotEqual(first["document"]["id"], other["document"]["id"])
        self.assertFalse(first["document"]["has_pdf"])
        self.assertTrue(first["document"]["has_file"])
        self.assertIsNone(self.store.pdf_path(first["document"]["id"]))
        self.assertEqual(self.store.source_path(first["document"]["id"]).read_bytes(), self.xlsx)

    def test_formula_is_skipped_but_exact_native_decimal_and_provenance_survive(self):
        document = ingest_table(self.store, self.xlsx, "report.xlsx", "A", 2024, "consolidated", TableOptions(**OPTIONS))["document"]
        row = self.store.table_row(document["id"], 2)
        drafts = table_suggestions(row, document["id"], 2)["candidates"]
        self.assertEqual([(c["year"], c["value"]) for c in drafts], [(2023, "-10.1234")])
        self.assertEqual(drafts[0]["source_cell"], "B3")
        self.assertEqual(self.store.page(document["id"], 2)["table"]["source_label"], "KQKD · A3:C3")
        self.assertEqual(self.store.facts(), [])

    def test_invalid_mapping_hidden_sheet_encoding_and_oversized_sheet_are_rejected(self):
        for change in [{"label_column": "B"}, {"year_columns": {"Z": 2024}}, {"sheet": "Ẩn"}, {"header_row": 99}]:
            with self.subTest(change=change), self.assertRaises(IngestError):
                ingest_table(self.store, self.xlsx, "report.xlsx", "A", 2024, "consolidated", TableOptions(**{**OPTIONS, **change}))
        with self.assertRaises(IngestError):
            preview_table(b"\xff\xfe\x00bad", "report.csv")
        with patch("finlens.tables.MAX_ROWS", 2), self.assertRaises(IngestError):
            preview_table(self.xlsx, "report.xlsx")
        self.assertEqual(self.store.documents(), [])

    def test_import_failure_removes_its_file_and_leaves_no_partial_rows(self):
        with patch.object(self.store, "add_document", side_effect=RuntimeError("DB failure")), self.assertRaises(RuntimeError):
            ingest_table(self.store, self.xlsx, "report.xlsx", "A", 2024, "consolidated", TableOptions(**OPTIONS))
        self.assertEqual(list((self.store.root / "uploads").iterdir()), [])
        self.assertEqual(self.store.documents(), [])


class TableAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.app = create_app(self.tmp.name, ModelConfig())
        self.client = TestClient(self.app)
        self.content = xlsx_fixture()

    def tearDown(self):
        self.client.close(); self.tmp.cleanup()

    def upload(self):
        query = urlencode({"filename": "report.xlsx", "company": "A", "year": 2024, "scope": "consolidated", "options": json.dumps(OPTIONS)})
        response = self.client.post("/api/tables?" + query, content=self.content)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["document"]["id"]

    def test_preview_import_confirm_compare_and_citations_trace_sheet_and_cells(self):
        preview = self.client.post("/api/tables/preview?filename=report.xlsx", content=self.content)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self.app.state.store.documents(), [])
        identifier = self.upload()
        suggestions = self.client.get(f"/api/documents/{identifier}/suggestions?page=1").json()["candidates"]
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(self.app.state.store.facts(), [])
        for c in suggestions:
            body = {k: c[k] for k in ("document_id", "page", "metric", "year", "value", "unit", "currency")}
            body["note"] = f"Reviewed KQKD source cell {c['source_cell']} and original workbook."
            self.assertEqual(self.client.post("/api/facts", json=body).status_code, 422)
            self.assertEqual(self.client.post("/api/facts", json={**body, "confirmed": True}).status_code, 201)
        result = self.client.post("/api/ask", json={"company": "A", "years": [2023, 2024], "question": "So sánh doanh thu thuần", "dataset": "real"}).json()
        self.assertEqual(result["mode"], "calculated")
        self.assertEqual(result["comparisons"][0]["growth_percent"], "25.00")
        self.assertTrue(all(c["source_label"] == "KQKD · A2:C2" for c in result["citations"]))
        self.assertEqual(self.client.get(f"/api/documents/{identifier}/pdf").status_code, 404)
        self.assertEqual(self.client.get(f"/api/documents/{identifier}/file").content, self.content)
        hits, _, _ = self.app.state.engine.retrieve(AskRequest(company="A", years=[2024], question="Doanh thu thuần", dataset="real"))
        self.assertEqual(hits[0]["source_label"], "KQKD · A2:C2")

    def test_table_parser_errors_never_create_documents(self):
        response = self.client.post("/api/tables/preview?filename=report.xlsx", content=b"not an Excel")
        self.assertEqual(response.status_code, 422)
        query = urlencode({"filename": "report.xlsx", "company": "A", "year": 2024, "options": "{}"})
        self.assertEqual(self.client.post("/api/tables?" + query, content=self.content).status_code, 422)
        self.assertEqual(self.app.state.store.documents(), [])
