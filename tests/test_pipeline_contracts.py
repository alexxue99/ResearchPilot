import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchpilot import prompts
from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.conjecture_pipeline import ConjecturePipeline, STAGES, structured_output_error, experiment_parameters
from researchpilot.deployment import DeploymentConfig
from researchpilot.executor import PythonExecutor
from researchpilot.models import Conjecture, ExperimentDesign, ExperimentResult, ExperimentalEvidence, ResearchState, Source
from researchpilot.pricing import ModelPrice
from researchpilot.storage import ResearchRepository


class PipelineContractTests(unittest.TestCase):
    def test_schema_drift_cannot_leave_nested_required_fields_unexplained(self):
        schema = {"type": "object", "required": ["reviews"], "properties": {
            "reviews": {"type": "array", "items": {"type": "object",
                "required": ["source_id", "new_review_field"], "properties": {
                    "source_id": {"type": "string"}, "new_review_field": {"type": "string"}}}}}}
        with self.assertRaisesRegex(ValueError, r"reviews\[\]\.new_review_field"):
            prompts.output_instructions("evidence_extraction", schema)
        schema["properties"]["reviews"]["items"]["required"] = ["source_id"]
        self.assertIn('- "reviews[].source_id": String:', prompts.output_instructions("evidence_extraction", schema))
        with patch.dict(prompts.FIELD_INSTRUCTIONS["evidence_extraction"], {"reviews[].source_id": ""}):
            with self.assertRaisesRegex(ValueError, r"reviews\[\]\.source_id"):
                prompts.output_instructions("evidence_extraction", schema)

    def test_default_demo_connects_all_nine_stages_and_all_ten_model_calls(self):
        statement = "A finite treatment lowers the measured error."
        passage = "The treatment reduced the measured error in this finite comparison."
        url = "https://doi.org/10.1234/treatment"

        class Provider:
            name = "contract-fixture"
            supports_web_search = True
            price, price_known, max_output_tokens = ModelPrice(0, 0), True, 2000
            web_search_cost_per_call = 0.01
            last_usage, last_cost_usd, last_web_search_calls = {}, 0.0, 0

            def __init__(self):
                self.calls = []

            def generate_with_search(self, messages, schema, **options):
                self.assert_search_limit = options["max_tool_calls"]
                result = self.generate(messages, schema)
                self.last_web_search_calls = 1
                self.last_cost_usd = 0.01
                result.update(_web_search_sources=[{"url": url, "title": "Treatment error theorem"}],
                              _web_search_calls=1)
                return result

            def generate(self, messages, schema):
                self.last_cost_usd, self.last_web_search_calls = 0.0, 0
                fields = schema["properties"]
                payload = json.loads(messages[1]["content"])
                self.calls.append((fields, payload))
                # Check exact paths, including nested fields whose names overlap.
                def required_paths(node, prefix=""):
                    for field in node.get("required", []):
                        path = prefix + field
                        yield path
                        child = node["properties"][field]
                        if child.get("type") == "object":
                            yield from required_paths(child, path + ".")
                        elif child.get("type") == "array":
                            yield from required_paths(child.get("items", {}), path + "[].")
                for path in required_paths(schema):
                    if f'- "{path}": ' not in messages[0]["content"]:
                        raise AssertionError(f"Missing field definition for {path}")
                if "normalized_statement" in fields:
                    return dict(normalized_statement=statement, mathematical_domain="numerical analysis",
                                objects=["error"], assumptions=["Matched inputs"],
                                measurable_predictions=["Lower treatment error"], ambiguities=[], experimentable=True)
                if "argument_searches" in fields:
                    return {"argument_searches": [{"relation": "supports", "observation": "A finite decrease is plausible.",
                            "query": "", "source_ids": [], "source_urls": [url]}]}
                if "queries" in fields:
                    return {"queries": ["treatment error theorem", "treatment error assumptions", "treatment error counterexample"]}
                if "selected_source_ids" in fields:
                    return {"selected_source_ids": [item["source_id"] for item in payload["candidates"]]}
                if "findings" in fields:
                    return {"reviews": [{"source_id": item["source_id"], "relevance": "high",
                            "summary": "A finite result is reported.", "limitations": ["Finite comparison"]}
                            for item in payload["sources"]],
                            "findings": [{"source_id": item["source_id"], "excerpt": passage,
                            "evidence_type": "empirical_result", "relation": "supports",
                            "assumptions": ["Matched inputs"], "notes": "Abstract only."} for item in payload["sources"]]}
                if "test_type" in fields:
                    return {
                        'name': 'Finite error comparison',
                        'baselines': ['control'],
                        'metrics': ['error'],
                        'seeds': [0, 1],
                        'algorithm_steps': ['Compute paired control and treatment errors.'],
                        'parameter_ranges_json': '{"n": 2, "stopping_rule": "two pairs"}',
                        'test_type': 'falsifying',
                        'additional_assumptions': ['Matched inputs'],
                    }
                if "code" in fields:
                    return {"code": "import json\nfrom pathlib import Path\n"
                            "Path('result.json').write_text(json.dumps({'control': [3, 4], 'treatment': [1, 2], 'higher_supports': False}))\n",
                            "visualization_code": "import json\nfrom pathlib import Path\n"
                            "data = json.loads(Path('result.json').read_text())\n"
                            "Path('visualization.svg').write_text('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"60\">'"
                            "+ '<text x=\"1\" y=\"20\">' + str(data['treatment'][0]) + '</text></svg>')\n"}
                if "finding" in fields:
                    return dict(finding="Treatment error was lower.", uncertainty="Two pairs only", robustness="One regime",
                                relation="supports", confounders=[], evidence_paths=["/control", "/treatment"])
                if "summary" in fields:
                    return dict(summary="Literature and measured errors support the finite comparison.", revised_conjecture="",
                                unresolved_questions=["Generalize beyond these finite inputs."])
                return dict(judgment="Supported in the tested setting; broader generality unresolved", rationale="The recorded evidence supports these finite cases, with limited coverage.")

        primary = Source("doi:10.1234/treatment", "Treatment error theorem", abstract=passage, doi="10.1234/treatment", verified=True)
        additional = Source("doi:10.1234/extra", "Treatment error additional study", abstract=passage, doi="10.1234/extra", verified=True)
        with tempfile.TemporaryDirectory() as temp, \
             patch("researchpilot.literature.ArxivClient.search", return_value=[primary, additional]), \
             patch("researchpilot.literature.CrossrefClient.search", return_value=[primary, additional]), \
             patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
             patch("researchpilot.conjecture_pipeline.executor_from_env", side_effect=lambda root, timeout: PythonExecutor(root, timeout)):
            provider = Provider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider, config=DeploymentConfig(mode="Limited"))
            state = agent.run(agent.propose(statement))
            self.assertEqual([event.action for event in state.trace if event.action in STAGES], list(STAGES))
            self.assertEqual(state.agent_steps, 10)
            self.assertEqual(len(provider.calls), 10)
            self.assertEqual(len(state.experiments_completed), 1)
            self.assertEqual(len(state.experimental_evidence), 1)
            planning_fields, planning_payload = next((fields, payload) for fields, payload in provider.calls
                                                     if "test_type" in fields)
            self.assertEqual(set(planning_fields), {
                "name", "baselines", "metrics", "seeds", "algorithm_steps",
                "parameter_ranges_json", "additional_assumptions", "test_type"})
            self.assertNotIn("reviewed_papers", planning_payload)
            design = state.experiments_planned[0]
            self.assertEqual(design.assumptions, ["Matched inputs"])
            self.assertEqual(design.additional_assumptions, ["Matched inputs"])
            code_payload = next(payload for fields, payload in provider.calls if "code" in fields)
            self.assertEqual(code_payload["max_generated_code_chars"], agent.config.max_generated_code_chars)
            self.assertEqual(code_payload["max_experiment_seconds"], agent.config.max_experiment_seconds)
            self.assertEqual(code_payload["max_result_json_bytes"], 100000)
            self.assertEqual(code_payload["design"]["inherited_assumptions"], ["Matched inputs"])
            self.assertEqual(design.planning_schema_version, 3)
            for key in ("purpose", "decision_criteria", "limitations"):
                self.assertNotIn(key, code_payload["design"])
                self.assertNotIn(key, design.model_context())
            synthesis_payload = next(payload for fields, payload in provider.calls if "summary" in fields)
            self.assertEqual(synthesis_payload["experiment_results"][0]["metrics"],
                             state.experiments_completed[0].metrics)
            self.assertEqual(synthesis_payload["investigation_context"]["planned_experiments"][0]["algorithm_steps"],
                             design.algorithm_steps)
            execution_payload = next(payload for fields, payload in provider.calls if "finding" in fields)
            self.assertEqual(execution_payload["design"], design.model_context())
            self.assertNotIn("**Decision criteria:**", state.report)
            self.assertNotIn("**Purpose:**", state.report)
            self.assertNotIn("**Hypothesis:**", state.report)
            self.assertNotIn("**Independent variables:**", state.report)
            saved_design = repo.get(state.id).experiments_planned[0]
            self.assertEqual(saved_design.model_context(), design.model_context())
            self.assertEqual(saved_design.additional_assumptions, design.additional_assumptions)
            self.assertEqual(len(state.structured_evidence), 2)
            self.assertEqual(state.sources[0].abstract, passage)  # Scholarly search enriched the web record.
            self.assertEqual(provider.assert_search_limit, 2)
            self.assertIsNone(state.stop_reason)
            self.assertEqual(state.judgment_method, "llm_judgment")
            confidence_payload = provider.calls[-1][1]
            for payload in (provider.calls[-2][1], confidence_payload):
                run = state.experiments_completed[0]
                self.assertEqual(payload["experiment_labels"][run.id], "Experiment 1: Finite error comparison")
                self.assertEqual(payload["source_references"][0]["title"], state.sources[0].title)
            self.assertEqual(confidence_payload["unresolved_questions"], state.unresolved_questions)
            self.assertEqual(confidence_payload["investigation_context"]["unresolved_questions"], state.unresolved_questions)
            self.assertIn("Generalize beyond these finite inputs.", state.report)
            self.assertEqual(repo.get(state.id).assessment.synthesis_summary, state.assessment.synthesis_summary)

    def test_invalid_nested_provider_fields_are_rejected_without_coercion_or_crashes(self):
        class Provider:
            name = "invalid-output-fixture"
            def generate(self, messages, schema):
                return self.result

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"), Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState("Claim", "Claim")
            pipeline.state.conjecture = Conjecture("Claim", "Claim")
            for result in ({"argument_searches": [{"relation": "supports", "observation": "Lead", "query": "",
                            "source_ids": [{}], "source_urls": []}]},
                           {"argument_searches": [{"relation": "supports", "observation": "Lead", "query": ""}]}):
                with self.subTest(result=result):
                    provider.result = result
                    pipeline.reason_generally()
                    self.assertEqual(pipeline.state.general_reasoning, [])
            self.assertEqual(len(pipeline.state.failed_attempts), 2)

    def test_unreadable_selected_sources_remain_visible_as_review_limitations(self):
        class Provider:
            name = "missing-text-fixture"
            def generate(self, messages, schema):
                self.payload = json.loads(messages[1]["content"])
                return {"reviews": [], "findings": []}

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"), Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(agent)
            state = ResearchState("Claim", "Claim")
            state.conjecture = Conjecture("Claim", "Claim")
            state.sources = [Source("web:theorem", "Original theorem", url="https://example.org/theorem")]
            pipeline.state = state
            pipeline.extract_evidence()
            self.assertEqual(provider.payload["sources"][0]["passages"], [])
            self.assertEqual(state.literature_reviews[0]["relevance"], "unknown")
            self.assertEqual(pipeline._reviewed_paper_context()[0]["text_scope"], "metadata_only")
            self.assertTrue(any("no passage or abstract" in question for question in state.unresolved_questions))
            self.assertEqual(state.structured_evidence, [])

    def test_valid_empty_source_selection_keeps_only_automatic_sources(self):
        class Provider:
            name = "empty-selection-fixture"
            def generate(self, messages, schema):
                return {"selected_source_ids": []}

        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"), Path(temp) / "artifacts", Provider())
            pipeline = ConjecturePipeline(agent)
            state = ResearchState("Claim", "Claim")
            state.conjecture = Conjecture("Claim", "Claim")
            state.sources = [Source("attached", "Attached paper"), Source("extra", "Irrelevant candidate")]
            state.context_source_ids, state.additional_literature_source_ids = ["attached"], ["extra"]
            pipeline.state = state
            pipeline.select_sources()
            self.assertEqual(state.selected_source_ids, ["attached"])

    def test_contracts_reject_nonfinite_numbers_and_boolean_probabilities(self):
        schema = {"type": "number", "minimum": 0, "maximum": 1}
        for value in (True, float("nan"), float("inf"), -1, 2, 10 ** 1000):
            self.assertIsNotNone(structured_output_error(value, schema))
        self.assertIsNone(structured_output_error(0.5, schema))
        self.assertEqual(set(STAGES) - set(prompts.OUTPUT_FIELDS), {"final_report"})
        self.assertIsNone(experiment_parameters('{"tolerance": NaN}'))
        self.assertIsNone(experiment_parameters('{"stopping_cap": Infinity}'))
        self.assertEqual(experiment_parameters('{"n": 2}'), {"n": 2})

    def test_declined_experiment_is_recorded_without_planner_assessments(self):
        class Provider:
            name = "declined-experiment-fixture"
            def generate(self, messages, schema):
                if "test_type" in schema["properties"]:
                    return {
                        'name': 'No finite test',
                        'baselines': [],
                        'metrics': [],
                        'seeds': [],
                        'algorithm_steps': [],
                        'parameter_ranges_json': '{}',
                        'test_type': 'not_meaningful',
                        'additional_assumptions': [],
                    }
                self.payload = json.loads(messages[1]["content"])
                return dict(summary="No experiment could be run.", revised_conjecture="", unresolved_questions=[])

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"), Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(agent)
            state = ResearchState("Claim", "Claim")
            state.conjecture = Conjecture("Claim", "Claim")
            pipeline.state = state
            pipeline.plan_experiments()
            pipeline.synthesize()
            self.assertEqual(state.experiments_planned, [])
            self.assertIn("The planner identified no meaningful finite experiment for this claim.",
                          provider.payload["unresolved_questions"])
            self.assertIn("The planner identified no meaningful finite experiment for this claim.",
                          state.assessment.unresolved_questions)

    def test_synthesis_receives_methods_and_measurements_without_saved_planner_assessments(self):
        class Provider:
            name = "independent-synthesis-fixture"
            def generate(self, messages, schema):
                self.messages = messages
                self.payload = json.loads(messages[1]["content"])
                return dict(summary="Measured error is lower, with only two paired trials.",
                            revised_conjecture="", unresolved_questions=["More trials are needed."])

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"), Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(agent)
            state = ResearchState("Treatment lowers error", "Treatment lowers error")
            state.conjecture = Conjecture(state.question, state.question)
            design = ExperimentDesign("design", "Paired errors", "legacy hypothesis", [], [], [],
                ["control"], ["mean error"], {"n": 2}, [0, 1], "legacy expected outcome", [],
                purpose="PLANNER_PURPOSE", decision_criteria="PLANNER_CRITERIA",
                limitations=["PLANNER_LIMITATION"], algorithm_steps=["Measure paired errors"],
                assumptions=["Matched inputs"])
            state.experiments_planned = [design]
            state.experiments_completed = [ExperimentResult("run", design.id, "completed", {"n": 2},
                {"control": [3, 4], "treatment": [1, 2]})]
            state.experimental_evidence = [ExperimentalEvidence("run", "Treatment error is lower", "supports",
                limitations=["PLANNER_LIMITATION"])]
            pipeline.state = state
            pipeline.synthesize()
            for marker in ("PLANNER_PURPOSE", "PLANNER_CRITERIA", "PLANNER_LIMITATION",
                           "legacy hypothesis", "legacy expected outcome"):
                self.assertNotIn(marker, json.dumps(provider.payload))
            methods = provider.payload["investigation_context"]["planned_experiments"][0]
            self.assertEqual(methods["algorithm_steps"], ["Measure paired errors"])
            self.assertEqual(methods["assumptions"], ["Matched inputs"])
            self.assertEqual(provider.payload["experiment_results"][0]["metrics"]["control"], [3, 4])
            self.assertIn("Independently reason", provider.messages[0]["content"])
            self.assertIn("More trials are needed.", state.assessment.unresolved_questions)

    def test_doi_duplicates_with_different_titles_keep_reasoning_links_and_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"), Path(temp) / "artifacts")
            pipeline = ConjecturePipeline(agent)
            state = ResearchState("Treatment error", "Treatment error")
            state.conjecture = Conjecture(state.question, state.question)
            state.sources = [Source("web:old", "Publisher landing page", doi="10.1234/test"),
                             Source("doi:10.1234/test", "Actual paper title", doi="10.1234/test",
                                    abstract="Treatment reduced error.", verified=True)]
            state.reasoning_source_ids, state.additional_literature_source_ids = ["web:old"], ["doi:10.1234/test"]
            state.general_reasoning = [{"relation": "supports", "observation": "Error can decrease.",
                                        "query": "", "source_ids": ["web:old"]}]
            pipeline.state = state
            pipeline._stage("ai_general_reasoning", "Recorded a lead.", observations=state.general_reasoning)
            pipeline._deduplicate_literature()
            self.assertEqual(len(state.sources), 1)
            self.assertEqual(state.reasoning_source_ids, ["doi:10.1234/test"])
            self.assertEqual(state.general_reasoning[0]["source_ids"], ["doi:10.1234/test"])
            self.assertEqual(state.source_aliases, {"web:old": "doi:10.1234/test"})
            self.assertEqual(state.trace[-1].outputs["observations"][0]["source_ids"], ["web:old"])
            agent.repository.save(state)
            self.assertEqual(agent.repository.get(state.id).source_aliases, state.source_aliases)


if __name__ == "__main__":
    unittest.main()
