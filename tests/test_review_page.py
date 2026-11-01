import json
from pathlib import Path
import tempfile
import unittest

from finlens.review_page import render_review_page
from finlens.review_scores import original_cell
from finlens.retrieval_dataset import fingerprint


class ReviewPageTests(unittest.TestCase):
    def test_untrusted_answers_are_json_escaped_and_not_executed_as_html(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as work:
            base=Path(work);cases={'cases':[{'id':'x','expected_status':'answered'}]}
            report={'dataset_sha256':fingerprint(cases),'results':[{'id':'x','execution':'completed',
                'response':{'answer':'</script><script>alert(1)</script>'}}]}
            (base/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
            (base/'results.json').write_text(json.dumps(report),encoding='utf-8')
            render_review_page(base/'results.json',base/'cases.json',base/'index.html')
            html=(base/'index.html').read_text(encoding='utf-8')
            self.assertNotIn('</script><script>alert(1)',html)
            self.assertIn('\\u003c/script\\u003e',html)

    def test_formula_escaping_does_not_allow_answer_tampering(self):
        self.assertTrue(original_cell("'=1+1",'=1+1'))
        self.assertFalse(original_cell("'changed",'original'))
        self.assertFalse(original_cell("'=2+2",'=1+1'))
