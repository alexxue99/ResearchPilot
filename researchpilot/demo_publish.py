"""Validate a curated snapshot and prepare static gallery assets."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .storage import state_from_dict


def publish_demo(archive_path: str | Path, slug: str, title: str, summary: str,
                 public_dir: str | Path = "frontend/public/demos") -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 80:
        raise ValueError("Demo slug must contain lowercase letters, digits and single hyphens")
    if not title.strip() or not summary.strip():
        raise ValueError("Provide a title and summary for the gallery card")
    source = Path(archive_path).resolve()
    root = Path(public_dir).resolve()
    target = root / slug
    if target.exists():
        raise ValueError("This demo slug already exists; use a new slug for another version")
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > 110_000_000:
            raise ValueError("Archive contains duplicate files or exceeds 110 MB")
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            if (path.is_absolute() or '\\' in name or ':' in name or
                any(part in ('', '.', '..') for part in name.split('/')) or
                (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe archive path")
            if path.suffix.lower() not in ('.json', '.md', '.py', '.svg', '.png', '.jpg', '.jpeg', '.csv', '.txt', '.pdf'):
                raise ValueError("Unsupported public demo file type")
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema_version') != 1 or manifest.get('kind') != 'precomputed_demonstration':
            raise ValueError("Unsupported demo snapshot format")
        expected = {item['path']: item for item in manifest['files']}
        if len(expected) != len(manifest['files']) or set(names) != set(expected) | {'manifest.json'}:
            raise ValueError("Archive file list does not match the manifest")
        contents = {}
        for name, metadata in expected.items():
            content = archive.read(name)
            if len(content) != metadata['bytes'] or hashlib.sha256(content).hexdigest() != metadata['sha256']:
                raise ValueError("Archive file failed integrity verification")
            contents[name] = content
        state_data = json.loads(contents['state.json'])
        state = state_from_dict(state_data)
        if state.id != manifest['research_id'] or str(state.status) != manifest['status']:
            raise ValueError("Snapshot metadata does not match the investigation")
        if str(state.status) == 'running' or state.pending_action:
            raise ValueError("Publish a finished snapshot rather than an active investigation")
        artifact_paths = [*state.artifacts, *(path for result in state.experiments_completed for path in result.artifacts)]
        if any(not path.startswith('artifacts/') or path not in contents for path in artifact_paths):
            raise ValueError("Snapshot contains unavailable or nonportable artifact paths")
        contents['manifest.json'] = archive.read('manifest.json')
    root.mkdir(parents=True, exist_ok=True)
    catalog_path = root / 'catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8')) if catalog_path.exists() else []
    if not isinstance(catalog, list) or any(item['slug'] == slug for item in catalog):
        raise ValueError("Catalog is invalid or already contains this slug")
    card = {'slug': slug, 'title': title.strip(), 'summary': summary.strip(),
            'question': state.question, 'status': str(state.status), 'model': state.model,
            'created_at': state.created_at, 'exported_at': manifest['exported_at'],
            'experiments': sum(item.status == 'completed' for item in state.experiments_completed)}
    with tempfile.TemporaryDirectory(prefix='.demo-', dir=root) as temporary:
        staging = Path(temporary) / slug
        staging.mkdir()
        for name, content in contents.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        shutil.copyfile(source, staging / 'snapshot.zip')
        staging.rename(target)
        updated = Path(temporary) / 'catalog.json'
        updated.write_text(json.dumps([*catalog, card], indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        updated.replace(catalog_path)
    return target
