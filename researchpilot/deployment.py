"""Deployment policy and bounded research budgets."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentConfig:
    mode: str = "Full"
    max_papers: int = 5
    max_paper_bytes: int = 10_000_000
    max_paper_pages: int = 40
    max_steps: int = 10
    max_tools: int = 6
    max_experiments: int = 1
    max_experiment_seconds: int = 60
    max_generated_code_chars: int = 20000
    max_repetitions: int = 5
    max_output_tokens: int = 2500
    max_run_tokens: int = 50000
    max_cost_usd: float = 0.05
    runs_per_day: int = 2
    cache_ttl_seconds: int = 86400
    cache_version: str = "v1"
    routine_model: str = ""
    synthesis_model: str = ""

    def __post_init__(self):
        mode = self.mode.strip().capitalize()
        if mode not in ("Full", "Limited", "Restricted"):
            raise ValueError("RESEARCHPILOT_MODE must be Full, Limited, or Restricted")
        object.__setattr__(self, "mode", mode)

    @property
    def bounded(self) -> bool:
        return self.mode != "Full"

    @property
    def experiments_allowed(self) -> bool:
        return self.mode != "Restricted"

    @property
    def experiment_timeout_seconds(self) -> int:
        return min(self.max_experiment_seconds, 300) if self.bounded else 300

    @property
    def experiment_seed_limit(self) -> int:
        return min(self.max_repetitions, 5) if self.bounded else 5

    @property
    def model_timeout_seconds(self) -> int:
        return 300 if self.mode == "Full" else 60

    @property
    def planning_timeout_seconds(self) -> int:
        return 600 if self.mode == "Full" else 120

    @classmethod
    def from_env(cls) -> "DeploymentConfig":
        mode = os.getenv("RESEARCHPILOT_MODE", "Full").strip().capitalize()
        if mode not in ("Full", "Limited", "Restricted"):
            raise ValueError("RESEARCHPILOT_MODE must be Full, Limited, or Restricted")

        def setting(key, default):
            return os.getenv(f"RESEARCHPILOT_{mode.upper()}_{key}", str(default))

        strong = os.getenv("RESEARCHPILOT_STRONG_MODEL", "")
        weak = os.getenv("RESEARCHPILOT_WEAK_MODEL", setting("ROUTINE_MODEL", ""))
        routine = strong if mode == "Full" else weak
        synthesis = (weak if mode == "Restricted" else strong if mode == "Full"
                     else setting("SYNTHESIS_MODEL", strong))
        config = cls(mode=mode,
            max_papers=int(setting("MAX_PAPERS", 5)),
            max_paper_bytes=int(setting("MAX_PAPER_BYTES", 10000000)),
            max_paper_pages=int(setting("MAX_PAPER_PAGES", 40)),
            max_steps=int(setting("MAX_STEPS", 10)),
            max_tools=int(setting("MAX_TOOLS", 6)),
            max_experiments=int(setting("MAX_EXPERIMENTS", 1)),
            max_experiment_seconds=int(setting("MAX_EXPERIMENT_SECONDS", 60)),
            max_generated_code_chars=int(setting("MAX_CODE_CHARS", 20000)),
            max_repetitions=int(setting("MAX_REPETITIONS", 5)),
            max_output_tokens=int(setting("MAX_OUTPUT_TOKENS", 2500)),
            max_run_tokens=int(setting("MAX_RUN_TOKENS", 50000)),
            max_cost_usd=float(setting("MAX_COST_USD", 0.05)),
            runs_per_day=int(setting("RUNS_PER_DAY", 2)),
            cache_ttl_seconds=int(setting("CACHE_TTL_SECONDS", 86400)),
            cache_version=setting("CACHE_VERSION", "v1"),
            routine_model=routine, synthesis_model=synthesis)
        if config.bounded and (min(config.max_papers, config.max_paper_bytes, config.max_paper_pages,
                            config.max_steps, config.max_tools, config.max_experiments,
                            config.max_experiment_seconds, config.max_generated_code_chars, config.max_repetitions,
                            config.max_output_tokens, config.max_run_tokens,
                            config.runs_per_day, config.cache_ttl_seconds) < 1
                        or config.max_cost_usd <= 0):
            raise ValueError("deployment limits must be positive")
        return config


class DailyQuota:
    """Atomic SQLite quota keyed by a caller identifier; replace keying for accounts."""

    def __init__(self, path: str | Path, limit: int, *, legacy_path: str | Path | None = None):
        self.path, self.limit = Path(path), limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve existing daily usage when upgrading the quota storage name.
        if legacy_path is not None and not self.path.exists() and Path(legacy_path).is_file():
            with closing(sqlite3.connect(Path(legacy_path).resolve().as_uri() + "?mode=ro", uri=True)) as old_db, \
                 closing(sqlite3.connect(self.path)) as new_db:
                old_db.backup(new_db)
        with closing(sqlite3.connect(self.path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "demo_quota" in tables and "research_quota" not in tables:
                db.execute("ALTER TABLE demo_quota RENAME TO research_quota")
            db.execute("CREATE TABLE IF NOT EXISTS research_quota (subject TEXT, day INTEGER, used INTEGER NOT NULL, PRIMARY KEY(subject, day))")
            db.commit()

    def take(self, subject: str) -> bool:
        day = int(time.time() // 86400)
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT used FROM research_quota WHERE subject=? AND day=?", (subject, day)).fetchone()
            if used and used[0] >= self.limit:
                db.rollback()
                return False
            db.execute("INSERT INTO research_quota VALUES (?,?,1) ON CONFLICT(subject,day) DO UPDATE SET used=used+1", (subject, day))
            db.commit()
        return True

    def remaining(self, subject: str) -> int:
        day = int(time.time() // 86400)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT used FROM research_quota WHERE subject=? AND day=?", (subject, day)).fetchone()
        return max(0, self.limit - (row[0] if row else 0))

    def refund(self, subject: str) -> None:
        day = int(time.time() // 86400)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE research_quota SET used=max(0,used-1) WHERE subject=? AND day=?", (subject, day))
            db.commit()
