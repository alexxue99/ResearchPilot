"""Load local server configuration without overriding explicit process settings."""
from __future__ import annotations

from pathlib import Path


def load_workspace_env(workspace: str | Path) -> Path | None:
    root = Path(workspace).resolve()
    candidates = [root / ".env.local"]
    if root.name == ".researchpilot":
        candidates.append(root.parent / ".env.local")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        return None
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("Install ResearchPilot with the 'api' extra to load .env.local") from exc
    load_dotenv(path, override=False)
    return path
