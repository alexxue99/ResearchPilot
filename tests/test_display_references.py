import unittest

from researchpilot.display_references import experiment_labels, readable_references, reference_context
from researchpilot.models import ExperimentDesign, ExperimentResult, ResearchState, Source
from researchpilot.reporting import render_conjecture_report
from researchpilot.storage import state_from_dict


class DisplayReferenceTests(unittest.TestCase):
    def setUp(self):
        self.state = ResearchState("Compare spectra", "Assess convergence")
        self.state.sources = [Source("arxiv:2006.16978v2", "A supplied paper title",
                                     arxiv_id="2006.16978v2")]
        self.state.experiments_planned = [ExperimentDesign(
            "design_1", "Harmonic vs. flat spectrum convergence", "Slower convergence",
            [], [], [], [], [], {}, [], "Slower", [], code="print('experiment_1')")]
        self.state.experiments_completed = [ExperimentResult("experiment_1", "design_1", "completed", {}, {})]

    def test_model_context_maps_provenance_to_descriptive_labels(self):
        labels = experiment_labels(self.state)
        self.assertEqual(labels["experiment_1"], "Experiment 1: Harmonic vs. flat spectrum convergence")
        self.assertEqual(labels["design_1"], labels["experiment_1"])
        context = reference_context(self.state)
        self.assertEqual(context["source_references"][0]["title"], "A supplied paper title")
        self.assertEqual(context["experiment_labels"], labels)

    def test_prose_references_are_readable_and_link_targets_and_code_survive(self):
        text = ("experiment_1 supports arxiv:2006.16978v2. "
                "[arxiv:2006.16978v2](https://arxiv.org/abs/2006.16978v2). "
                "Unknown experiment_123. `experiment_1`\n```python\nprint('experiment_1')\n```")
        rendered = readable_references(text, self.state)
        self.assertIn("Experiment 1: Harmonic vs. flat spectrum convergence supports A supplied paper title", rendered)
        self.assertIn("[A supplied paper title](https://arxiv.org/abs/2006.16978v2)", rendered)
        self.assertIn("Unknown experiment_123", rendered)
        self.assertIn("`experiment_1`", rendered)
        self.assertIn("print('experiment_1')", rendered)

    def test_saved_assessments_and_reports_use_labels_without_changing_ids(self):
        self.state.confidence_rationale = "experiment_1 agrees with arxiv:2006.16978v2."
        self.state.report = render_conjecture_report(self.state)
        saved = state_from_dict(self.state.to_dict())
        self.assertIn("Experiment 1: Harmonic vs. flat spectrum convergence", saved.confidence_rationale)
        self.assertIn("A supplied paper title", saved.confidence_rationale)
        self.assertEqual(saved.experiments_completed[0].id, "experiment_1")
        self.assertEqual(saved.sources[0].id, "arxiv:2006.16978v2")
        self.assertIn("## Experiment 1: Harmonic vs. flat spectrum convergence", saved.report)

    def test_links_keep_their_destination_and_show_the_article_title(self):
        url = "https://arxiv.org/abs/2006.16978v2"
        expected = f"[A supplied paper title]({url})"
        self.assertEqual(readable_references(f"[Read paper]({url})", self.state), expected)
        self.assertEqual(readable_references(url, self.state), expected)
        self.assertEqual(readable_references(expected, self.state), expected)


if __name__ == "__main__":
    unittest.main()
