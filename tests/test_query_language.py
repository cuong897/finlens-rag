import unittest

from finlens.query_language import canonical_query
from finlens.financial_retrieval import profile
from finlens.retrieval import financial_query


class QueryLanguageTests(unittest.TestCase):
    def test_compound_abbreviations_preserve_financial_intent(self):
        self.assertEqual({c['key'] for c in profile('LCTT thuần từ HĐKD năm 2024')}, {'cfo'})
        self.assertEqual({c['key'] for c in profile('TSNH và VCSH cuối năm 2024')}, {'current_assets', 'equity'})
        self.assertIn('cost of sales', financial_query('GVHB năm 2024'))

    def test_boundaries_negation_and_amounts_survive(self):
        self.assertEqual(canonical_query('Không tăng LNST: -1.234,50 năm 2024'),
                         'khong tang loi nhuan sau thue: -1.234,50 nam 2024')
        self.assertEqual(canonical_query('DT LN TS XTSNH DTT2024'), 'dt ln ts xtsnh dtt2024')
        self.assertEqual(profile('Vì sao LNST giảm?'), [])
        for query in ['Hàng tồn kho thuần', 'giá trị tồn kho thuần', 'LCTT thuần từ HĐKD']:
            canonical = canonical_query(query)
            self.assertEqual(canonical_query(canonical), canonical)
