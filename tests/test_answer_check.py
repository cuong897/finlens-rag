import json
from pathlib import Path
import tempfile
import unittest

from finlens.answer_check import GenerationBudget,run_answer_checks,score_contract
from finlens.engine import Engine
from finlens.ingest import seed_demo
from finlens.providers import Generation,ModelConfig,ProviderError
from finlens.store import Store


class AnswerCheckTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);seed_demo(self.store)
        self.dataset=json.loads((Path(__file__).resolve().parents[1]/'finlens/fixtures/answer_cases_v1.json').read_text(encoding='utf-8'))

    def test_offline_never_claims_model_quality_and_exports_review(self):
        r=run_answer_checks(Engine(self.store,ModelConfig()),self.dataset,Path(self.tmp.name)/'out')
        self.assertEqual((r['completed'],r['contract_passed'],r['skipped']),(10,10,5))
        self.assertEqual(r['generation_questions_attempted'],0)
        self.assertIsNone(r['semantic_accuracy']);self.assertIsNone(r['source_entailment_accuracy'])
        self.assertTrue((Path(self.tmp.name)/'out/human-review.csv').exists())

    def test_outage_is_not_counted_as_correct_abstention_and_stops_model_questions(self):
        class Broken:
            def generate_with_report(self,*args):raise ProviderError('Unavailable',code='service_unavailable',attempts=[{'model':'test','status':'failed','code':'service_unavailable'}])
        cases=[{**self.dataset['cases'][10],'expected_status':'insufficient_evidence'},self.dataset['cases'][11]]
        r=run_answer_checks(Engine(self.store,ModelConfig(chat_model='test'),Broken()),{'cases':cases,'label_origin':'synthetic'},Path(self.tmp.name)/'out',live=True,pause=lambda _:None)
        self.assertEqual(r['contract_passed'],0)
        self.assertEqual(r['correct_abstentions_contract_only'],0)
        self.assertTrue(r['provider_error_stop']);self.assertEqual(r['completed'],1)
        self.assertEqual(r['results'][1]['reason'],'provider_error_stop')

    def test_keywords_and_valid_sources_do_not_become_semantic_accuracy(self):
        case=self.dataset['cases'][10]
        class Reply:
            def generate_with_report(self,q,e):return Generation('Logistics và tồn kho. [S1]','test',[{'model':'test','status':'succeeded'}])
        r=run_answer_checks(Engine(self.store,ModelConfig(chat_model='test'),Reply()),{'cases':[case],'label_origin':'synthetic'},Path(self.tmp.name)/'out',live=True,pause=lambda _:None)
        self.assertEqual(r['results'][0]['concept_matches_diagnostic'],[True,True])
        self.assertEqual(r['results'][0]['semantic_review'],'pending')
        self.assertIsNone(r['semantic_accuracy'])

    def test_budget_guards_actual_generation_calls(self):
        class Reply:
            def generate_with_report(self,*args):return 'sent'
        guarded=GenerationBudget(Reply(),1)
        self.assertEqual(guarded.generate_with_report('q',[]),'sent')
        with self.assertRaises(ProviderError):guarded.generate_with_report('q',[])
        self.assertEqual(guarded.used,1)
        with self.assertRaises(ValueError):
            run_answer_checks(Engine(self.store,ModelConfig(chat_model='test')),self.dataset,Path(self.tmp.name)/'out')
