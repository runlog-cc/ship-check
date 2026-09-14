import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class OfflineExampleTests(unittest.TestCase):
    def test_real_offline_harness_passes_and_fails_without_credentials(self):
        self.assertTrue((ROOT/'examples/harness.py').is_file(), 'runnable offline harness is required')
        with tempfile.TemporaryDirectory() as directory:
            for fail in (False, True):
                with self.subTest(fail=fail):
                    subprocess.run([sys.executable, str(ROOT/'examples/harness.py'), '--output', directory, *(['--fail'] if fail else [])], check=True, capture_output=True, text=True)
                    result = subprocess.run([sys.executable, str(ROOT/'runner.py'), '--offline', '--scenarios', directory+'/suite.json', '--trace', directory+'/trace.json'], capture_output=True, text=True, env={'PATH': '/usr/bin:/bin'})
                    self.assertEqual(result.returncode, 1 if fail else 0)
                    self.assertNotIn('APPROVED', result.stdout)
                    evidence = json.loads(Path(directory, 'evidence.json').read_text())
                    self.assertEqual(evidence['schema'], 'runlog.evaluation-evidence.v2')
