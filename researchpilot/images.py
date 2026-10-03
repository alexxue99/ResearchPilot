"""Explicit Docker image preparation and removal of workspace-owned aliases only."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


OFFICIAL_IMAGE = re.compile(r"python(?::[A-Za-z0-9_][A-Za-z0-9_.-]*)?(?:@sha256:[0-9a-f]{64})?")
MANAGED_IMAGE = re.compile(r"researchpilot-executor:[0-9a-f]{16}-[0-9a-f]{16}")


def validate_image(image):
    if not ((OFFICIAL_IMAGE.fullmatch(image) and image != "python") or MANAGED_IMAGE.fullmatch(image)):
        raise ValueError("image must be an explicit official Python tag/digest or a managed executor alias")


class DockerImageManager:
    def __init__(self, root, docker_command="docker"):
        self.root = Path(root).resolve()
        self.docker = docker_command
        self.owner = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]

    def _run(self, *args, timeout=30):
        try:
            result = subprocess.run([self.docker, *args], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout)
        except FileNotFoundError as exc:
            raise RuntimeError("Docker runtime is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Docker image operation exceeded its deadline") from exc
        if result.returncode:
            raise RuntimeError(f"Docker image operation failed: {result.stderr[-1000:]}")
        return result.stdout

    def inspect(self, image):
        validate_image(image)
        data = json.loads(self._run("image", "inspect", "--format", "{{json .}}", image))
        if data.get("Os") != "linux":
            raise ValueError("executor policy requires a Linux container image")
        image_id = data.get("Id", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Docker returned an invalid image ID")
        return {"id": image_id, "os": data["Os"], "digests": data.get("RepoDigests", [])}

    def prepare(self, image):
        validate_image(image)
        if not OFFICIAL_IMAGE.fullmatch(image):
            raise ValueError("prepare requires an official Python base image")
        # Pulling is intentionally confined to this explicit operator command.
        self._run("image", "pull", image, timeout=300)
        metadata = self.inspect(image)
        alias = f"researchpilot-executor:{self.owner}-{metadata['id'][7:23]}"
        self._run("image", "tag", metadata["id"], alias)
        self.root.mkdir(parents=True, exist_ok=True)
        receipt = {"alias": alias, "base": image, "owner": self.owner, **metadata}
        target = self.root / f"{alias.split(':')[1]}.json"
        target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt

    def remove(self, alias):
        if not MANAGED_IMAGE.fullmatch(alias) or not alias.startswith(f"researchpilot-executor:{self.owner}-"):
            raise ValueError("can only remove an alias owned by this workspace")
        receipt_path = self.root / f"{alias.split(':')[1]}.json"
        if not receipt_path.is_file():
            raise ValueError("managed image receipt is missing")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("owner") != self.owner or receipt.get("alias") != alias or receipt.get("removed"):
            raise ValueError("invalid or retired image receipt")
        if self.inspect(alias)["id"] != receipt["id"]:
            raise ValueError("managed alias changed since preparation; refusing removal")
        self._run("image", "rm", "--no-prune", alias)
        receipt["removed"] = True
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return {"removed_alias": alias, "shared_images_pruned": False}
