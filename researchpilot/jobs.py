from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat()


class LeaseLost(RuntimeError):
    """A stale worker must stop publishing checkpoints and outcomes."""


@dataclass(slots=True)
class Job:
    id: int
    research_id: str
    status: str
    attempts: int
    max_attempts: int
    error: str | None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until: float | None = None
    available_at: float = 0


_COLUMNS = 'id,research_id,status,attempts,max_attempts,error,lease_owner,lease_token,lease_until,available_at'


class DurableJobQueue:
    """Leased queue for processes sharing a local filesystem, not network SQLite."""

    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = Path(path).resolve(); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, research_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 2, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            for name, definition in [('lease_owner', 'TEXT'), ('lease_token', 'TEXT'),
                    ('lease_until', 'REAL'), ('available_at', 'REAL NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
            db.execute('CREATE INDEX IF NOT EXISTS ix_jobs_ready ON jobs(status,available_at,id)')
            db.commit()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def enqueue(self, research_id: str, max_attempts: int = 2) -> Job | None:
        if not 1 <= max_attempts <= 20:
            raise ValueError('max_attempts must be between 1 and 20')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM jobs WHERE research_id=?', (research_id,)).fetchone()
            if row and row[0] in ('queued', 'running'):
                return None
            db.execute('''INSERT INTO jobs(research_id,status,max_attempts,created_at,updated_at)
                VALUES (?,'queued',?,?,?) ON CONFLICT(research_id) DO UPDATE SET
                status='queued',attempts=0,max_attempts=excluded.max_attempts,error=NULL,
                lease_owner=NULL,lease_token=NULL,lease_until=NULL,available_at=0,updated_at=excluded.updated_at''',
                (research_id, max_attempts, _now(), _now()))
            row = db.execute(f'SELECT {_COLUMNS} FROM jobs WHERE research_id=?', (research_id,)).fetchone()
            db.commit()
            return Job(*row)

    def _recover(self, db):
        db.execute('''UPDATE jobs SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
            error='Worker lease expired',lease_owner=NULL,lease_token=NULL,lease_until=NULL,
            updated_at=? WHERE status='running' AND (lease_until IS NULL OR lease_until<=?)''', (_now(), self.clock()))

    def recoverable(self):
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE'); self._recover(db)
            rows = db.execute(f"SELECT {_COLUMNS} FROM jobs WHERE status='queued' AND available_at<=? ORDER BY id", (self.clock(),)).fetchall()
            db.commit()
            return [Job(*row) for row in rows]

    def claim(self, owner: str, lease_seconds: float = 30, research_id: str | None = None) -> Job | None:
        if not owner or not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 3600:
            raise ValueError('owner and a lease duration between 1 and 3600 seconds are required')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE'); self._recover(db)
            sql = "SELECT id FROM jobs WHERE status='queued' AND available_at<=? AND attempts<max_attempts"
            params = [self.clock()]
            if research_id is not None:
                sql += ' AND research_id=?'; params.append(research_id)
            row = db.execute(sql + ' ORDER BY id LIMIT 1', params).fetchone()
            if row is None:
                db.commit(); return None
            db.execute("""UPDATE jobs SET status='running',attempts=attempts+1,lease_owner=?,lease_token=?,
                lease_until=?,updated_at=? WHERE id=?""", (owner, uuid.uuid4().hex, self.clock() + lease_seconds, _now(), row[0]))
            result = Job(*db.execute(f'SELECT {_COLUMNS} FROM jobs WHERE id=?', row).fetchone())
            db.commit(); return result

    def _owned(self, db, job: Job):
        if not job.lease_token or not db.execute("""SELECT 1 FROM jobs WHERE id=? AND research_id=?
                AND status='running' AND lease_token=? AND lease_until>?""",
                (job.id, job.research_id, job.lease_token, self.clock())).fetchone():
            raise LeaseLost('Worker lease is no longer current')

    def heartbeat(self, job: Job, lease_seconds=30):
        if not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 3600:
            raise ValueError('invalid lease duration')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE'); self._owned(db, job)
            db.execute('UPDATE jobs SET lease_until=?,updated_at=? WHERE id=?', (self.clock() + lease_seconds, _now(), job.id))
            db.commit()

    def complete(self, job: Job):
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE'); self._owned(db, job)
            db.execute("UPDATE jobs SET status='completed',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (_now(), job.id))
            db.commit()

    def fail(self, job: Job, error: str, *, retry_delay=1) -> bool:
        if not math.isfinite(retry_delay) or retry_delay < 0:
            raise ValueError('retry delay must be finite and nonnegative')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE'); self._owned(db, job)
            row = db.execute('SELECT attempts,max_attempts FROM jobs WHERE id=?', (job.id,)).fetchone()
            retry = row[0] < row[1]
            db.execute('''UPDATE jobs SET status=?,error=?,available_at=?,lease_token=NULL,lease_until=NULL,
                updated_at=? WHERE id=?''', ('queued' if retry else 'failed', error[:2000],
                self.clock() + min(60, retry_delay * 2 ** (row[0] - 1)), _now(), job.id))
            db.commit(); return retry

    def checkpoint(self, repository, state, job: Job):
        """Check ownership and write research state in one attached-database transaction."""
        if state.id != job.research_id:
            raise ValueError('checkpoint belongs to a different research task')
        with closing(self._connect()) as db:
            db.execute('ATTACH DATABASE ? AS state_store', (str(repository.path.resolve()),))
            db.execute('BEGIN IMMEDIATE'); self._owned(db, job)
            db.execute('''INSERT INTO state_store.research VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,updated_at=excluded.updated_at,state_json=excluded.state_json''',
                (state.id, str(state.status), state.updated_at, json.dumps(state.to_dict(), ensure_ascii=False)))
            db.commit()

    def save_idle(self, repository, state, expected_updated_at):
        """Reject edits racing an active job or a newer user edit."""
        with closing(self._connect()) as db:
            db.execute('ATTACH DATABASE ? AS state_store', (str(repository.path.resolve()),))
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT 1 FROM jobs WHERE research_id=? AND status IN ('queued','running')", (state.id,)).fetchone()
            row = db.execute('SELECT updated_at FROM state_store.research WHERE id=?', (state.id,)).fetchone()
            if active or not row or row[0] != expected_updated_at:
                raise LeaseLost('Research is executing or was updated; reload before editing')
            db.execute('UPDATE state_store.research SET status=?,updated_at=?,state_json=? WHERE id=?',
                (str(state.status), state.updated_at, json.dumps(state.to_dict(), ensure_ascii=False), state.id))
            db.commit()

    def reconcile_expired(self, repository):
        from .storage import state_from_dict
        from .models import Status
        with closing(self._connect()) as db:
            db.execute('ATTACH DATABASE ? AS state_store', (str(repository.path.resolve()),))
            db.execute('BEGIN IMMEDIATE'); self._recover(db)
            rows = db.execute("""SELECT r.state_json FROM state_store.research r JOIN jobs j ON j.research_id=r.id
                WHERE j.status='failed' AND j.error='Worker lease expired' AND r.status='running'""").fetchall()
            for row in rows:
                state = state_from_dict(json.loads(row[0]))
                state.status = Status.FAILED
                state.finish_investigation_trace('failed', 'Investigation attempt failed after the worker lease expired.')
                state.failed_attempts.append('Worker lease expired and retry budget was exhausted')
                state.record('execution', 'failed', 'Worker lease expired; retry budget exhausted.')
                db.execute('UPDATE state_store.research SET status=?,updated_at=?,state_json=? WHERE id=?',
                    (str(state.status), state.updated_at, json.dumps(state.to_dict()), state.id))
            db.commit()

    def cancel(self, research_id: str) -> bool:
        with closing(self._connect()) as db:
            cursor = db.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE research_id=? AND status='queued'", (_now(), research_id))
            db.commit(); return cursor.rowcount == 1

    def cancel_active(self, repository, research_id: str) -> bool:
        """Cancel a queued or running job and its research state atomically."""
        from .models import Status
        from .storage import state_from_dict

        with closing(self._connect()) as db:
            db.execute('ATTACH DATABASE ? AS state_store', (str(repository.path.resolve()),))
            db.execute('BEGIN IMMEDIATE')
            job = db.execute("SELECT status FROM jobs WHERE research_id=? AND status IN ('queued','running')",
                             (research_id,)).fetchone()
            row = db.execute('SELECT state_json FROM state_store.research WHERE id=?', (research_id,)).fetchone()
            if not job or not row:
                db.commit(); return False
            state = state_from_dict(json.loads(row[0]))
            if state.status in (Status.COMPLETED, Status.CANCELLED):
                db.commit(); return False
            state.status = Status.CANCELLED
            state.finish_investigation_trace('cancelled', 'Investigation cancelled by the researcher.')
            state.record('execution', 'cancelled', 'Investigation cancelled by the researcher.')
            db.execute("""UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_token=NULL,
                lease_until=NULL,updated_at=? WHERE research_id=?""", (_now(), research_id))
            db.execute('UPDATE state_store.research SET status=?,updated_at=?,state_json=? WHERE id=?',
                       (str(state.status), state.updated_at, json.dumps(state.to_dict(), ensure_ascii=False), research_id))
            db.commit(); return True

    def get(self, research_id: str) -> Job | None:
        with closing(self._connect()) as db:
            row = db.execute(f'SELECT {_COLUMNS} FROM jobs WHERE research_id=?', (research_id,)).fetchone()
        return Job(*row) if row else None
