from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pymupdf

from finlens.api import create_app
from finlens.ingest import ingest_pdf, seed_demo
from finlens.providers import ModelConfig
from finlens.suggestions import extract_page, identify_metric, parse_amount


def table_pdf(*, currency="VND", scale="", values=("8,326,486", "5,516,393"),
              years=(2023, 2024), duplicate=False, column_offset=0, extra_currency=False):
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    def right(text, end, y):
        page.insert_text((end - pymupdf.get_text_length(text, fontsize=11), y), text, fontsize=11)
    page.insert_text((40, 40), "CONSOLIDATED INCOME STATEMENT")
    if scale:
        page.insert_text((40, 65), f"Unit: {scale} {currency}")
    for year, end in zip(years, [430, 560]):
        right(str(year), end, 100)
        if currency:
            right(currency, end, 120)
    if extra_currency:
        page.insert_text((300, 120), "USD")
    page.insert_text((40, 160), "23 Including: Interest expense")
    for value, end in zip(values, [430, 560]):
        right(value, end + column_offset, 160)
    if duplicate:
        page.insert_text((40, 195), "23 Including: Interest expense")
        for value, end in zip(["9,999,999", "6,666,666"], [430, 560]):
            right(value, end, 195)
    return pdf


class SuggestionParserTests(unittest.TestCase):
    def test_ocr_geometry_creates_only_unverified_drafts_with_source_warning(self):
        with table_pdf() as pdf:
            page=pdf[0];textpage=page.get_textpage()
            with patch('finlens.suggestions.needs_ocr',return_value=True), patch('finlens.suggestions.textpage_ocr',return_value=(textpage,'OCR requires source review')):
                result=extract_page(page,'doc',1,ocr_dir='local-test-models')
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['method']=='aligned_ocr_columns' and not c['verified'] and c['bbox'] for c in result['candidates']))
        self.assertIn('OCR requires source review',result['warnings'])
    def test_years_come_from_headers_not_document_year_or_column_order(self):
        with table_pdf(years=(2024, 2023)) as pdf:
            candidates = extract_page(pdf[0], "doc", 1)["candidates"]
        self.assertEqual([(c["year"], c["value"]) for c in candidates], [(2024, "8326486"), (2023, "5516393")])
        self.assertTrue(all(not c["verified"] for c in candidates))
        self.assertTrue(all(c["source_label"] and c["bbox"] and c["raw_value"] for c in candidates))

    def test_explicit_scale_and_currency_preserved(self):
        with table_pdf(scale="million", currency="USD") as pdf:
            candidates = extract_page(pdf[0], "doc", 1)["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(c["unit"] == "million" and c["currency"] == "USD" for c in candidates))

    def test_company_word_ty_does_not_multiply_values_by_a_billion(self):
        with table_pdf() as pdf:
            pdf[0].insert_text((40, 65), "CONG TY CO PHAN XYZ")
            candidates = extract_page(pdf[0], "doc", 1)["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(c["unit"] == "one" for c in candidates))

    def test_vietnamese_unit_declaration_and_unknown_multiplier(self):
        with table_pdf() as pdf:
            pdf[0].insert_text((40, 65), "Don vi tinh: trieu dong")
            candidates = extract_page(pdf[0], "doc", 1)["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(c["unit"] == "million" for c in candidates))
        with table_pdf() as pdf:
            pdf[0].insert_text((40, 65), "Unit: 1000 VND")
            self.assertEqual(extract_page(pdf[0], "doc", 1)["candidates"], [])
        for unit in ["kVND", "VND mn"]:
            with table_pdf() as pdf:
                pdf[0].insert_text((40, 65), f"Unit: {unit}")
                self.assertEqual(extract_page(pdf[0], "doc", 1)["candidates"], [])

    def test_missing_or_ambiguous_currency_refuses_drafts(self):
        for options in [{"currency": ""}, {"extra_currency": True}]:
            with table_pdf(**options) as pdf:
                result = extract_page(pdf[0], "doc", 1)
            self.assertEqual(result["candidates"], [])
            self.assertTrue(result["warnings"])

    def test_conflicting_same_label_rows_are_not_arbitrarily_selected(self):
        with table_pdf(duplicate=True) as pdf:
            result = extract_page(pdf[0], "doc", 1)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("nhiều dòng" in w for w in result["warnings"]))

    def test_dash_is_missing_not_zero_and_parentheses_are_negative(self):
        with table_pdf(values=("-", "(5,516,393)")) as pdf:
            candidates = extract_page(pdf[0], "doc", 1)["candidates"]
        self.assertEqual([(c["year"], c["value"]) for c in candidates], [(2024, "-5516393")])
        self.assertEqual(parse_amount("0"), "0")
        self.assertIsNone(parse_amount("—"))

    def test_ambiguous_single_separator_and_malformed_numbers_refuse(self):
        for raw in ["1,234", "1.234", "12,34,567", "NaN", "1e10", "(−12)", "1.234,5.6"]:
            self.assertIsNone(parse_amount(raw))
        self.assertEqual(parse_amount("1,234", ","), "1234")
        self.assertEqual(parse_amount("1.234.567"), "1234567")
        self.assertEqual(parse_amount("1.234.567,89", "."), "1234567.89")

    def test_unaligned_figures_are_not_attached_to_year_columns(self):
        with table_pdf(column_offset=-45) as pdf:
            self.assertEqual(extract_page(pdf[0], "doc", 1)["candidates"], [])

    def test_blank_or_rotated_page_does_not_guess(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            self.assertEqual(extract_page(pdf[0], "doc", 1)["candidates"], [])
        with table_pdf() as pdf:
            pdf[0].set_rotation(90)
            self.assertEqual(extract_page(pdf[0], "doc", 1)["candidates"], [])

    def test_total_labels_do_not_capture_provisions_or_parent_only_income(self):
        for label in ["Provision for short-term liabilities", "Profit after tax attributable to owners", "Long-term liabilities"]:
            self.assertIsNone(identify_metric(label))
        self.assertEqual(identify_metric("10 Net revenue from sales of goods and rendering of services (10 = 01 - 02)"), "revenue")
        self.assertEqual(identify_metric("Chi phí lãi vay"), "interest_expense")

    def test_four_digit_amount_is_not_mistaken_for_another_year_header(self):
        with table_pdf(values=("2023", "2024")) as pdf:
            page = pdf[0]
            page.insert_text((40, 210), "23 Including: Interest expense")
            for value, end in zip(["2023", "2024"], [430, 560]):
                page.insert_text((end - pymupdf.get_text_length(value, fontsize=11), 210), value, fontsize=11)
            result = extract_page(page, "doc", 1)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual({c["year"] for c in result["candidates"]}, {2023, 2024})


class SuggestionAPITests(unittest.TestCase):
    def batch_payload(self):
        candidates = self.client.get(f"/api/documents/{self.document}/suggestions?page=1").json()["candidates"]
        return {"facts": [{**{key: c[key] for key in ["document_id", "page", "metric", "year", "value", "unit", "currency"]},
                          "confirmed": True, "note": "Human reviewed every selected row, unit and year column."} for c in candidates]}

    def test_batch_review_is_explicit_idempotent_and_enables_comparison(self):
        payload = self.batch_payload()
        self.assertEqual(self.client.get('/api/facts').json(), [])
        result = self.client.post('/api/facts/batch', json=payload)
        self.assertEqual(result.status_code, 201)
        self.assertEqual((result.json()['created'], result.json()['confirmed']), (2, 2))
        again = self.client.post('/api/facts/batch', json=payload)
        self.assertEqual((again.json()['created'], again.json()['confirmed']), (0, 2))
        document = self.app.state.store.get_document(self.document)
        comparison = self.client.post('/api/compare', json={'company': document['company'], 'metric': 'interest_expense',
                    'from_year': 2023, 'to_year': 2024, 'scope': document['scope'], 'dataset': 'real'})
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual(len(comparison.json()['citations']), 2)

    def test_batch_invalid_member_never_saves_a_prefix(self):
        payload = self.batch_payload()
        payload['facts'][1]['page'] = 999
        self.assertEqual(self.client.post('/api/facts/batch', json=payload).status_code, 404)
        self.assertEqual(self.client.get('/api/facts').json(), [])
        for invalid in [{'facts': []}, {'facts': self.batch_payload()['facts'] * 26},
                        {'facts': [{**self.batch_payload()['facts'][0], 'confirmed': False}]}]:
            self.assertEqual(self.client.post('/api/facts/batch', json=invalid).status_code, 422)
        self.assertEqual(self.client.get('/api/facts').json(), [])

    def test_batch_rejects_demo_and_out_of_scope_sources(self):
        payload = self.batch_payload()
        seed_demo(self.app.state.store)
        payload['facts'][1]['document_id'] = 'demo-ANPHAT-2024'
        self.assertEqual(self.client.post('/api/facts/batch', json=payload).status_code, 409)
        self.assertEqual(self.app.state.store.facts(dataset='real'), [])
        payload = self.batch_payload()
        with self.app.state.store.connection() as db:
            db.execute('INSERT OR REPLACE INTO document_context(document_id,allowed_pages) VALUES (?,?)', (self.document, '[]'))
        self.assertEqual(self.client.post('/api/facts/batch', json=payload).status_code, 409)

    def test_batch_storage_failure_rolls_back_earlier_insert(self):
        payload = self.batch_payload()
        with self.app.state.store.connection() as db:
            db.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON facts
                WHEN NEW.year=2024 BEGIN SELECT RAISE(ABORT,'simulated storage error'); END""")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post('/api/facts/batch', json=payload)
        self.assertEqual(self.client.get('/api/facts').json(), [])

    def test_batch_duplicates_do_not_overwrite_existing_values(self):
        payload = self.batch_payload()
        self.client.post('/api/facts/batch', json=payload)
        payload['facts'] = [{**payload['facts'][0], 'value': '999'}]
        self.assertEqual(self.client.post('/api/facts/batch', json=payload).status_code, 201)
        self.assertEqual(len(self.client.get('/api/facts').json()), 3)
        from finlens.finance import compare, EvidenceError
        document = self.app.state.store.get_document(self.document)
        with self.assertRaises(EvidenceError):
            compare(self.app.state.store, document['company'], 'interest_expense', 2023, 2024, document['scope'])

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.app = create_app(self.tmp.name, ModelConfig())
        with table_pdf() as pdf:
            result = ingest_pdf(self.app.state.store, pdf.tobytes(), "test.pdf", "Company", 2024, "consolidated")
        self.document = result["document"]["id"]
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        assert Path(self.tmp.name).resolve().is_relative_to(self.root.resolve())
        self.tmp.cleanup()

    def test_drafts_never_enter_calculations_until_explicit_confirmation(self):
        result = self.client.get(f"/api/documents/{self.document}/suggestions?page=1")
        self.assertEqual(result.status_code, 200)
        candidates = result.json()["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertEqual(self.client.get("/api/facts").json(), [])
        payload = {key: candidates[1][key] for key in ["document_id", "page", "metric", "year", "value", "unit", "currency"]}
        payload["note"] = "Human reviewed the source row and year column."
        self.assertEqual(self.client.post("/api/facts", json=payload).status_code, 422)
        payload["confirmed"] = True
        self.assertEqual(self.client.post("/api/facts", json=payload).status_code, 201)
        self.assertEqual(len(self.client.get("/api/facts").json()), 1)
        again = self.client.get(f"/api/documents/{self.document}/suggestions?page=1").json()["candidates"]
        self.assertFalse(again[0]["already_confirmed"])
        self.assertTrue(again[1]["already_confirmed"])

    def test_suggestion_api_uses_its_own_local_ocr_directory(self):
        with patch('finlens.api.suggest_page',return_value={'candidates':[],'warnings':[],'notice':'review'}) as suggest:
            result=self.client.get(f'/api/documents/{self.document}/suggestions?page=1')
        self.assertEqual(result.status_code,200)
        self.assertEqual(suggest.call_args.kwargs['ocr_dir'],self.app.state.store.root/'ocr/tessdata')
        self.assertEqual(self.app.state.store.facts(),[])

    def test_missing_document_page_and_demo_are_rejected(self):
        self.assertEqual(self.client.get("/api/documents/missing/suggestions?page=1").status_code, 404)
        self.assertEqual(self.client.get(f"/api/documents/{self.document}/suggestions?page=2").status_code, 404)
        self.assertEqual(self.client.get(f"/api/documents/{self.document}/suggestions?page=0").status_code, 422)
        seed_demo(self.app.state.store)
        self.assertEqual(self.client.get("/api/documents/demo-ANPHAT-2024/suggestions?page=1").status_code, 409)
