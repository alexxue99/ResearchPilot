import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout

from researchpilot.demo_export import demo_bundle, save_demo
from researchpilot.models import ExperimentDesign, ExperimentResult, ExperimentalEvidence, ResearchState, Status


class DemoExportTests(unittest.TestCase):
    def setUp(self):
        self.pdf = b'%PDF-1.7\nfixture typeset report\n%%EOF\n'
        renderer = patch('researchpilot.demo_export.render_latex_pdf', return_value=self.pdf)
        self.renderer = renderer.start()
        self.addCleanup(renderer.stop)

    def test_cli_save_and_export_existing_run(self):
        from researchpilot.cli import main
        def cli(*args):
            output = io.StringIO()
            with patch.object(sys, 'argv', ['researchpilot', *args]), redirect_stdout(output):
                main()
            return json.loads(output.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'runtime'
            first = Path(tmp) / 'demos' / 'first.zip'
            command = ['--workspace', str(root)]
            metadata = cli(*command, 'research', 'Every prime number is odd.',
                '--plan-only', '--provider', 'deterministic', '--save-demo', str(first))
            self.assertTrue(first.is_file())
            self.assertEqual(cli(*command, 'list')[0]['id'], metadata['id'])
            second = Path(tmp) / 'demos' / 'second.zip'
            cli(*command, 'export-demo', metadata['id'], '--output', str(second))
            with zipfile.ZipFile(second) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json'))['research_id'], metadata['id'])

    def state(self, root):
        artifact = root / 'artifacts' / 'attempt-test' / 'plot.svg'
        artifact.parent.mkdir(parents=True)
        artifact.write_text('<svg>Measured results</svg>', encoding='utf-8')
        state = ResearchState('Does the method converge?', 'Test convergence', status=Status.COMPLETED,
                              report='## Report\nMeasured convergence.', artifacts=[str(artifact)])
        state.experiments_planned = [ExperimentDesign('d1', 'Convergence', 'Converges', [], [], [], [], [], {}, [1], '', [], code='print(1)')]
        state.experiments_completed = [ExperimentResult('r1', 'd1', 'completed', {}, {'error': 0.1}, artifacts=[str(artifact)])]
        state.experimental_evidence = [ExperimentalEvidence('r1', 'Error measured', 'supports', evidence_paths=['metrics.error'])]
        state.record('execution', 'completed', 'Measured', outputs={'artifact': str(artifact), 'api_key': 'private-value', 'output_tokens': 23})
        return state, artifact

    def test_portable_archive_survives_runtime_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'runtime'
            state, artifact = self.state(root)
            original = state.to_dict()
            target = save_demo(state, root, Path(tmp) / 'demos' / 'example.zip')
            artifact.unlink()
            with zipfile.ZipFile(target) as archive:
                data = json.loads(archive.read('state.json'))
                self.assertEqual(data['artifacts'], ['artifacts/attempt-test/plot.svg'])
                self.assertEqual(data['experiments_completed'][0]['artifacts'], data['artifacts'])
                self.assertEqual(data['trace'][0]['outputs']['api_key'], '[REDACTED]')
                self.assertEqual(data['trace'][0]['outputs']['output_tokens'], 23)
                self.assertEqual(archive.read('experiments/01/experiment.py'), b'print(1)')
                self.assertEqual(archive.read('report.pdf'), self.pdf)
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['kind'], 'precomputed_demonstration')
                for entry in manifest['files']:
                    content = archive.read(entry['path'])
                    self.assertEqual(hashlib.sha256(content).hexdigest(), entry['sha256'])
                    self.assertEqual(len(content), entry['bytes'])
                self.assertNotIn(str(root), archive.read('state.json').decode())
            self.assertEqual(state.to_dict(), original)

    def test_rejects_missing_external_and_busy_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'runtime'
            state, artifact = self.state(root)
            state.status = Status.RUNNING
            with self.assertRaisesRegex(ValueError, 'finish'):
                demo_bundle(state, root)
            state.status = Status.COMPLETED
            artifact.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                demo_bundle(state, root)
            external = Path(tmp) / '.env.local'
            external.write_text('secret', encoding='utf-8')
            state.artifacts = [str(external)]
            state.experiments_completed = []
            with self.assertRaisesRegex(ValueError, 'outside'):
                demo_bundle(state, root)

    def test_protects_curated_files_and_redacts_configured_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'runtime'
            state, _ = self.state(root)
            state.report = 'Log contained sk-demo-secret-value'
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-demo-secret-value'}):
                payload = demo_bundle(state, root)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertNotIn(b'sk-demo-secret-value', archive.read('report.md'))
            self.assertNotIn('sk-demo-secret-value', self.renderer.call_args.args[0])
            with self.assertRaisesRegex(ValueError, 'outside'):
                save_demo(state, root, root / 'demo.zip')
            target = save_demo(state, root, Path(tmp) / 'demo.zip')
            with self.assertRaises(FileExistsError):
                save_demo(state, root, target)

    def test_api_export_authentication_and_queued_run(self):
        from fastapi.testclient import TestClient
        from researchpilot.api import create_app
        from researchpilot.jobs import DurableJobQueue
        from researchpilot.storage import ResearchRepository
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'RESEARCHPILOT_API_TOKEN': 'private-service-token', 'RESEARCHPILOT_WORKERS': '0',
            'RESEARCHPILOT_PROVIDER': 'deterministic', 'RESEARCHPILOT_MODE': 'full',
        }):
            root = Path(tmp)
            state, _ = self.state(root)
            ResearchRepository(root / 'researchpilot.db').save(state)
            with TestClient(create_app(root)) as client:
                url = f'/research/{state.id}/demo.zip'
                self.assertEqual(client.get(url).status_code, 401)
                headers = {'Authorization': 'Bearer private-service-token'}
                response = client.get(url, headers=headers)
                self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
                self.assertEqual(response.headers['content-type'], 'application/zip')
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    self.assertEqual(json.loads(archive.read('state.json'))['id'], state.id)
                DurableJobQueue(root / 'jobs.sqlite').enqueue(state.id)
                self.assertEqual(client.get(url, headers=headers).status_code, 409)

    def test_pdf_failure_leaves_no_export_and_is_explained_by_api(self):
        from researchpilot.latex_report import LatexUnavailable
        from fastapi.testclient import TestClient
        from researchpilot.api import create_app
        from researchpilot.storage import ResearchRepository
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'RESEARCHPILOT_WORKERS': '0', 'RESEARCHPILOT_PROVIDER': 'deterministic',
                'RESEARCHPILOT_MODE': 'Full', 'RESEARCHPILOT_API_TOKEN': ''}):
            root = Path(tmp) / 'runtime'
            state, _ = self.state(root)
            self.renderer.side_effect = LatexUnavailable('XeLaTeX is required')
            target = Path(tmp) / 'demo.zip'
            with self.assertRaisesRegex(LatexUnavailable, 'XeLaTeX'):
                save_demo(state, root, target)
            self.assertFalse(target.exists())
            ResearchRepository(root / 'researchpilot.db').save(state)
            with TestClient(create_app(root)) as client:
                response = client.get(f'/research/{state.id}/demo.zip')
            self.assertEqual(response.status_code, 503)
            self.assertIn('XeLaTeX', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
