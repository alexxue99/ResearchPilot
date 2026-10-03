import tempfile
import unittest
from pathlib import Path

from researchpilot.jobs import DurableJobQueue


class DurableJobQueueTests(unittest.TestCase):
    def test_recovery_retry_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            queue = DurableJobQueue(Path(tmp) / "jobs.sqlite", clock=lambda: now[0])
            queue.enqueue("research-1", max_attempts=2)
            job = queue.claim("worker-a", 5)
            self.assertIsNotNone(job)
            self.assertEqual(queue.recoverable(), [])
            now[0] = 106
            recovered = queue.recoverable()
            self.assertEqual(recovered[0].research_id, "research-1")
            job = queue.claim("worker-b", 5)
            self.assertFalse(queue.fail(job, "boom"))
            self.assertEqual(queue.get("research-1").status, "failed")
            queue.enqueue("research-1")
            replacement = queue.claim("worker-c", 5)
            queue.complete(replacement)
            self.assertEqual(queue.get("research-1").status, "completed")

    def test_cancel_only_queued_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DurableJobQueue(Path(tmp) / "jobs.sqlite")
            queue.enqueue("research-2")
            self.assertTrue(queue.cancel("research-2"))
            self.assertFalse(queue.cancel("research-2"))
