import unittest

import pymupdf

from finlens.suggestions import extract_page
from finlens.text import normalize, tokens


def relative_pdf(*, heading="For the year ended 31 March 2025", labels=("Current year", "Previous year")):
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((40, 45), "CONSOLIDATED INCOME STATEMENT")
    page.insert_text((40, 65), heading)
    page.insert_text((440, 80), "Currency: VND")
    for label, end in zip(labels, [430, 560]):
        page.insert_text((end - pymupdf.get_text_length(label, fontsize=11), 100), label, fontsize=11)
    page.insert_text((40, 145), "10 Net revenue from sale of goods and")
    page.insert_text((40, 160), "rendering of services")
    for value, end in zip(["8.202.642.306.320", "7.341.773.438.715"], [430, 560]):
        page.insert_text((end - pymupdf.get_text_length(value, fontsize=11), 145), value, fontsize=11)
    return pdf


CONTEXT = {"report_year": 2024, "period_start": "2024-04-01", "period_end": "2025-03-31"}


class PDFLayoutTests(unittest.TestCase):
    def test_relative_years_require_matching_annual_period_and_keep_drafts_unverified(self):
        with relative_pdf() as pdf:
            result = extract_page(pdf[0], "doc", 7, context=CONTEXT)
            self.assertEqual(extract_page(pdf[0], "doc", 7)["candidates"], [])
        self.assertEqual([(c["year"], c["value"]) for c in result["candidates"]], [(2024, "8202642306320"), (2023, "7341773438715")])
        self.assertTrue(all(c["year_basis"] == "document_fiscal_period" and not c["verified"] for c in result["candidates"]))
        self.assertTrue(all(c["page"] == 7 for c in result["candidates"]))

    def test_wrong_or_short_period_does_not_guess_current_year(self):
        for context in [{**CONTEXT, "period_end": "2024-12-31"}, {**CONTEXT, "period_start": "2024-10-01"},
                        {**CONTEXT, "report_year": 2034}]:
            with relative_pdf() as pdf:
                self.assertEqual(extract_page(pdf[0], "doc", 1, context=context)["candidates"], [])

    def test_opening_balance_date_maps_to_prior_year_only_with_period_context(self):
        with relative_pdf(heading="As at 31 December 2024", labels=("31/12/2024", "01/01/2024")) as pdf:
            # Date columns normally have currency headers immediately below.
            for end in [430, 560]:
                pdf[0].insert_text((end - pymupdf.get_text_length("VND", fontsize=11), 120), "VND", fontsize=11)
            context = {"report_year": 2024, "period_start": "2024-01-01", "period_end": "2024-12-31"}
            values = extract_page(pdf[0], "doc", 1, context=context)["candidates"]
            self.assertEqual(extract_page(pdf[0], "doc", 1)["candidates"], [])
        self.assertEqual([c["year"] for c in values], [2024, 2023])
        self.assertTrue(all(c["year_basis"] == "period_date" for c in values))

    def test_spread_tables_are_isolated_and_source_coordinates_stay_on_original_page(self):
        with pymupdf.open() as source, pymupdf.open() as spread:
            for label, currency, values in [("Net profit after tax", "VND", ["1000000", "900000"]),
                                             ("Cash and cash equivalents", "USD", ["1234567", "1200000"])]:
                page = source.new_page(width=600, height=800)
                page.insert_text((40, 45), "CONSOLIDATED FINANCIAL STATEMENT")
                for year, value, end in zip([2024, 2023], values, [430, 560]):
                    for text, y in [(str(year), 100), (currency, 120), (value, 160)]:
                        page.insert_text((end - pymupdf.get_text_length(text, fontsize=11), y), text, fontsize=11)
                page.insert_text((40, 160), label)
            page = spread.new_page(width=1200, height=800)
            for i in [0, 1]:
                page.show_pdf_page(pymupdf.Rect(i * 600, 0, (i + 1) * 600, 800), source, i)
            candidates = extract_page(page, "doc", 109)["candidates"]
        self.assertEqual(len(candidates), 4)
        self.assertTrue(all(c["currency"] == "VND" and c["bbox"][2] < 600 for c in candidates if c["metric"] == "net_income"))
        self.assertTrue(all(c["currency"] == "USD" and c["bbox"][0] >= 600 for c in candidates if c["metric"] == "cash"))

    def test_english_ligatures_do_not_break_financial_labels_or_search(self):
        self.assertEqual(normalize("proﬁt"), "profit")
        self.assertEqual(tokens("Gross proﬁt"), ["gross", "profit"])
