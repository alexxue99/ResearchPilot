import unittest

from researchpilot.agent.planner import material_ambiguities, select_tools
from researchpilot.evaluation import BenchmarkRunner, citation_correctness, evaluate_investigation, experiment_quality, load_benchmark, numerical_groundedness, precision_recall
from researchpilot.models import Evidence, EvidenceKind, ExperimentDesign, ExperimentResult, ResearchState, Source


class PlanningEvaluationTests(unittest.TestCase):
    def test_tool_selection_is_conditional(self):
        self.assertEqual(select_tools("Plot convergence curves"), ["experiment_design", "python", "statistics", "plotting"])
        self.assertEqual(select_tools("Find literature about solvers"), ["literature_search", "rag"])

    def test_ambiguity_detection(self):
        self.assertTrue(material_ambiguities("Compare them."))

    def test_benchmark_contains_at_least_30_tasks(self):
        tasks = load_benchmark()
        self.assertGreaterEqual(len(tasks), 30)
        self.assertEqual(len({t.id for t in tasks}), len(tasks))

    def test_precision_recall(self):
        self.assertEqual(precision_recall({"a", "b"}, {"b", "c"}), (0.5, 0.5))

    def test_benchmark_runs(self):
        result = BenchmarkRunner().run(load_benchmark())
        self.assertEqual(result["tasks"], 32)
        self.assertGreater(result["tool_recall"], 0.8)

    def test_investigation_evaluators_check_recorded_values(self):
        state = ResearchState("q", "o")
        source = Source("doi:x", "Paper", doi="x", url="https://doi.org/x", verified=True)
        state.sources.append(source)
        state.evidence.append(Evidence("lit", EvidenceKind.LITERATURE, "supported", source_id=source.id,
                                       chunk_id="chunk", support="retrieved passage", support_score=0.8))
        design = ExperimentDesign("d", "study", "h", ["x"], ["y"], ["size"], ["base"], ["error"], {}, list(range(5)), "lower", [])
        state.experiments_planned.append(design)
        result = ExperimentResult("run", "d", "completed", {"seeds": list(range(5))}, {"error": {"mean": 2.5}}, artifacts=["manifest"], software_versions={"python": "3"})
        state.experiments_completed.append(result)
        state.evidence.append(Evidence("obs", EvidenceKind.OBSERVATION, "Mean error was 2.5 across 5 seeds.", experiment_id="run"))
        self.assertEqual(numerical_groundedness(state)["score"], 1)
        self.assertEqual(citation_correctness(state)["score"], 1)
        self.assertEqual(experiment_quality(state)["score"], 1)
        self.assertEqual(evaluate_investigation(state)["aggregate_quality"], 1)

    def test_numerical_hallucination_is_detected(self):
        state = ResearchState("q", "o")
        state.experiments_completed.append(ExperimentResult("run", "d", "completed", {"seeds": list(range(5))}, {"mean": 2.5}))
        state.evidence.append(Evidence("obs", EvidenceKind.OBSERVATION, "Mean was 99.0.", experiment_id="run"))
        result = numerical_groundedness(state)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["checks"][0]["unsupported_values"], [99.0])
