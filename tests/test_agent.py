import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchpilot.agent import ResearchAgent
from researchpilot.storage import ResearchRepository
from researchpilot.models import Status


class AgentWorkflowTests(unittest.TestCase):
    def test_stage_plan_is_persisted_without_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "db.sqlite")
            state = ResearchAgent(repo, Path(temp) / "artifacts").propose("Every prime is odd.")
            self.assertEqual(state.model_calls, 0)
            self.assertEqual(state.plan[0], "Interpretation")
            self.assertEqual(repo.get(state.id).plan[-1], "Final report")

    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    def test_unexperimentable_claim_has_explicit_uncertainty(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "db.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts")
            state = agent.run(agent.propose("Every prime is odd."))
            self.assertEqual(state.experiments_completed, [])
            self.assertTrue(state.unresolved_questions)
            self.assertIn("ResearchPilot assessment:", state.report)
            self.assertEqual(state.status, Status.COMPLETED)
            investigation = [event for event in repo.get(state.id).trace if event.action == "investigation"]
            self.assertEqual(len(investigation), 1)
            self.assertEqual(investigation[0].status, "completed")


if __name__ == "__main__":
    unittest.main()
