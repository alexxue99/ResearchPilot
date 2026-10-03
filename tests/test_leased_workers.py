import multiprocessing
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from researchpilot.jobs import DurableJobQueue, LeaseLost
from researchpilot.models import ResearchState, Status
from researchpilot.storage import ResearchRepository
from researchpilot.worker import JobWorker, research_agent_factory


def claim_in_process(path, barrier, result_queue):
    queue = DurableJobQueue(path)
    barrier.wait()
    job = queue.claim('process', 10)
    result_queue.put(job.lease_token if job else None)


class LeasedWorkerTests(unittest.TestCase):
    def test_two_processes_cannot_claim_the_same_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'jobs.sqlite')
            DurableJobQueue(path).enqueue('r')
            ctx = multiprocessing.get_context('spawn')
            barrier, results = ctx.Barrier(2), ctx.Queue()
            processes = [ctx.Process(target=claim_in_process, args=(path, barrier, results)) for _ in range(2)]
            try:
                for p in processes: p.start()
                outcomes = [results.get(timeout=20) for _ in processes]
                for p in processes: p.join(timeout=10)
                self.assertEqual(sum(value is not None for value in outcomes), 1)
                self.assertTrue(all(p.exitcode == 0 for p in processes))
            finally:
                for p in processes:
                    if p.is_alive(): p.terminate(); p.join()
                results.close()

    def test_expired_worker_cannot_save_finish_fail_or_renew(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite', clock=lambda: now[0])
            repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
            state = ResearchState('q', 'o'); repo.save(state)
            queue.enqueue(state.id)
            old = queue.claim('old', 5)
            now[0] += 6
            new = queue.claim('new', 5)
            state.conclusions = ['new result']; queue.checkpoint(repo, state, new)
            state.conclusions = ['stale result']
            for operation in (lambda: queue.checkpoint(repo, state, old), lambda: queue.complete(old),
                              lambda: queue.fail(old, 'bad'), lambda: queue.heartbeat(old)):
                with self.assertRaises(LeaseLost): operation()
            self.assertEqual(repo.get(state.id).conclusions, ['new result'])
            queue.complete(new)

    def test_cancelling_running_job_fences_worker_and_preserves_partial_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite')
            repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
            state = ResearchState('q', 'o', status=Status.RUNNING)
            state.record('investigation', 'running', 'Conjecture investigation started.')
            state.record('interpretation', 'completed', 'Finished interpretation.')
            repo.save(state)
            queue.enqueue(state.id)
            job = queue.claim('worker', 30)
            self.assertTrue(queue.cancel_active(repo, state.id))
            self.assertEqual(queue.get(state.id).status, 'cancelled')
            saved = repo.get(state.id)
            self.assertEqual(saved.status, Status.CANCELLED)
            self.assertEqual(saved.trace[-1].status, 'cancelled')
            self.assertEqual(saved.trace[-2].action, 'interpretation')
            self.assertEqual(saved.trace[0].status, 'cancelled')
            state.record('final_report', 'completed', 'Stale result.')
            for operation in (lambda: queue.checkpoint(repo, state, job),
                              lambda: queue.complete(job), lambda: queue.heartbeat(job)):
                with self.assertRaises(LeaseLost): operation()
            self.assertEqual(repo.get(state.id).trace[-1].status, 'cancelled')
            self.assertFalse(queue.cancel_active(repo, state.id))

    def test_heartbeat_prevents_live_recovery_and_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite', clock=lambda: now[0])
            queue.enqueue('r', max_attempts=2)
            first = queue.claim('a', 5)
            now[0] += 4; queue.heartbeat(first, 5)
            now[0] += 2
            self.assertIsNone(queue.claim('b', 5))
            self.assertTrue(queue.fail(first, 'temporary', retry_delay=2))
            self.assertIsNone(queue.claim('b', 5))
            now[0] += 3
            second = queue.claim('b', 5)
            now[0] += 6
            self.assertIsNone(queue.claim('c', 5))
            self.assertEqual(queue.get('r').status, 'failed')
            self.assertEqual(second.attempts, 2)

    def test_legacy_schema_migration_preserves_queued_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jobs.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY,research_id TEXT UNIQUE,status TEXT,attempts INTEGER,max_attempts INTEGER,error TEXT,created_at TEXT,updated_at TEXT)')
                db.execute("INSERT INTO jobs VALUES(1,'r','queued',0,2,NULL,'old','old')")
                db.commit()
            job = DurableJobQueue(path).claim('new-worker')
            self.assertEqual(job.research_id, 'r')
            self.assertEqual(job.attempts, 1)

    def test_concurrent_enqueue_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite')
            with ThreadPoolExecutor(max_workers=8) as pool:
                rows = list(pool.map(lambda _: queue.enqueue('r'), range(8)))
            self.assertEqual(sum(row is not None for row in rows), 1)

    def test_standalone_worker_executes_and_records_attempt_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            from researchpilot.agent import ResearchAgent
            repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
            state = ResearchAgent(repo, Path(tmp) / 'artifacts').propose('Measure PCA reconstruction error versus retained rank and plot it.')
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite'); queue.enqueue(state.id)
            worker = JobWorker(tmp, research_agent_factory(tmp))
            self.assertTrue(worker.run_once())
            self.assertEqual(queue.get(state.id).status, 'completed')
            saved = repo.get(state.id)
            self.assertEqual(saved.status, Status.COMPLETED)
            self.assertTrue(all('attempt-' in path for path in saved.artifacts))

    def test_idle_edit_cannot_overwrite_active_worker_or_newer_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite')
            state = ResearchState('q', 'o'); repo.save(state); before = state.updated_at
            state.record('edit', 'completed', 'new plan')
            queue.save_idle(repo, state, before)
            with self.assertRaises(LeaseLost): queue.save_idle(repo, state, before)
            before = state.updated_at; queue.enqueue(state.id)
            with self.assertRaises(LeaseLost): queue.save_idle(repo, state, before)

    def test_exhausted_expiry_updates_research_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            queue = DurableJobQueue(Path(tmp) / 'jobs.sqlite', clock=lambda: now[0])
            repo = ResearchRepository(Path(tmp) / 'researchpilot.db')
            state = ResearchState('q', 'o', status=Status.RUNNING); repo.save(state)
            queue.enqueue(state.id, max_attempts=1); queue.claim('worker', 5)
            now[0] += 6; queue.reconcile_expired(repo)
            self.assertEqual(repo.get(state.id).status, Status.FAILED)
            self.assertEqual(queue.get(state.id).status, 'failed')
