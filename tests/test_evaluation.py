from pathlib import Path
import tempfile
import unittest

from finlens.evaluate import evaluate


class EvaluationTests(unittest.TestCase):
    def test_synthetic_smoke_corpus_and_artifacts(self):
        base = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            result = evaluate(directory)
            self.assertEqual(result["cases"], 24)
            self.assertEqual(result["passed"], result["cases"], [r for r in result["results"] if not r["passed"]])
            self.assertEqual(result["recall_at_5"], 1.0)
            self.assertTrue((Path(directory) / "evaluation.md").is_file())
            self.assertFalse(result["configuration"]["models_used"])
            assert Path(directory).resolve().is_relative_to(base.resolve())
