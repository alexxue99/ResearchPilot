import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from researchpilot.demo_export import demo_bundle, encode_demo
from researchpilot.demo_publish import publish_demo
from researchpilot.models import ResearchState, Status


class DemoPublishTests(unittest.TestCase):
    def setUp(self):
        self.pdf = b'%PDF-1.7\nfixture report\n%%EOF\n'
        renderer = patch('researchpilot.demo_export.render_latex_pdf', return_value=self.pdf)
        self.renderer = renderer.start()
        self.addCleanup(renderer.stop)

    def archive(self, root):
        artifact = root / 'runtime' / 'artifacts' / 'plot.svg'
        artifact.parent.mkdir(parents=True)
        artifact.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding='utf-8')
        state = ResearchState('Test conjecture', 'Investigate', status=Status.COMPLETED,
                              report='# Results\nInconclusive.', artifacts=[str(artifact)])
        path = root / 'demo.zip'
        path.write_bytes(demo_bundle(state, root / 'runtime'))
        return path, state

    def test_publish_portable_gallery_without_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, state = self.archive(root)
            public = root / 'public' / 'demos'
            target = publish_demo(archive, 'test-demo', 'Test demo', 'An inconclusive investigation.', public)
            catalog = json.loads((public / 'catalog.json').read_text())
            self.assertEqual(catalog[0]['slug'], 'test-demo')
            self.assertEqual(catalog[0]['status'], 'completed')
            self.assertEqual(catalog[0]['report_pdf'], 'report.pdf')
            self.assertEqual((target / 'report.pdf').read_bytes(), self.pdf)
            snapshot = json.loads((target / 'state.json').read_text())
            self.assertEqual(snapshot['id'], state.id)
            self.assertTrue((target / snapshot['artifacts'][0]).is_file())
            self.assertEqual((target / 'snapshot.zip').read_bytes(), archive.read_bytes())
            with self.assertRaisesRegex(ValueError, 'exists'):
                publish_demo(archive, 'test-demo', 'Test', 'Summary', public)
            self.assertEqual(len(json.loads((public / 'catalog.json').read_text())), 1)

    def test_older_snapshot_gets_pdf_and_updated_download_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, _ = self.archive(root)
            with zipfile.ZipFile(original) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(files.pop('manifest.json'))
            del files['report.pdf']
            original.write_bytes(encode_demo(files, manifest))
            self.renderer.reset_mock()
            target = publish_demo(original, 'legacy', 'Legacy', 'Saved result', root / 'public')
            self.renderer.assert_called_once_with('# Results\nInconclusive.', 'Test conjecture')
            with zipfile.ZipFile(target / 'snapshot.zip') as archive:
                self.assertEqual(archive.read('report.pdf'), self.pdf)
                manifest = json.loads(archive.read('manifest.json'))
                self.assertIn('report.pdf', [item['path'] for item in manifest['files']])
                self.assertEqual(archive.read('manifest.json'), (target / 'manifest.json').read_bytes())
            with zipfile.ZipFile(original) as archive:
                self.assertNotIn('report.pdf', archive.namelist())

    def test_replace_keeps_one_card_and_rolls_back_on_catalog_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _ = self.archive(root)
            public = root / 'public'
            target = publish_demo(archive, 'demo', 'Original', 'Summary', public)
            old_catalog = (public / 'catalog.json').read_bytes()
            old_snapshot = (target / 'snapshot.zip').read_bytes()
            with patch.object(Path, 'replace', side_effect=OSError('catalog locked')):
                with self.assertRaisesRegex(OSError, 'locked'):
                    publish_demo(archive, 'demo', 'Updated', 'Summary', public, replace=True)
            self.assertEqual((public / 'catalog.json').read_bytes(), old_catalog)
            self.assertEqual((target / 'snapshot.zip').read_bytes(), old_snapshot)
            publish_demo(archive, 'demo', 'Updated', 'Summary', public, replace=True)
            catalog = json.loads((public / 'catalog.json').read_text())
            self.assertEqual(len(catalog), 1)
            self.assertEqual(catalog[0]['title'], 'Updated')

    def test_rejects_tampering_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, _ = self.archive(root)
            with zipfile.ZipFile(original) as archive:
                contents = {name: archive.read(name) for name in archive.namelist()}
            for name, data, expected in [('report.md', b'tampered', 'integrity'), ('../escape.md', b'escape', 'Unsafe')]:
                candidate = root / 'bad.zip'
                with zipfile.ZipFile(candidate, 'w') as archive:
                    for entry, content in {**contents, name: data}.items():
                        archive.writestr(entry, content)
                with self.assertRaisesRegex(ValueError, expected):
                    publish_demo(candidate, 'bad', 'Bad', 'Summary', root / 'public')
                self.assertFalse((root / 'public').exists())
            with self.assertRaisesRegex(ValueError, 'slug'):
                publish_demo(original, '../outside', 'Bad', 'Summary', root / 'public')


if __name__ == '__main__':
    unittest.main()
