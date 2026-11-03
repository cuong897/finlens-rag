from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finlens.accounting_policy import policy_intent, rank_policy_chunks
from finlens.engine import Engine
from finlens.models import AskRequest
from finlens.providers import ModelConfig, grounded_request
from finlens.retrieval import financial_query
from finlens.store import Store


class AccountingPolicyTests(unittest.TestCase):
    def test_liquidity_wording_in_cash_policy_does_not_request_ratio_facts(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        class Reply:
            def generate(self,q,e):return 'Tương đương tiền là khoản đầu tư ngắn hạn có tính thanh khoản cao. [S1]'
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            store=Store(tmp)
            store.add_document({'id':'d','company':'EXAMPLE','report_year':2025,'scope':'consolidated',
                'filename':'notes.pdf','sha256':'example','is_demo':False,'pdf_path':None},
                ['Cash equivalents are short-term highly liquid investments readily convertible to known cash amounts.'])
            engine=Engine(store,ModelConfig(chat_model='fake'),Reply())
            with patch('finlens.engine.debt_capacity',side_effect=AssertionError('No ratio calculation for a definition')):
                a=engine.answer(AskRequest(company='EXAMPLE',years=[2025],question='Nguyên tắc phân loại tương đương tiền để đáp ứng khả năng thanh toán là gì?'))
            self.assertEqual(a['mode'],'generated')
            self.assertEqual(a['ratios'],[])
            self.assertEqual(store.facts(),[])

    def test_policy_and_numeric_requests_have_distinct_routes(self):
        for q in ['Nguyên tắc đo lường giá trị hàng tồn kho là gì?',
                  'TSCĐ hữu hình được trình bày theo giá trị nào?',
                  'How are inventories valued under the accounting policies?',
                  'Giải thích cách đánh giá lại khoản mục ngoại tệ cuối kỳ.']:
            self.assertIsNotNone(policy_intent(q),q)
        for q in ['Giá trị hàng tồn kho năm 2025 là bao nhiêu?',
                  'So sánh tài sản cố định năm 2024 và 2025 theo phương pháp kê khai.',
                  'Vì sao chi phí lãi vay tăng?',
                  'Giải thích thuật toán AI dự đoán tỷ giá hàng ngày.',
                  'Vì sao doanh thu giảm?']:
            self.assertIsNone(policy_intent(q),q)

    def test_policy_expansion_is_bilingual_without_supplying_an_answer(self):
        self.assertIn('inventories',financial_query('Nguyên tắc xác định giá trị hàng tồn kho'))
        self.assertNotIn('straight line',financial_query('Khấu hao năm 2025 là bao nhiêu?'))

    def test_policy_body_beats_an_inventory_amount_and_heading_only(self):
        hits=[{'id':1,'document_id':'d','page':1,'score':1,'text':'Inventories 1,234,567'},
              {'id':2,'document_id':'d','page':2,'score':.8,'text':'Inventories heading only'},
              {'id':3,'document_id':'d','page':3,'score':.1,'text':'Inventories are measured at the lower of cost and net realisable value.'}]
        ranked=rank_policy_chunks('Nguyên tắc xác định giá trị hàng tồn kho',hits,{})
        self.assertEqual(ranked[0]['id'],3)
        self.assertNotIn('policy_rank',hits[0])

    def test_closing_fx_prioritizes_both_sides_over_receivables_only(self):
        hits=[{'id':1,'document_id':'d','page':1,'score':1,'text':'Receivables are foreign currency monetary items translated at the buying rate.'},
              {'id':2,'document_id':'d','page':2,'score':.1,'text':'Foreign currency monetary assets and liabilities use the transfer buying rate and transfer selling rate, respectively.'}]
        ranked=rank_policy_chunks('Giải thích cách đánh giá lại ngoại tệ cuối kỳ',hits,{})
        self.assertEqual(ranked[0]['id'],2)
        self.assertEqual(len(ranked),2)

    def test_grounding_prompt_covers_definitions_and_preserves_claim_scope(self):
        _,system,_=grounded_request('policy',[{'id':'S1','excerpt':'Public source'}])
        self.assertIn('không đòi một nguyên nhân',system)
        self.assertIn('không đại diện mọi tài sản',system)
        self.assertIn('bình quân gia quyền',system)

    def test_policy_reaches_model_without_facts_but_amount_lookup_stays_local(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        class PolicyReply:
            calls=0
            def generate(self,q,e):
                self.calls+=1
                return 'Giá trị thấp hơn giữa giá gốc và giá trị thuần có thể thực hiện được. [S1]'
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            store=Store(tmp)
            store.add_document({'id':'d','company':'EXAMPLE','report_year':2025,'scope':'consolidated',
                'filename':'notes.pdf','sha256':'example','is_demo':False,'pdf_path':None},
                ['Inventories are measured at the lower of cost and net realisable value.'])
            provider=PolicyReply();engine=Engine(store,ModelConfig(chat_model='fake'),provider)
            req=lambda q:AskRequest(company='EXAMPLE',years=[2025],question=q)
            with patch('finlens.engine.select_fact',side_effect=AssertionError('Policy does not require facts')):
                result=engine.answer(req('Giải thích nguyên tắc xác định giá trị hàng tồn kho.'))
            self.assertEqual(result['mode'],'generated')
            self.assertEqual(result['retrieval_reranker'],'accounting_policy_context')
            result=engine.answer(req('Giá trị hàng tồn kho năm 2025 là bao nhiêu?'))
            self.assertFalse(result['generation_attempted'])
            self.assertEqual(provider.calls,1)
            self.assertEqual(store.facts(),[])
