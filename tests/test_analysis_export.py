from pathlib import Path
import shutil
import subprocess
import unittest


class AnalysisExportTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser export checks')
    def test_browser_exports(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', '--test', str(root / 'tests/analysis_export.cjs')],
                                cwd=root, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
