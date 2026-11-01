import csv
import json
from pathlib import Path
import tempfile
import unittest

from finlens.answer_check import run_answer_checks
from finlens.engine import Engine
from finlens.ingest import seed_demo
from finlens.providers import ModelConfig
from finlens.review_scores import score_reviews
from finlens.store import Store


class ReviewScoresTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        ds=json.loads(Path('finlens/fixtures/answer_cases_v1.json').read_text(encoding='utf-8'));ds['cases']=ds['cases'][:2]
        self.cases=self.root/'cases.json';self.cases.write_text(json.dumps(ds),encoding='utf-8')
        store=Store(self.root/'data');seed_demo(store)
        run_answer_checks(Engine(store,ModelConfig()),ds,self.root/'answers')
        with (self.root/'answers/human-review.csv').open(encoding='utf-8-sig',newline='') as f:self.rows=list(csv.DictReader(f))

    def run_review(self):
        path=self.root/'reviews.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=self.rows[0].keys());writer.writeheader();writer.writerows(self.rows)
        return score_reviews(self.root/'answers/answer-check.json',path,self.cases,self.root/'score')

    def grade(self,row):row.update(correctness='2',completeness='2',source_support='2',reviewer='Test reviewer')

    def test_blank_and_partial_reviews_never_become_overall_accuracy(self):
        self.grade(self.rows[0]);r=self.run_review()
        self.assertEqual(r['pending_answers'],1);self.assertIsNone(r['quality_metrics'])
        self.assertFalse(r['independent_review_verified'])

    def test_full_review_reports_explicit_denominators_without_certifying_reviewer(self):
        for row in self.rows:self.grade(row)
        r=self.run_review();self.assertEqual(r['quality_metrics']['fully_correct_rate'],1)
        self.assertEqual(r['quality_metrics']['source_support_denominator'],2)
        self.assertFalse(r['independent_review_verified'])

    def test_tampered_answer_is_rejected(self):
        self.grade(self.rows[0]);self.rows[0]['answer']='Changed answer'
        with self.assertRaises(ValueError):self.run_review()

    def test_provider_outage_cannot_be_reviewed_as_correct_abstention(self):
        path=self.root/'answers/answer-check.json';r=json.loads(path.read_text(encoding='utf-8'))
        r['results'][0]['response'].update(status='insufficient_evidence',generation_error={'code':'timeout'})
        path.write_text(json.dumps(r),encoding='utf-8')
        self.grade(self.rows[0]);self.rows[0]['abstention_correct']='yes'
        with self.assertRaises(ValueError):self.run_review()

    def test_missing_reviewer_and_duplicate_ids_are_rejected(self):
        self.grade(self.rows[0]);self.rows[0]['reviewer']=''
        with self.assertRaises(ValueError):self.run_review()
        self.rows[0]['reviewer']='Test';self.rows[1]['id']=self.rows[0]['id']
        with self.assertRaises(ValueError):self.run_review()
