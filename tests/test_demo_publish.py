import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from researchpilot.demo_export import demo_bundle
from researchpilot.demo_publish import publish_demo
from researchpilot.models import ResearchState, Status


class DemoPublishTests(unittest.TestCase):
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
            snapshot = json.loads((target / 'state.json').read_text())
            self.assertEqual(snapshot['id'], state.id)
            self.assertTrue((target / snapshot['artifacts'][0]).is_file())
            self.assertEqual((target / 'snapshot.zip').read_bytes(), archive.read_bytes())
            with self.assertRaisesRegex(ValueError, 'exists'):
                publish_demo(archive, 'test-demo', 'Test', 'Summary', public)
            self.assertEqual(len(json.loads((public / 'catalog.json').read_text())), 1)

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
