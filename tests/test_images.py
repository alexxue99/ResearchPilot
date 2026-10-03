import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from researchpilot.images import DockerImageManager, validate_image


class ImageLifecycleTests(unittest.TestCase):
    def test_prepare_records_immutable_id_and_removes_only_owned_alias(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as run:
            image_id = "sha256:" + "a" * 64
            run.side_effect = [SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout=json.dumps({"Id": image_id, "Os": "linux", "RepoDigests": []}), stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr="")]
            manager = DockerImageManager(tmp)
            receipt = manager.prepare("python:3.13-slim")
            self.assertEqual(receipt["id"], image_id)
            self.assertEqual(run.call_args.args[0], ["docker", "image", "tag", image_id, receipt["alias"]])
            run.side_effect = [SimpleNamespace(returncode=0, stdout=json.dumps({"Id": image_id, "Os": "linux"}), stderr=""),
                                 SimpleNamespace(returncode=0, stdout="", stderr="")]
            result = manager.remove(receipt["alias"])
            self.assertEqual(run.call_args.args[0], ["docker", "image", "rm", "--no-prune", receipt["alias"]])
            self.assertFalse(result["shared_images_pruned"])
            self.assertTrue(json.loads(next(Path(tmp).glob("*.json")).read_text())["removed"])

    def test_removal_rejects_foreign_or_changed_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = DockerImageManager(tmp)
            with self.assertRaises(ValueError): manager.remove("python:3.13-slim")
            alias = f"researchpilot-executor:{manager.owner}-{'a' * 16}"
            path = Path(tmp) / f"{alias.split(':')[1]}.json"
            path.write_text(json.dumps({"alias": alias, "owner": manager.owner, "id": "sha256:" + "a" * 64}))
            with patch.object(manager, "inspect", return_value={"id": "sha256:" + "b" * 64}), patch.object(manager, "_run") as run:
                with self.assertRaises(ValueError): manager.remove(alias)
                run.assert_not_called()

    def test_digest_allowed_but_native_windows_and_invalid_names_rejected(self):
        validate_image("python@sha256:" + "a" * 64)
        for image in ("python", "--privileged", "python:3.13 --privileged", "untrusted/image:latest"):
            with self.assertRaises(ValueError): validate_image(image)
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps({"Id": "sha256:" + "a" * 64, "Os": "windows"}), stderr="")):
            with self.assertRaises(ValueError): DockerImageManager(tmp).inspect("python:3.13")
