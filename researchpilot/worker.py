"""Leased worker shared by API threads and standalone local processes."""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from .jobs import DurableJobQueue, LeaseLost
from .models import Status
from .storage import ResearchRepository


class FencedRepository:
    def __init__(self, repository, queue, job):
        self.repository, self.queue, self.job = repository, queue, job
        self.path = repository.path

    def save(self, state):
        self.queue.checkpoint(self.repository, state, self.job)


class JobWorker:
    def __init__(self, workspace, agent_factory, *, lease_seconds=30, poll_seconds=.1):
        self.root = Path(workspace).resolve()
        self.queue = DurableJobQueue(self.root / 'jobs.sqlite')
        self.repository = ResearchRepository(self.root / 'researchpilot.db')
        self.agent_factory = agent_factory
        self.owner = f'worker-{uuid.uuid4().hex}'
        self.lease_seconds, self.poll_seconds = lease_seconds, poll_seconds
        self.stopped = threading.Event()

    def run_once(self, research_id=None):
        self.queue.reconcile_expired(self.repository)
        job = self.queue.claim(self.owner, self.lease_seconds, research_id)
        if job is None: return False
        finished = threading.Event()

        def heartbeat():
            while not finished.wait(self.lease_seconds / 3):
                try: self.queue.heartbeat(job, self.lease_seconds)
                except LeaseLost: return
                except Exception: logging.exception('Worker heartbeat failed')

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        state = None
        agent = None
        try:
            state = self.repository.get(job.research_id)
            if state is None: raise ValueError('research task disappeared')
            if state.status != Status.COMPLETED:
                fenced = FencedRepository(self.repository, self.queue, job)
                agent = self.agent_factory(fenced, job)
                agent.run(state)
            self.queue.complete(job)
        except LeaseLost:
            logging.warning('Discarded stale worker outcome for job %s', job.id)
        except Exception as exc:
            try:
                if state is not None:
                    state.status = Status.RUNNING if job.attempts < job.max_attempts else Status.FAILED
                    state.finish_investigation_trace('failed', 'Investigation attempt failed.')
                    state.failed_attempts.append(type(exc).__name__)
                    state.record('execution', 'failed', f'Execution failed ({type(exc).__name__}); retry policy applied.')
                    self.queue.checkpoint(self.repository, state, job)
                self.queue.fail(job, type(exc).__name__)
            except LeaseLost:
                logging.warning('Discarded stale worker failure for job %s', job.id)
        finally:
            finished.set(); thread.join(timeout=2)
            if agent is not None and getattr(agent, 'trace_exporter', None):
                agent.trace_exporter.close()
        return True

    def run_forever(self):
        while not self.stopped.is_set():
            try:
                if not self.run_once(): self.stopped.wait(self.poll_seconds)
            except Exception:
                logging.exception('Worker polling failed')
                self.stopped.wait(1)

    def stop(self): self.stopped.set()


def research_agent_factory(workspace):
    root = Path(workspace).resolve()
    def create(repository, job):
        from .agent import ResearchAgent
        from .agent.routing import providers_from_env
        from .deployment import DeploymentConfig
        from .environment import load_workspace_env
        from .rag_store import PersistentChunkStore
        from .observability import TraceExporter
        load_workspace_env(root)
        config = DeploymentConfig.from_env()
        provider, synthesis_provider = providers_from_env(config, root)
        return ResearchAgent(repository, root / 'artifacts' / f'attempt-{job.lease_token}', provider=provider,
            chunk_store=PersistentChunkStore(root / 'rag.sqlite'),
            trace_exporter=TraceExporter(root / 'traces' / f'attempt-{job.lease_token}'),
            config=config, synthesis_provider=synthesis_provider)
    return create
