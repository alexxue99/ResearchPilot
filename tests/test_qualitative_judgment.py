import json
import tempfile
import unittest
from pathlib import Path

from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.conjecture_pipeline import ConjecturePipeline
from researchpilot.deployment import DeploymentConfig
from researchpilot.models import Conjecture, ConjectureAssessment, ResearchState, Status
from researchpilot.reporting import render_conjecture_report
from researchpilot.storage import ResearchRepository


class QualitativeJudgmentTests(unittest.TestCase):
    def assess(self, response):
        class Provider:
            name = "qualitative-fixture"

            def generate(self, messages, schema):
                self.schema = schema
                self.payload = json.loads(messages[1]["content"])
                return response

        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            provider = Provider()
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider,
                                  config=DeploymentConfig(mode="Full"))
            state = ResearchState(objective="Investigate the conjecture", question="A peak occurs in noisy Fourier regression.",
                                  conjecture=Conjecture(original_statement="A peak occurs in noisy Fourier regression.",
                                                        normalized_statement="A peak occurs in noisy Fourier regression."),
                                  assessment=ConjectureAssessment(), confidence=0.52,
                                  confidence_method="llm_prediction",
                                  confidence_rationale="Old numeric assessment.")
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            pipeline.assess()
            state.status = Status.COMPLETED
            state.report = render_conjecture_report(state)
            repo.save(state)
            return repo.get(state.id), provider

    def test_free_wording_survives_persistence_and_report(self):
        judgment = "The peak is well supported here; extrapolation to other kernels remains open"
        rationale = "All five replicates support the effect. Only one bandwidth was tested."
        state, provider = self.assess({"judgment": judgment, "rationale": rationale})
        self.assertEqual(state.conjecture_judgment, judgment)
        self.assertEqual(state.judgment_rationale, rationale)
        self.assertEqual(state.judgment_method, "llm_judgment")
        self.assertIn(judgment, state.report)
        self.assertIn(rationale, state.report)
        self.assertNotIn("Old numeric assessment", state.report)
        self.assertNotIn("probability_true", provider.schema["properties"])
        self.assertNotIn("enum", provider.schema["properties"]["judgment"])

    def test_invalid_and_legacy_responses_do_not_create_numeric_verdict(self):
        for response in ({"probability_true": 0.9, "rationale": "Old contract"},
                         {"judgment": "   ", "rationale": "Empty judgment"},
                         {"judgment": "Supported", "rationale": ""}):
            with self.subTest(response=response):
                state, _ = self.assess(response)
                self.assertEqual(state.judgment_method, "unavailable")
                self.assertEqual(state.conjecture_judgment, "Qualitative judgment unavailable")
                self.assertEqual(state.confidence, 0.0)
                self.assertNotIn("very likely", state.report.lower())
                self.assertNotIn("Old numeric assessment", state.report)
