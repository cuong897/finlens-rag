import unittest

from finlens.financial_retrieval import expansion, label_strength, profile, rank_rows, statement_kinds
from finlens.retrieval import contextual_fusion


class FinancialRowsTests(unittest.TestCase):
    def test_statement_detection_also_supports_standalone_scope(self):
        self.assertEqual(statement_kinds("INCOME STATEMENT\nNet profit after tax 321"), {"income"})
        self.assertEqual(statement_kinds("Balance sheet\nTotal assets 123"), {"balance"})
        self.assertEqual(statement_kinds("NOTES TO THE FINANCIAL STATEMENTS\nINCOME STATEMENT detail"), {"notes"})
    def test_specific_bilingual_concepts_and_negation_do_not_collapse_to_parent_metric(self):
        self.assertEqual({c["key"] for c in profile("Doanh thu thuần từ bán trang sức trong thuyết minh")}, {"jewelry_revenue"})
        self.assertEqual({c["key"] for c in profile("Khoản tiền không gồm tương đương tiền")}, {"cash"})
        self.assertIn("raw materials", expansion("Giá gốc nguyên vật liệu"))
        self.assertIn("cfo", {c["key"] for c in profile("Operating cash flow")})

    def test_factor_costs_are_accounting_notes_not_a_causal_question(self):
        self.assertTrue(profile("Chi phí nguyên vật liệu theo yếu tố trong thuyết minh"))
        self.assertEqual(profile("Vì sao chi phí nguyên vật liệu tăng?"), [])

    def test_sibling_rows_are_not_confused(self):
        for question, wrong in [("Tổng nợ phải trả", "Current liabilities 123456"),
                                 ("Tài sản ngắn hạn", "Non-current assets 123456"),
                                 ("Tiền mặt", "Cash and cash equivalents 123456")]:
            concept = profile(question)[0]
            self.assertLess(label_strength(wrong, concept), 1)

    def test_local_statement_header_beats_navigation_and_other_statements(self):
        hits = [{"id": 1, "document_id": "a", "page": 1, "text": "NOTES TO THE FINANCIAL STATEMENTS\nProfit after tax 1000"},
                {"id": 2, "document_id": "a", "page": 2, "text": "CONSOLIDATED INCOME STATEMENT\nProfit after tax 1000"}]
        ranked = rank_rows("Lợi nhuận sau thuế trên báo cáo kết quả kinh doanh", hits, {})
        self.assertEqual(ranked[0]["id"], 2)
        notes = rank_rows("Lợi nhuận sau thuế trong thuyết minh", hits, {})
        self.assertEqual(notes[0]["id"], 1)

    def test_multi_value_selection_covers_both_statements_before_repeated_cashflow_chunks(self):
        hits = [{"id": i, "document_id": "a", "page": 1, "text": f"CONSOLIDATED STATEMENT OF CASH FLOWS\nNet cash flows from operating activities {100+i}"} for i in range(4)]
        hits.append({"id": 10, "document_id": "a", "page": 2, "text": "CONSOLIDATED INCOME STATEMENT\nNet profit after tax 777"})
        ranked = rank_rows("Đối chiếu dòng tiền kinh doanh và lợi nhuận sau thuế", hits, {}, 2)
        self.assertEqual({h["page"] for h in ranked}, {1, 2})

    def test_amounts_and_document_names_do_not_control_ranking_and_scope_cannot_expand(self):
        hits = [{"id": 1, "document_id": "allowed", "page": 1, "text": "CONSOLIDATED BALANCE SHEET\nTotal assets 123"},
                {"id": 2, "document_id": "allowed", "page": 2, "text": "NOTES TO THE FINANCIAL STATEMENTS\nTotal assets 456"}]
        first = rank_rows("Tổng tài sản", hits, {})
        swapped = [{**h, "text": h["text"].replace("123", "999999999").replace("456", "88888")} for h in hits]
        second = rank_rows("Tổng tài sản", swapped, {})
        self.assertEqual([h["id"] for h in first], [h["id"] for h in second])
        self.assertTrue(all(h["document_id"] == "allowed" for h in second))
