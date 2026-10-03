import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from researchpilot.executor import DockerPythonExecutor, PythonExecutor


class ExecutorTests(unittest.TestCase):
    def test_captures_output_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = PythonExecutor(tmp).run("from pathlib import Path\nprint('ok')\nPath('x.txt').write_text('x')", "run1")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.stdout.strip(), "ok")
            self.assertTrue(any(path.endswith("x.txt") for path in result.artifacts))

    def test_malformed_code_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = PythonExecutor(tmp).run("def broken(", "run1")
            self.assertEqual(result.status, "failed")
            self.assertIn("SyntaxError", result.stderr)

    def test_non_utf8_output_does_not_break_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = PythonExecutor(tmp).run(
                "import sys\nsys.stdout.buffer.write(b'\\x9d')\nsys.stdout.flush()", "run1")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.stdout, "\ufffd")

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = PythonExecutor(tmp, timeout_seconds=0.1).run("while True: pass", "run1")
            self.assertEqual(result.status, "timeout")

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): PythonExecutor(tmp).run("pass", "../escape")

    def test_docker_executor_applies_isolation_flags(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as run:
            run.return_value.returncode = 0; run.return_value.stdout = "ok"; run.return_value.stderr = ""
            result = DockerPythonExecutor(tmp).run("print('ok')", "run1")
            command = run.call_args.args[0]
            self.assertEqual(result.status, "completed")
            self.assertIn("--network", command); self.assertIn("none", command)
            self.assertIn("--read-only", command); self.assertIn("ALL", command)
            self.assertNotIn("OPENAI_API_KEY", " ".join(command))

    def test_chart_run_receives_result_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.dumps({"measurement": 7}).encode()
            code = ("import json\nfrom pathlib import Path\n"
                    "value = json.loads(Path('result.json').read_text())['measurement']\n"
                    "Path('visualization.svg').write_text("
                    "f'<svg xmlns=\"http://www.w3.org/2000/svg\"><text>{value}</text></svg>')\n")
            result = PythonExecutor(tmp).run(code, "chart1", result_json=payload)
            self.assertEqual(result.status, "completed")
            self.assertEqual((Path(tmp) / "chart1" / "result.json").read_bytes(), payload)
            self.assertIn(">7<", (Path(tmp) / "chart1" / "visualization.svg").read_text())

    def test_docker_chart_run_copies_result_json(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as run:
            run.return_value.returncode = 0; run.return_value.stdout = ""; run.return_value.stderr = ""
            payload = b'{"measurement": 7}'
            DockerPythonExecutor(tmp).run("pass", "chart1", result_json=payload)
            self.assertEqual((Path(tmp) / "chart1" / "result.json").read_bytes(), payload)
