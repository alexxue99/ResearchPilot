import tempfile
import unittest
from pathlib import Path

from researchpilot.models import Evidence, EvidenceKind, ExperimentDesign, ResearchState, Status, new_id
from researchpilot.storage import ResearchRepository, state_from_dict


class StorageTests(unittest.TestCase):
    def test_legacy_experiment_snapshot_preserves_execution_and_report_context(self):
        from researchpilot.reporting import _experiment_details
        from researchpilot.experiments.code import label_experiment_script

        state = ResearchState("Claim", "Test claim", status=Status.PLANNED)
        state.experiments_planned = [ExperimentDesign(
            "design_old", "Legacy comparison", "Treatment reduces error", ["treatment"],
            ["error"], ["matched inputs"], ["control"], ["mean error"], {"n": 2}, [0, 1],
            "Lower error supports", ["sampling noise"], code="print('legacy')\n",
            expected_if_false="Higher error contradicts", rationale="Matched comparison",
            algorithm_steps=["Compare errors"], assumptions=["Independent pairs"])]
        snapshot = state.to_dict()
        for key in ("purpose", "decision_criteria", "additional_assumptions"):
            snapshot["experiments_planned"][0].pop(key)
        loaded = state_from_dict(snapshot)
        design = loaded.experiments_planned[0]
        self.assertEqual(design.purpose, "")
        self.assertEqual(design.model_context()["controls"], ["matched inputs"])
        self.assertNotIn("expected_if_false", design.model_context())
        self.assertNotIn("limitations", design.model_context())
        self.assertIn("Higher error contradicts", _experiment_details(loaded, False))
        self.assertIn("# HYPOTHESIS", label_experiment_script(design))
        self.assertTrue(label_experiment_script(design).endswith("print('legacy')\n"))

    def test_state_round_trip_preserves_nested_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = ResearchRepository(Path(tmp) / "db.sqlite")
            state = ResearchState("question", "objective", status=Status.PLANNED)
            state.evidence.append(Evidence(new_id("e"), EvidenceKind.HYPOTHESIS, "maybe"))
            state.record("plan", "completed", "planned")
            repo.save(state)
            loaded = repo.get(state.id)
            self.assertEqual(loaded.status, Status.PLANNED)
            self.assertEqual(loaded.evidence[0].kind, EvidenceKind.HYPOTHESIS)
            self.assertEqual(loaded.trace[0].sequence, 1)

    def test_missing_state_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(ResearchRepository(Path(tmp) / "db.sqlite").get("missing"))

    def test_completed_legacy_state_resolves_running_investigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = ResearchRepository(Path(tmp) / "db.sqlite")
            state = ResearchState("question", "objective", status=Status.COMPLETED)
            state.record("investigation", "running", "Conjecture investigation started.")
            repo.save(state)
            loaded = repo.get(state.id)
            self.assertEqual(loaded.trace[0].status, "completed")
            self.assertEqual(loaded.updated_at, state.updated_at)

