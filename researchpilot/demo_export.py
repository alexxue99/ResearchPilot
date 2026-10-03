"""Portable, explicit snapshots for curated demos; never copy the workspace wholesale."""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

from .models import ResearchState, Status, utc_now
from .latex_report import render_latex_pdf

MAX_ARTIFACT_BYTES = 100_000_000


def add_report_pdf(files: dict[str, bytes], question: str) -> bool:
    """Typeset the portable report; reuse a PDF already included in a snapshot."""
    if "report.pdf" in files:
        if not files["report.pdf"].startswith(b"%PDF-"):
            raise ValueError("The saved report.pdf is not a PDF document")
        return False
    files["report.pdf"] = render_latex_pdf(files["report.md"].decode("utf-8"), question)
    return True


def encode_demo(files: dict[str, bytes], metadata: dict) -> bytes:
    """Refresh the manifest after adding assets and serialize a portable ZIP."""
    manifest = {**metadata, "files": [
        {"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(files.items()) if name != "manifest.json"]}
    files["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    return buffer.getvalue()


def demo_bundle(state: ResearchState, workspace: str | Path) -> bytes:
    if state.status == Status.RUNNING or state.pending_action:
        raise ValueError("Wait for the investigation and any reruns to finish before exporting")
    root = Path(workspace).resolve()
    artifact_root = root / "artifacts"
    paths = dict.fromkeys([*state.artifacts,
        *(path for result in state.experiments_completed for path in result.artifacts)])
    files: dict[str, bytes] = {}
    replacements = {}
    total = 0
    for original in paths:
        target = Path(original).resolve()
        if not target.is_file() and not Path(original).is_absolute():
            target = (root / original).resolve()
        if not target.is_relative_to(artifact_root) or not target.is_file():
            raise ValueError("An investigation artifact is missing or outside the workspace artifact directory")
        total += target.stat().st_size
        if total > MAX_ARTIFACT_BYTES:
            raise ValueError("Demo artifacts exceed the 100 MB export limit")
        name = "artifacts/" + target.relative_to(artifact_root).as_posix()
        files[name] = target.read_bytes()
        replacements[original] = name
        replacements[str(target)] = name
        replacements[target.as_posix()] = name

    # Remove machine-specific workspace paths and configured credentials from text.
    replacements[str(root)] = "[workspace]"
    replacements[root.as_posix()] = "[workspace]"
    secrets = [value for key, value in os.environ.items() if value and len(value) >= 8
               and any(term in key.lower() for term in ("api_key", "api_token", "password", "secret"))]

    def portable(value):
        if isinstance(value, dict):
            return {key: "[REDACTED]" if any(term in key.lower() for term in
                    ("api_key", "api_token", "password", "secret", "authorization", "access_token", "refresh_token"))
                    else portable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, str):
            for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
                value = value.replace(old, new)
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
        return value

    def json_bytes(value):
        return (json.dumps(portable(value), indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    for name, content in list(files.items()):
        if name.endswith(".json"):
            files[name] = json_bytes(json.loads(content))
        elif name.endswith((".svg", ".txt", ".md", ".py", ".csv")):
            files[name] = portable(content.decode("utf-8")).encode("utf-8")
    snapshot = portable(state.to_dict())
    files["state.json"] = json_bytes(snapshot)
    files["report.md"] = portable(state.report or "\n".join(state.plan)).encode("utf-8")
    add_report_pdf(files, snapshot["question"])
    files["trace.json"] = json_bytes(snapshot["trace"])
    for index, design in enumerate(state.experiments_planned, 1):
        if design.code:
            files[f"experiments/{index:02d}/experiment.py"] = portable(design.code).encode("utf-8")
        if design.visualization_code:
            files[f"experiments/{index:02d}/visualization.py"] = portable(design.visualization_code).encode("utf-8")
    files["README.md"] = (
        "# Precomputed ResearchPilot demonstration\n\n"
        "This is a saved investigation, not a live run. See manifest.json for its status and dates.\n"
        "state.json contains sources, evidence, designs, measurements, model usage and assessment.\n"
        "Artifact paths are relative to this bundle. Scripts are under experiments/.\n"
        "report.pdf is the precomputed typeset report; report.md is its portable source.\n"
        "Unreferenced caches, databases and environment files are not included.\n"
        "Review the snapshot before publishing; research text may contain private information.\n"
        "Reproduction requires the dependencies and sandbox described in the repository README.\n"
    ).encode("utf-8")
    return encode_demo(files, portable({
        "schema_version": 1, "kind": "precomputed_demonstration", "research_id": state.id,
        "question": state.question, "status": str(state.status), "model": state.model,
        "created_at": state.created_at, "updated_at": state.updated_at, "exported_at": utc_now(),
    }))


def save_demo(state: ResearchState, workspace: str | Path, destination: str | Path) -> Path:
    target = Path(destination).resolve()
    if target.is_relative_to(Path(workspace).resolve()):
        raise ValueError("Save curated demos outside the runtime workspace so cleanup cannot erase them")
    if target.suffix.lower() != ".zip":
        raise ValueError("Demo destination must end in .zip")
    payload = demo_bundle(state, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents silently replacing a previously curated result.
    with target.open("xb") as handle:
        try:
            handle.write(payload)
        except BaseException:
            handle.close()
            target.unlink()
            raise
    return target
