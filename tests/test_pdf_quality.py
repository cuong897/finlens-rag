import json
from pathlib import Path
import unittest

from scripts.evaluate_pdf_quality import score_fields


class PDFQualityScoringTests(unittest.TestCase):
    def field(self, **updates):
        return {'metric':'cash','year':2024,'value':'1234567','raw_value':'1,234,567',
                'unit':'one','currency':'VND',**updates}

    def candidate(self, **updates):
        return {**self.field(), 'verified':False, **updates}

    def test_visible_values_do_not_prove_correct_year_or_unit_mapping(self):
        row=score_fields([self.field()], '1,234,567', [self.candidate(year=2023)])
        self.assertEqual(row['numeric_text_preserved'],1)
        self.assertEqual(row['exact_drafts'],0); self.assertEqual(row['wrong_drafts'],1)
        row=score_fields([self.field()], '1,234,567', [self.candidate(unit='million')])
        self.assertEqual(row['exact_drafts'],0)

    def test_duplicate_proposals_cannot_inflate_recall_or_precision(self):
        row=score_fields([self.field()], '1,234,567', [self.candidate(),self.candidate()])
        self.assertEqual((row['exact_drafts'],row['proposals'],row['duplicates']),(1,2,1))
        with self.assertRaises(ValueError):score_fields([self.field(),self.field()],'',[])

    def test_missing_values_and_lost_signs_stay_in_denominator(self):
        row=score_fields([self.field(raw_value='(1,234,567)',value='-1234567')], '1,234,567', [])
        self.assertEqual(row['expected_fields'],1); self.assertEqual(row['numeric_text_preserved'],0)
        self.assertEqual(score_fields([self.field()], '11,234,567', [])['numeric_text_preserved'],0)

    def test_corpus_is_explicitly_synthetic_balanced_and_has_800_unique_fields(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'finlens/fixtures/pdf_quality_corpus_v1.json').read_text(encoding='utf-8'))
        self.assertEqual(data['split'],'synthetic_development')
        self.assertEqual(len(data['pages']),100)
        self.assertEqual(sum(p['language']=='vi' for p in data['pages']),50)
        self.assertEqual(sum(len(p['fields']) for p in data['pages']),800)
        for page in data['pages']:
            self.assertEqual(score_fields(page['fields'],'',[])['expected_fields'],8)
