import tempfile
import unittest
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.conjecture_pipeline import ConjecturePipeline, STAGES, rank_sources
from researchpilot.models import (Conjecture, ConjectureAssessment, ConfidenceFactors,
                                  EvidenceItem, ExperimentDesign, ResearchState, Source, Status)
from researchpilot.storage import ResearchRepository
from researchpilot.rag import Chunk
from researchpilot.agent.provider import ModelLimitError, ProviderError
from researchpilot.deployment import DeploymentConfig
from researchpilot.executor import PythonExecutor
from researchpilot.pricing import ModelPrice
from researchpilot.experiments.visualization import visualization_svg_contract

CHART_CODE = ("import json, html\nfrom pathlib import Path\n"
              "result = json.loads(Path('result.json').read_text())\n"
              "value = result.get('error', result.get('control', [0])[0])\n"
              "Path('visualization.svg').write_text("
              "f'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"300\" height=\"100\">'"
              "f'<text x=\"10\" y=\"30\">Error: {html.escape(str(value))}</text></svg>')\n")


class ConjecturePipelineTests(unittest.TestCase):
    def test_planning_recovery_uses_only_interpretation_and_execution_limits(self):
        class Provider:
            name = "planning-failure-fixture"

            def __init__(self): self.payloads = []

            def generate(self, messages, schema):
                self.payloads.append(json.loads(messages[1]["content"]))
                raise ProviderError("Planning unavailable")

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(agent)
            state = ResearchState("Original claim", "Investigation objective")
            state.conjecture = Conjecture(state.question, "Normalized method comparison",
                objects=["Method update and sampling rule"], assumptions=["Matched data"],
                experimentable=True)
            state.sources = [Source("paper:extra", "Literature title", abstract="Literature summary")]
            state.general_reasoning = [{"observation": "Later reasoning"}]
            state.unresolved_questions = ["Later unresolved question"]
            pipeline.state = state
            with patch.object(pipeline, "_paper_context", return_value=[{"text": "Paper passage"}]), \
                 patch.object(pipeline, "_reviewed_paper_context") as reviewed:
                pipeline.plan_experiments()
            reviewed.assert_not_called()
            self.assertEqual(len(provider.payloads), 2)
            expected = {"normalized_statement": "Normalized method comparison",
                        "objects": ["Method update and sampling rule"],
                        "assumptions": ["Matched data"],
                        "max_repetitions": 5, "max_experiment_seconds": 300}
            self.assertEqual(provider.payloads[0], expected)
            self.assertEqual(provider.payloads[1], {**expected, "previous_design": {},
                "revision_reason": "No usable finite experiment was proposed."})

    def test_experiment_stages_use_longer_timeout_than_ordinary_calls(self):
        from researchpilot.agent.provider import OpenAIResponsesProvider
        import io

        response = b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}]}'
        with tempfile.TemporaryDirectory() as temp:
            provider = OpenAIResponsesProvider("secret", "test-model")
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState("claim", "claim")
            for stage, timeout in (("experiment_code", 300), ("experiment_visualization", 300),
                                   ("experiment_code_repair", 300), ("experiment_planning", 300), ("interpretation", 60)):
                with self.subTest(stage=stage), patch("urllib.request.urlopen",
                        return_value=io.BytesIO(response)) as request:
                    self.assertEqual(pipeline._generate(stage, "Generate", {}, {"type": "object"}), {})
                    self.assertEqual(request.call_args.kwargs["timeout"], timeout)

    def test_reasoning_model_can_discover_and_select_outside_sources_without_attachments(self):
        from researchpilot.agent.provider import OpenAIResponsesProvider
        from researchpilot.agent.cached import CachedProvider
        from researchpilot.reporting import render_conjecture_report
        import io

        url = "https://arxiv.org/abs/2401.12345"
        web_url = "https://example.org/prime-theorem"
        response = {"status": "completed", "output": [
            {"type": "web_search_call", "action": {"type": "search", "sources": [
                {"url": url, "title": "Prime parity theorem"},
                {"url": web_url, "title": "Original prime theorem statement"}]}},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "argument_searches": [{"relation": "contradicts", "observation": "Two is an even prime.",
                    "source_ids": [], "source_urls": [url, web_url, "https://invented.org/paper"], "query": ""}]})}]}]}
        with tempfile.TemporaryDirectory() as temp, \
             patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request, \
             patch("researchpilot.literature.ArxivClient.search") as arxiv:
            provider = CachedProvider(OpenAIResponsesProvider("secret", "test-model", input_cost_per_million=1),
                                      Path(temp) / "cache.sqlite", "v1", 3600)
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider, config=DeploymentConfig(mode="Limited"))
            state = ResearchState("Every prime is odd.", "Check parity")
            state.conjecture = Conjecture(state.question, state.question)
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            pipeline.reason_generally()
            sent = json.loads(request.call_args.args[0].data)
            self.assertEqual(sent["max_tool_calls"], 2)
            self.assertEqual(state.context_source_ids, [])
            self.assertEqual(len(state.reasoning_source_ids), 2)
            self.assertEqual(state.reasoning_source_ids[0], "arxiv:2401.12345")
            self.assertEqual(state.general_reasoning[0]["source_ids"], state.reasoning_source_ids)
            self.assertEqual({source.url for source in state.sources}, {"https://arxiv.org/pdf/2401.12345", web_url})
            self.assertEqual(state.tool_calls, 1)
            self.assertAlmostEqual(state.estimated_cost_usd, 0.01)
            arxiv.assert_not_called()
            pipeline.select_sources()
            self.assertEqual(state.selected_source_ids, state.reasoning_source_ids)
            report = render_conjecture_report(state)
            self.assertIn(f"]({web_url})", report)
            self.assertNotIn("invented.org", report)
            saved = agent.repository.get(state.id)
            self.assertEqual(saved.reasoning_source_ids, state.reasoning_source_ids)
            self.assertEqual(saved.selected_source_ids, state.selected_source_ids)

    def test_unavailable_docker_is_reported_without_code_repair(self):
        class Provider:
            name = "unused-fixture"

            def __init__(self): self.calls = 0

            def generate(self, messages, schema):
                self.calls += 1
                return {"code": "print('repair')"}

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider)
            question = "A numerical treatment reduces error."
            pipeline = ConjecturePipeline(agent)
            state = ResearchState(question, question)
            state.conjecture = Conjecture(question, question, experimentable=True)
            state.experiments_planned = [ExperimentDesign(
                "design-1", "Finite comparison", question, ["treatment"], ["error"],
                [], [], ["error"], {}, [0], "lower", [], "print('run')")]
            pipeline.state = state
            unavailable = SimpleNamespace(status="unavailable", stderr="Docker runtime unavailable",
                                          stdout="", runtime_seconds=0.0, artifacts=[])
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = unavailable
                pipeline.execute_experiments()
            self.assertEqual(provider.calls, 0)
            self.assertEqual(state.experiments_completed, [])
            self.assertTrue(any("Docker executor is unavailable" in item
                                for item in state.unresolved_questions))

    def test_limited_enforces_five_seed_cap_after_revision(self):
        class Provider:
            name = "repetition-limit-fixture"
            price = ModelPrice(0, 0)
            price_known = True
            max_output_tokens = 1000
            last_usage = {}
            last_cost_usd = 0.0

            def __init__(self): self.plan_calls = 0

            def generate(self, messages, schema):
                if "test_type" in schema["properties"]:
                    self.plan_calls += 1
                    return {
                        'name': 'Paired finite test',
                        'baselines': ['control'],
                        'metrics': ['error'],
                        'seeds': list(range(6 if self.plan_calls == 1 else 5)),
                        'algorithm_steps': ['Compare outcomes under paired seeds.'],
                        'parameter_ranges_json': '{}',
                        'test_type': 'falsifying',
                        'additional_assumptions': [],
                    }
                return {"code": "import json\nopen('result.json', 'w').write(json.dumps({'error': 1}))\n",
                        "visualization_code": CHART_CODE}

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            config = DeploymentConfig(mode="Limited", max_repetitions=20)
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider, config=config)
            question = "A finite treatment changes estimation error."
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState(question, question)
            pipeline.state.conjecture = Conjecture(question, question, experimentable=True)
            pipeline.plan_experiments()
            self.assertEqual(provider.plan_calls, 2)
            self.assertEqual(len(pipeline.state.experiments_planned), 1)
            self.assertEqual(len(pipeline.state.experiments_planned[0].seeds), 5)
            self.assertTrue(pipeline.state.experiments_planned[0].code)

    def test_invalid_generic_plan_is_revised(self):
        class Provider:
            name = "plan-revision-fixture"

            def __init__(self): self.calls = 0

            def generate(self, messages, schema):
                if "test_type" in schema["properties"]:
                    self.calls += 1
                    return {
                        'name': 'Finite comparison',
                        'baselines': ['control'],
                        'metrics': ['error'],
                        'seeds': [0],
                        'algorithm_steps': ['Compare error.'],
                        'parameter_ranges_json': 'invalid JSON' if self.calls == 1 else '{}',
                        'test_type': 'falsifying',
                        'additional_assumptions': [],
                    }
                return {"code": "import json\nopen('result.json', 'w').write(json.dumps({'error': 1}))\n",
                        "visualization_code": CHART_CODE}

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider)
            question = "A finite numerical treatment changes estimation error."
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState(question, question)
            pipeline.state.conjecture = Conjecture(question, question, experimentable=True)
            pipeline.plan_experiments()
            self.assertEqual(provider.calls, 2)
            self.assertEqual(len(pipeline.state.experiments_planned), 1)

    def test_experimentable_claim_repairs_failed_execution_once(self):
        class RepairProvider:
            name = "repair-fixture"

            def generate(self, messages, schema):
                if "code" in schema["properties"]:
                    return {"code": "import json\nwith open('result.json', 'w') as f:\n"
                            "    json.dump({'control': [2], 'treatment': [1], "
                            "'higher_supports': False}, f)\n"}
                return {"finding": "Treatment was lower", "uncertainty": "One trial",
                        "robustness": "Untested", "relation": "inconclusive", "confounders": [],
                        "evidence_paths": ["/control", "/treatment"]}

        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", RepairProvider())
            question = "A numerical treatment reduces estimation error."
            pipeline = ConjecturePipeline(agent)
            state = ResearchState(question, question)
            state.conjecture = Conjecture(question, question, experimentable=True)
            state.experiments_planned = [ExperimentDesign(
                "design-1", "Numerical comparison", question, ["treatment"], ["error"],
                [], ["control"], ["error"], {}, [0], "lower error", [],
                "raise RuntimeError('broken')", "no decrease", [], "falsifying",
                "Finite comparison", [], ["Compute and compare errors."])]
            pipeline.state = state
            path = Path(temp) / "result.json"
            path.write_text(json.dumps({"control": [2], "treatment": [1],
                                        "higher_supports": False}), encoding="utf-8")
            failed = SimpleNamespace(status="failed", stderr="runtime error", stdout="",
                                     runtime_seconds=0.1, artifacts=[])
            completed = SimpleNamespace(status="completed", stderr="", stdout="",
                                        runtime_seconds=0.1, artifacts=[str(path)])
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.side_effect = [failed, completed]
                pipeline.execute_experiments()
                self.assertEqual(factory.return_value.run.call_count, 2)
            self.assertEqual(len(state.experiments_completed), 1)

    def test_general_experimentable_claim_recovers_plan_code_and_runs(self):
        class RecoveringProvider:
            name = "recovering-fixture"

            def __init__(self):
                self.plans = 0
                self.codes = 0

            def generate(self, messages, schema):
                properties = schema["properties"]
                if "test_type" in properties:
                    self.plans += 1
                    return {
                        'name': 'Paired mean test',
                        'baselines': ['small sample'],
                        'metrics': ['absolute error'],
                        'seeds': [0, 1, 2],
                        'algorithm_steps': ['Draw paired samples and compare errors.'],
                        'parameter_ranges_json': '{}',
                        'test_type': 'not_meaningful' if self.plans == 1 else 'falsifying',
                        'additional_assumptions': [],
                    }
                if "code" in properties:
                    self.codes += 1
                    if self.codes == 1:
                        return {"code": "def broken(:", "visualization_code": CHART_CODE}
                    return {"code": "import json\nwith open('result.json', 'w') as f:\n"
                            "    json.dump({'control': [3, 4, 5], 'treatment': [1, 2, 3], "
                            "'higher_supports': False}, f)\n", "visualization_code": CHART_CODE}
                return {"finding": "Measured lower error", "uncertainty": "Three pairs",
                        "robustness": "One regime", "relation": "supports", "confounders": [],
                        "evidence_paths": ["/control", "/treatment"]}

        with tempfile.TemporaryDirectory() as temp:
            provider = RecoveringProvider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider)
            question = "Larger samples reduce estimation error in this finite simulation."
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState(question, question)
            pipeline.state.conjecture = Conjecture(question, question, experimentable=True)
            pipeline.plan_experiments()
            self.assertEqual((provider.plans, provider.codes), (2, 2))
            self.assertEqual(len(pipeline.state.experiments_planned), 1)
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env",
                       side_effect=lambda root, timeout: PythonExecutor(root, timeout)):
                pipeline.execute_experiments()
            self.assertEqual(len(pipeline.state.experiments_completed), 1)
            self.assertEqual(pipeline.state.experiments_completed[0].metrics["control"], [3, 4, 5])
            self.assertTrue(any(Path(path).name == "visualization.svg"
                                for path in pipeline.state.experiments_completed[0].artifacts))

    def test_failed_chart_keeps_measured_experiment(self):
        class Provider:
            name = "chart-failure-fixture"
            def generate(self, messages, schema): return {}

        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", Provider())
            question = "A numerical treatment changes error."
            pipeline = ConjecturePipeline(agent)
            state = ResearchState(question, question)
            state.conjecture = Conjecture(question, question, experimentable=True)
            state.experiments_planned = [ExperimentDesign(
                "design-1", "Finite comparison", question, ["treatment"], ["error"],
                [], [], ["error"], {}, [0], "different error", [], "pass",
                visualization_code=CHART_CODE)]
            pipeline.state = state
            result_path = Path(temp) / "result.json"
            result_path.write_text('{"error": 7}', encoding="utf-8")
            measured = SimpleNamespace(status="completed", stderr="", stdout="",
                                       runtime_seconds=0.1, artifacts=[str(result_path)])
            failed_chart = SimpleNamespace(status="failed", stderr="chart error", stdout="",
                                           runtime_seconds=0.1, artifacts=[])
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.side_effect = [measured, failed_chart]
                pipeline.execute_experiments()
            self.assertEqual(state.experiments_completed[0].metrics, {"error": 7})
            self.assertEqual(state.experiments_completed[0].status, "completed")
            self.assertTrue(any("visualization failed" in item for item in state.unresolved_questions))

    def test_review_without_finding_reaches_later_model_calls(self):
        class ReviewProvider:
            name = "review-fixture"
            def __init__(self): self.payloads = []
            def generate(self, messages, schema):
                payload = json.loads(messages[1]["content"])
                self.payloads.append(payload)
                if "findings" in schema.get("properties", {}):
                    return {"reviews": [{"source_id": "paper:attached", "relevance": "high",
                                         "summary": "Defines the named algorithm and its update.",
                                         "limitations": ["No comparison of spectra."]}],
                            "findings": []}
                return {}

        with tempfile.TemporaryDirectory() as temp:
            provider = ReviewProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            state = ResearchState("The named method is slower for harmonic spectra.",
                                  "The named method is slower for harmonic spectra.")
            state.conjecture = Conjecture(state.question, state.question, experimentable=True)
            source = Source("paper:attached", "Named algorithm paper")
            state.sources = [source]
            state.context_source_ids = [source.id]
            agent.chunk_store.add([Chunk("method-passage", source.id, source.title, "Algorithm",
                                         "Algorithm 1 defines the named method update.")])
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            pipeline.extract_evidence()
            self.assertEqual(state.structured_evidence, [])
            self.assertEqual(state.literature_reviews[0]["relevance"], "high")
            state.sources.extend(Source(f"paper:candidate-{index}", f"Candidate {index}",
                                        abstract=f"Search result abstract {index}.") for index in range(9))
            literature = Source("paper:reviewed", "Reviewed literature paper",
                                abstract="The searched paper defines a related update.")
            state.sources.append(literature)
            state.inspected_papers.append(literature.id)
            state.literature_reviews.append({"source_id": literature.id, "relevance": "high",
                                             "summary": "A related update was reviewed.", "limitations": []})
            agent.chunk_store.add([Chunk("searched-passage", literature.id, literature.title, "Method",
                                         "The searched paper defines a related update rule.")])
            agent._checkpoint(state)
            resumed_agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            pipeline = ConjecturePipeline(resumed_agent)
            pipeline.state = repo.get(state.id)
            for stage in ("experiment_planning", "experiment_code", "experiment_visualization",
                          "experiment_code_repair", "experiment_execution",
                          "evidence_synthesis", "confidence_estimation"):
                pipeline._generate(stage, "Use the supplied paper context.", {}, {"required": []})
                payload = provider.payloads[-1]
                if stage == "experiment_planning":
                    self.assertEqual(payload, {"normalized_statement": state.conjecture.normalized_statement,
                                              "objects": state.conjecture.objects,
                                              "assumptions": state.conjecture.assumptions})
                    continue
                if stage in ("experiment_code", "experiment_visualization", "experiment_code_repair"):
                    self.assertEqual(payload["svg_contract"], visualization_svg_contract())
                else:
                    self.assertNotIn("svg_contract", payload)
                self.assertIn("Algorithm 1 defines the named method update.",
                              payload["attached_paper_passages"][0]["text"])
                self.assertEqual(payload["reviewed_papers"][0]["review"]["summary"],
                                 "Defines the named algorithm and its update.")
                self.assertEqual(payload["reviewed_papers"][-1]["review"]["summary"],
                                 "A related update was reviewed.")
                self.assertIn("related update rule", payload["reviewed_papers"][-1]["passage"]["text"])
                self.assertEqual(len(payload["investigation_context"]["literature_sources"]), 11)
                self.assertIn(source.id, payload["investigation_context"]["attached_source_ids"])

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_limited_model_reviews_top_sources_and_preserves_context(self, *_):
        class LimitedProvider:
            name = "limited-fixture"
            price = ModelPrice(0, 0)
            price_known = True
            max_output_tokens = 4000
            last_usage = {}
            last_cost_usd = 0.0
            def __init__(self): self.calls = []
            def generate(self, messages, schema):
                payload = json.loads(messages[1]["content"])
                self.calls.append((schema["properties"], payload))
                properties = schema["properties"]
                if "normalized_statement" in properties:
                    return {"normalized_statement": payload["statement"], "mathematical_domain": "numerical",
                            "objects": ["named method"], "assumptions": [],
                            "measurable_predictions": ["Treatment takes more iterations"], "ambiguities": [],
                             "experimentable": False}
                if "argument_searches" in properties:
                    return {"argument_searches": []}
                if "queries" in properties:
                    return {"queries": ["named method convergence", "spectral comparison",
                                        "iteration counterexample"]}
                if "selected_source_ids" in properties:
                    return {"selected_source_ids": [item["source_id"] for item in reversed(payload["candidates"])]}
                if "findings" in properties:
                    return {"reviews": [{"source_id": item["source_id"], "relevance": "high",
                            "summary": "Relevant method or comparison.", "limitations": []}
                            for item in payload["sources"]],
                            "findings": [{"source_id": item["source_id"],
                            "excerpt": item["passages"][0]["text"][:70],
                            "evidence_type": "related_result", "relation": "neutral",
                            "assumptions": [], "notes": "Relevant context."} for item in payload["sources"]]}
                if "test_type" in properties:
                    return {
                        'name': 'Paired comparison',
                        'baselines': ['flat spectrum'],
                        'metrics': ['iterations'],
                        'seeds': [0, 1, 2],
                        'algorithm_steps': ['Compare paired spectra.'],
                        'parameter_ranges_json': '{}',
                        'test_type': 'falsifying',
                        'additional_assumptions': [],
                    }
                if "code" in properties:
                    return {"code": "print('planned')\n"}
                if "summary" in properties:
                    return {"summary": "Five sources were reviewed.", "revised_conjecture": "",
                            "unresolved_questions": []}
                return {"judgment": "Supported in the tested setting; broader generality unresolved", "rationale": "No executed experiment."}

        with tempfile.TemporaryDirectory() as temp:
            provider = LimitedProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider,
                                  config=DeploymentConfig(mode="Limited", max_steps=10))
            state = agent.propose("Named method is slower under treatment than control.",
                                  objective="Compare iteration counts under matched seeds.")
            state.constraints.append("Use the same stopping tolerance.")
            attached = Source("paper:attached", "Named method algorithm")
            state.sources = [attached] + [Source(f"paper:{name}", f"Spectral source {name}",
                abstract=f"Source {name} describes a relevant spectral comparison of treatment and control.")
                for name in ("alpha", "beta", "gamma", "delta")]
            state.context_source_ids = [attached.id]
            _[-1].return_value = state.sources[1:]
            agent.chunk_store.add([Chunk("attached-algorithm", attached.id, attached.title, "Algorithm",
                                         "Algorithm 1 named method uses a regularized update.")])
            state = agent.run(state)
            self.assertEqual(state.agent_steps, 9)
            self.assertEqual(state.sources[1].id, "paper:delta")
            review = next(payload for properties, payload in provider.calls if "findings" in properties)
            self.assertEqual(len(review["sources"]), 5)
            self.assertEqual(len(state.structured_evidence), 5)
            self.assertEqual(len(state.experiments_planned), 1)
            self.assertEqual(state.judgment_method, "llm_judgment")
            for properties, payload in provider.calls:
                if "test_type" in properties:
                    self.assertNotIn("investigation_context", payload)
                    self.assertNotIn("attached_paper_passages", payload)
                    continue
                self.assertEqual(payload["investigation_context"]["objective"],
                                 "Compare iteration counts under matched seeds.")
                self.assertIn("Use the same stopping tolerance.",
                              payload["investigation_context"]["user_constraints"])
                self.assertIn("Algorithm 1 named method", payload["attached_paper_passages"][0]["text"])
            for properties, payload in provider.calls:
                if "code" in properties or "summary" in properties or "judgment" in properties:
                    self.assertEqual(len(payload["reviewed_papers"]), 5)

    def test_design_survives_code_generation_failure(self):
        class CodeFailureProvider:
            name = "code-failure"
            def generate(self, messages, schema):
                if "code" in schema["properties"]:
                    raise ProviderError("Responses API returned status 'incomplete' (max_output_tokens)")
                return {
                    'name': 'Paired spectra',
                    'baselines': ['flat spectrum'],
                    'metrics': ['iterations'],
                    'seeds': [0, 1, 2],
                    'algorithm_steps': ['Compare paired spectra.'],
                    'parameter_ranges_json': '{}',
                    'test_type': 'falsifying',
                    'additional_assumptions': [],
                }

        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", CodeFailureProvider())
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState("Harmonic decay is slower", "Harmonic decay is slower")
            pipeline.state.conjecture = Conjecture("Harmonic decay is slower", "Harmonic decay is slower",
                                                   experimentable=True)
            pipeline.plan_experiments()
            self.assertEqual(len(pipeline.state.experiments_planned), 1)
            self.assertEqual(pipeline.state.experiments_planned[0].code, "")
            self.assertTrue(any("code could not be generated" in text
                                for text in pipeline.state.unresolved_questions))

    def test_failed_provider_request_is_counted(self):
        class FailingProvider:
            name = "fixture-failure"
            last_usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
            last_cost_usd = 0.001
            price_known = True
            def generate(self, messages, schema):
                raise ProviderError("Responses API returned status 'incomplete' (max_output_tokens)")

        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", FailingProvider())
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState("A testable claim", "A testable claim")
            result = pipeline._generate("experiment_planning", "Plan a test.",
                                        {"conjecture": "A testable claim"}, {"required": []})
            self.assertIsNone(result)
            self.assertEqual(pipeline.state.model_calls, 1)
            self.assertEqual(pipeline.state.agent_steps, 1)
            self.assertEqual(pipeline.state.llm_calls[0]["status"], "failed")
            self.assertEqual(pipeline.state.token_usage, 30)

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_model_limit_produces_partial_report_without_more_model_calls(self, *_):
        class LimitedProvider:
            name = "limited-fixture"
            def __init__(self): self.calls = 0
            def generate(self, messages, schema):
                self.calls += 1
                raise ModelLimitError("The LLM rate or usage limit was reached.")

        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            provider = LimitedProvider()
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            state = agent.run(agent.propose("Every prime is odd."))
            self.assertEqual(provider.calls, 1)
            self.assertEqual(state.status, Status.COMPLETED)
            self.assertEqual(state.stop_reason, "model_limit")
            self.assertEqual(state.confidence_method, "unavailable")
            self.assertIn("LLM limit", state.report)
            self.assertTrue(any(event.action == "model_limit" for event in state.trace))
            self.assertEqual(repo.get(state.id).stop_reason, "model_limit")

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_resynthesis_retries_after_prior_model_limit(self, *_):
        class RecoveringProvider:
            name = "recovering-fixture"
            def __init__(self):
                self.calls = 0
                self.available = False
            def generate(self, messages, schema):
                self.calls += 1
                if not self.available:
                    raise ModelLimitError("The LLM rate or usage limit was reached.")
                if "summary" in schema["properties"]:
                    return {"summary": "Evidence remains inconclusive.",
                            "revised_conjecture": "", "unresolved_questions": []}
                return {"judgment": "Supported in the tested setting; broader generality unresolved", "rationale": "The available evidence is inconclusive."}

        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            provider = RecoveringProvider()
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            state = agent.run(agent.propose("Every prime is odd."))
            self.assertEqual(state.stop_reason, "model_limit")
            self.assertEqual(provider.calls, 1)

            provider.available = True
            state.pending_action = "resynthesize"
            state = agent.run(state)
            self.assertEqual(provider.calls, 3)
            self.assertIsNone(state.stop_reason)
            self.assertEqual(state.judgment_method, "llm_judgment")
            self.assertIn("Evidence remains inconclusive", state.report)
            self.assertNotIn("model-dependent findings may be incomplete", state.report)
            self.assertIsNone(repo.get(state.id).stop_reason)

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_attached_method_reaches_llm_and_enables_experiment(self, *_):
        passage = ("Algorithm 1 Kaczmarz++ uses adaptive momentum acceleration, "
                   "Tikhonov-regularized block projections, and memoized sampled blocks.")

        class PaperProvider:
            name = "paper-fixture"
            def __init__(self): self.calls = []
            def generate(self, messages, schema):
                payload = json.loads(messages[1]["content"])
                self.calls.append((schema["properties"], payload))
                if "normalized_statement" in schema["properties"]:
                    return {"normalized_statement": payload["statement"],
                            "mathematical_domain": "numerical linear algebra", "objects": ["Kaczmarz++"],
                            "assumptions": [], "measurable_predictions": ["More iterations for harmonic decay"],
                             "ambiguities": [], "experimentable": False}
                if "argument_searches" in schema["properties"]:
                    return {"argument_searches": []}
                if "queries" in schema["properties"]:
                    return {"queries": ["Kaczmarz++ harmonic spectrum", "accelerated Kaczmarz flat singular values",
                                        "randomized block Kaczmarz convergence"]}
                if "selected_source_ids" in schema["properties"]:
                    return {"selected_source_ids": [item["source_id"] for item in payload["candidates"]]}
                if "findings" in schema["properties"]:
                    return {"reviews": [{"source_id": item["source_id"], "relevance": "high",
                            "summary": "Relevant context.", "limitations": []}
                            for item in payload["sources"]],
                            "findings": [{"source_id": item["source_id"],
                            "excerpt": passage if passage in item["passages"][0]["text"] else item["passages"][0]["text"][:100],
                            "evidence_type": "related_result", "relation": "neutral",
                            "assumptions": [], "notes": "Defines the accelerated method."}
                            for item in payload["sources"]]}
                if "test_type" in schema["properties"]:
                    return {
                        'name': 'Spectrum comparison',
                        'baselines': ['flat spectrum'],
                        'metrics': ['iterations'],
                        'seeds': [0, 1, 2],
                        'algorithm_steps': ['Run matched Kaczmarz++ instances.'],
                        'parameter_ranges_json': '{"n": 20}',
                        'test_type': 'falsifying',
                        'additional_assumptions': ['Method details match paper'],
                    }
                if "code" in schema["properties"]:
                    return {"code": "print('planned')\n"}
                if "summary" in schema["properties"]:
                    return {"summary": "The paper defines the method; experiment awaits execution.",
                            "revised_conjecture": "", "unresolved_questions": []}
                return {"judgment": "Supported in the tested setting; broader generality unresolved", "rationale": "No executed results."}

        with tempfile.TemporaryDirectory() as temp:
            provider = PaperProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            question = "Accelerated Kaczmarz becomes slower when singular values decay harmonically rather than remaining approximately flat."
            state = agent.propose(question)
            source = Source("arxiv:2501.11673", "Randomized Kaczmarz Methods with Beyond-Krylov Convergence",
                            arxiv_id="2501.11673")
            state.sources.append(source)
            abstract_source = Source("doi:comparison", "Spectral convergence comparison",
                                     abstract="A separate spectral comparison describes flat and harmonic singular values.")
            full_source = Source("doi:full", "Kaczmarz spectral analysis")
            state.sources.extend([abstract_source, full_source])
            _[-1].return_value = [abstract_source, full_source]
            state.context_source_ids.append(source.id)
            agent.chunk_store.add([
                Chunk("method-1", source.id, source.title, "Algorithm", passage),
                Chunk("method-2", source.id, source.title, "Appendix",
                      "Algorithm 2 transforms an unrelated auxiliary matrix."),
                Chunk("full-1", full_source.id, full_source.title, "Results",
                      "Kaczmarz convergence depends on the singular value distribution."),
            ])
            state = agent.run(state)
            interpretation = next(payload for properties, payload in provider.calls if "normalized_statement" in properties)
            planning = next(payload for properties, payload in provider.calls if "test_type" in properties)
            self.assertIn(passage, interpretation["attached_paper_passages"][0]["text"])
            self.assertNotIn("attached_paper_passages", planning)
            self.assertNotIn("reviewed_papers", planning)
            self.assertNotIn("svg_contract", planning)
            self.assertEqual(planning["normalized_statement"], state.conjecture.normalized_statement)
            self.assertEqual(planning["objects"], state.conjecture.objects)
            self.assertEqual(planning["assumptions"], state.conjecture.assumptions)
            self.assertTrue(all(passage in call_payload["attached_paper_passages"][0]["text"]
                                for properties, call_payload in provider.calls if "test_type" not in properties))
            self.assertTrue(any("queries" in properties for properties, _ in provider.calls))
            self.assertTrue(any("summary" in properties for properties, _ in provider.calls))
            self.assertTrue(any("judgment" in properties for properties, _ in provider.calls))
            self.assertTrue(any("code" in properties for properties, _ in provider.calls))
            code_payload = next(payload for properties, payload in provider.calls if "code" in properties)
            reviewed = {item["source_id"]: item for item in code_payload["reviewed_papers"]}
            self.assertEqual(set(reviewed), {source.id, abstract_source.id, full_source.id})
            self.assertEqual(reviewed[source.id]["findings"][0]["excerpt"], passage)
            self.assertEqual(reviewed[abstract_source.id]["text_scope"], "abstract_only")
            self.assertIn("flat and harmonic", reviewed[abstract_source.id]["passage"]["text"])
            self.assertEqual(reviewed[full_source.id]["text_scope"], "full_text_passage")
            self.assertIn("singular value distribution", reviewed[full_source.id]["passage"]["text"])
            for properties, call_payload in provider.calls:
                if ("code" in properties or
                        "summary" in properties or "judgment" in properties):
                    self.assertEqual({item["source_id"] for item in call_payload["reviewed_papers"]}, set(reviewed))
                elif "findings" in properties:
                    self.assertIn(source.id, {item["source_id"] for item in call_payload["reviewed_papers"]})
                else:
                    self.assertNotIn("reviewed_papers", call_payload)
            self.assertEqual(len(state.experiments_planned), 1)
            self.assertIn(source.id, state.inspected_papers)
            self.assertGreaterEqual(state.model_calls, 5)

    def test_report_includes_all_literature_relations_and_upgrades_saved_report(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            state = ResearchState("Claim", "Claim", status=Status.COMPLETED)
            state.conjecture = Conjecture("Claim", "Claim")
            state.sources = [Source("doi:example", "Relevant paper", abstract="A study abstract.", doi="example")]
            state.structured_evidence = [
                EvidenceItem("doi:example", "Relevant paper", f"Finding {relation}", "discussion", relation,
                             "abstract", notes=f"Note {relation}")
                for relation in ("supports", "contradicts", "qualifies", "neutral")]
            state.assessment = ConjectureAssessment(
                literature_support=state.structured_evidence[:1],
                literature_contradictions=state.structured_evidence[1:2],
                literature_qualifications=state.structured_evidence[2:3])
            state.confidence_factors = ConfidenceFactors()
            state.confidence = 0.5
            state.report = "# Old report"
            repo.save(state)
            report = repo.get(state.id).report
            self.assertIn("ResearchPilot assessment: Qualitative judgment not yet generated", report)
            self.assertNotIn("confidence estimate:", report)
            for heading in ("Supporting evidence", "Contradictory evidence", "Important qualifications", "Related evidence"):
                self.assertIn(f"## {heading}", report)
            for relation in ("supports", "contradicts", "qualifies", "neutral"):
                self.assertIn(f"Finding {relation}", report)
            state.report = report.replace("ResearchPilot assessment: Qualitative judgment not yet generated", "ResearchPilot confidence estimate: 50% — unresolved or mixed")
            repo.save(state)
            self.assertIn("ResearchPilot assessment: Qualitative judgment not yet generated", repo.get(state.id).report)

    class ExcerptProvider:
        name = "fixture"
        def __init__(self, fabricated=False): self.fabricated = fabricated
        def generate(self, messages, schema):
            if "normalized_statement" in schema["properties"]:
                return {"normalized_statement": "Every prime is odd.", "mathematical_domain": "number theory",
                        "objects": ["prime numbers"], "assumptions": [], "measurable_predictions": [],
                        "ambiguities": [], "experimentable": False}
            if "selected_source_ids" in schema["properties"]:
                return {"selected_source_ids": [item["source_id"] for item in json.loads(messages[1]["content"])["candidates"]]}
            if "findings" in schema["properties"]:
                source_id = json.loads(messages[1]["content"])["sources"][0]["source_id"]
                return {"reviews": [{"source_id": source_id, "relevance": "high",
                        "summary": "A direct counterexample.", "limitations": []}],
                        "findings": [{"source_id": source_id,
                        "excerpt": "Invented theorem" if self.fabricated else "The number 2 is prime and even.",
                        "evidence_type": "counterexample", "relation": "contradicts", "assumptions": [],
                        "notes": "Direct counterexample."}]}
            return {"summary": "", "revised_conjecture": "", "unresolved_questions": []}

    class DesignProvider:
        name = "fixture-design"
        def generate(self, messages, schema):
            if "code" in schema["properties"]:
                return {"code": "print('planned')\n", "visualization_code": CHART_CODE}
            if "normalized_statement" in schema["properties"]:
                return {"normalized_statement": "Treatment increases the measured outcome.",
                    "mathematical_domain": "numerical", "objects": ["treatment", "outcome"],
                    "assumptions": [], "measurable_predictions": ["Higher treatment mean"],
                    "ambiguities": [], "experimentable": True}
            return {
                'name': 'Paired comparison',
                'baselines': ['untreated'],
                'metrics': ['mean outcome'],
                'seeds': [0, 1, 2],
                'algorithm_steps': ['Run paired treatment and control.'],
                'parameter_ranges_json': '{"n": 5}',
                'test_type': 'falsifying',
                'additional_assumptions': ['Same measurement method'],
            }

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_model_generated_code_has_design_labels_even_if_not_run(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", self.DesignProvider())
            state = agent.run(agent.propose("Treatment increases the measured outcome."))
            self.assertEqual(len(state.experiments_planned), 1)
            self.assertEqual(state.experiments_planned[0].algorithm_steps,
                             ["Run paired treatment and control."])
            self.assertEqual(state.experiments_planned[0].seeds, [0, 1, 2])
            self.assertEqual(state.experiments_planned[0].limitations, [])
            code = state.experiments_planned[0].code
            self.assertIn("# BASELINES AND METRICS", code)
            self.assertNotIn("# WHY THIS EXPERIMENT", code)
            self.assertNotIn("# DECISION CRITERIA", code)
            self.assertIn("# WORKING ASSUMPTIONS", code)
            self.assertIn("# EXECUTABLE STUDY", code)
            self.assertEqual(state.experiments_completed, [])

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    def test_evidence_must_quote_retrieved_text(self, _):
        source = Source("doi:fixture", "Prime numbers", abstract="The number 2 is prime and even.", doi="fixture", verified=True)
        with patch("researchpilot.literature.CrossrefClient.search", return_value=[source]):
            with tempfile.TemporaryDirectory() as temp:
                repo = ResearchRepository(Path(temp) / "state.sqlite")
                honest = ResearchAgent(repo, Path(temp) / "artifacts", self.ExcerptProvider())
                state = honest.run(honest.propose("Every prime is odd."))
                self.assertEqual(len(state.structured_evidence), 1)
                self.assertEqual(state.structured_evidence[0].relation_to_conjecture, "contradicts")
                self.assertEqual(state.structured_evidence[0].evidence_type, "counterexample")
                fabricated = ResearchAgent(repo, Path(temp) / "artifacts2", self.ExcerptProvider(True))
                state = fabricated.run(fabricated.propose("Every prime is odd."))
                self.assertEqual(state.structured_evidence, [])

    def test_benchmark_covers_required_claim_types(self):
        path = Path(__file__).resolve().parents[1] / "researchpilot" / "evaluation_data" / "conjectures.json"
        categories = {item["category"] for item in json.loads(path.read_text(encoding="utf-8"))}
        self.assertEqual(len(categories), 7)

    def test_ranking_uses_conjecture_terms(self):
        conjecture = Conjecture("Kaczmarz singular values", "Kaczmarz singular values")
        papers = [Source("irrelevant", "Botany"), Source("relevant", "Kaczmarz singular values")]
        self.assertEqual(rank_sources(conjecture, papers)[0].id, "relevant")

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_literature_queries_come_from_provider_prompt(self, crossref_search, arxiv_search):
        class SearchProvider:
            name = "fixture-search"
            def __init__(self): self.search_messages = None
            def generate(self, messages, schema):
                if "normalized_statement" in schema["properties"]:
                    return {"normalized_statement": "Every prime is odd.", "mathematical_domain": "number theory",
                            "objects": ["prime numbers"], "assumptions": [], "measurable_predictions": [],
                             "ambiguities": [], "experimentable": False}
                if "argument_searches" in schema["properties"]:
                    return {"argument_searches": []}
                if "queries" in schema["properties"]:
                    self.search_messages = messages
                    return {"queries": ["prime number parity theorem", "odd primes assumptions",
                                        "even prime counterexample"]}
                if "summary" in schema["properties"]:
                    return {"summary": "No evidence was retrieved.", "revised_conjecture": "",
                            "unresolved_questions": ["Find a relevant source."]}
                if "test_type" in schema["properties"]:
                    return {
                        'name': 'No finite test',
                        'parameter_ranges_json': '{}',
                        'test_type': 'not_meaningful',
                        'additional_assumptions': [],
                    }
                if "judgment" in schema["properties"]:
                    return {"judgment": "Supported in the tested setting; broader generality unresolved", "rationale": "No evidence was retrieved."}
                raise AssertionError("unexpected provider call")

        with tempfile.TemporaryDirectory() as temp:
            provider = SearchProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            state = agent.run(agent.propose("Every prime is odd."))
            self.assertEqual(state.literature_queries,
                             ["prime number parity theorem", "odd primes assumptions", "even prime counterexample"])
            search_payload = json.loads(provider.search_messages[1]["content"])
            self.assertEqual(search_payload["conjecture"], "Every prime is odd.")
            self.assertEqual(search_payload["assumptions"], [])
            self.assertEqual(search_payload["investigation_context"]["question"], "Every prime is odd.")
            self.assertIn("counterexamples", provider.search_messages[0]["content"])
            self.assertEqual([call.args[0] for call in arxiv_search.call_args_list],
                             ["prime number parity theorem", "even prime counterexample"])
            self.assertEqual([call.args[0] for call in crossref_search.call_args_list],
                             ["odd primes assumptions"])

    def test_reasoning_sources_are_automatically_selected_before_additional_results(self):
        class SearchProvider:
            name = "argument-search-fixture"
            def __init__(self): self.interpretation_schema = None; self.selection = None; self.reasoning_schema = None
            def generate(self, messages, schema):
                properties = schema["properties"]
                if "normalized_statement" in properties:
                    self.interpretation_schema = schema
                    return {"normalized_statement": "Every prime is odd.",
                            "mathematical_domain": "number theory", "objects": ["primes"],
                            "assumptions": [], "measurable_predictions": [],
                            "ambiguities": [], "experimentable": False}
                if "argument_searches" in properties:
                    self.reasoning_schema = schema
                    return {"argument_searches": [{"relation": "contradicts",
                            "observation": "Two is an even prime.",
                            "query": "prime number two even counterexample", "source_ids": [], "source_urls": []}]}
                if "queries" in properties:
                    return {"queries": ["prime parity theorem", "odd primes theorem",
                                        "prime number properties"]}
                if "selected_source_ids" in properties:
                    self.selection = json.loads(messages[1]["content"])
                    return {"selected_source_ids": ["general", "unknown", "argument", "general"]}
                raise AssertionError("unexpected provider call")

        def arxiv(query, *args, **kwargs):
            if query == "prime number two even counterexample":
                return [Source("argument", "Prime number two and parity", abstract="Two is even.")]
            return [Source("general", "Prime parity theorem", abstract="General background.")]

        with tempfile.TemporaryDirectory() as temp, \
             patch("researchpilot.literature.ArxivClient.search", side_effect=arxiv) as arxiv_search, \
             patch("researchpilot.literature.CrossrefClient.search", return_value=[]):
            provider = SearchProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            state = agent.propose("Every prime is odd.")
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            pipeline.interpret()
            self.assertNotIn("supporting_observations", provider.interpretation_schema["properties"])
            self.assertNotIn("contradicting_observations", provider.interpretation_schema["properties"])
            pipeline.reason_generally()
            self.assertNotIn("queries", provider.reasoning_schema["properties"])
            self.assertEqual(state.literature_queries, [])
            self.assertEqual(state.general_reasoning[0]["source_ids"], ["argument"])
            pipeline.search_literature()
            self.assertEqual(arxiv_search.call_args_list[0].args[0],
                             "prime number two even counterexample")
            self.assertEqual(state.conjecture.contradicting_observations, ["Two is an even prime."])
            self.assertEqual(state.sources[0].id, "argument")
            self.assertEqual(state.reasoning_source_ids, ["argument"])
            self.assertEqual(state.additional_literature_source_ids, ["general"])
            self.assertEqual(state.selected_source_ids, ["argument", "general"])
            self.assertEqual(provider.selection["automatically_selected_source_ids"], ["argument"])
            self.assertEqual([item["source_id"] for item in provider.selection["candidates"]], ["general"])
            self.assertEqual(provider.selection["investigation_context"]["general_reasoning"], state.general_reasoning)
            saved = repo.get(state.id)
            self.assertEqual(saved.general_reasoning, state.general_reasoning)
            self.assertEqual(saved.reasoning_source_ids, ["argument"])
            self.assertEqual(saved.selected_source_ids, ["argument", "general"])

    def test_reasoning_provenance_survives_duplicate_records_and_existing_search_hits(self):
        class Provider:
            name = "reasoning-provenance-fixture"
            def generate(self, messages, schema):
                if "argument_searches" in schema["properties"]:
                    return {"argument_searches": [
                        {"relation": "contradicts", "observation": "Two is an even prime.",
                         "query": "even prime two", "source_ids": [], "source_urls": []},
                        {"relation": "supports", "observation": "Primes above two are odd.",
                         "query": "", "source_ids": ["attached", "invented"], "source_urls": []}]}
                return {"queries": ["parity theorem", "prime assumptions", "prime counterexample"]}

        with tempfile.TemporaryDirectory() as temp, \
             patch("researchpilot.literature.ArxivClient.search") as arxiv, \
             patch("researchpilot.literature.CrossrefClient.search", return_value=[]):
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", Provider())
            state = ResearchState("Every prime is odd.", "Check parity")
            state.conjecture = Conjecture(state.question, state.question)
            attached = Source("attached", "Prime number parity analysis", abstract="Primes above two are odd.")
            existing = Source("doi:parity", "Even prime counterexample", doi="10.1234/parity")
            alias = Source("arxiv:parity", existing.title, arxiv_id="parity", abstract="Two is even.")
            state.sources = [attached, existing]
            state.context_source_ids = [attached.id]
            arxiv.side_effect = [[existing], [alias], [alias]]
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            pipeline.reason_generally()
            self.assertEqual(state.reasoning_source_ids, [existing.id, attached.id])
            self.assertEqual(state.general_reasoning[1]["source_ids"], [attached.id])
            self.assertEqual(arxiv.call_count, 1)
            pipeline.search_literature()
            self.assertEqual(state.reasoning_source_ids, [alias.id, attached.id])
            self.assertEqual(state.general_reasoning[0]["source_ids"], [alias.id])
            self.assertEqual(state.additional_literature_source_ids, [alias.id])
            self.assertEqual(state.selected_source_ids, [attached.id, alias.id])
            saved = repo.get(state.id)
            self.assertEqual(saved.general_reasoning, state.general_reasoning)
            self.assertEqual(saved.additional_literature_source_ids, [alias.id])
            from researchpilot.reporting import render_conjecture_report
            report = render_conjecture_report(saved)
            self.assertIn("Two is an even prime. [Even prime counterexample]", report)
            self.assertIn("Even prime counterexample", report)

    def test_source_selection_uses_only_additional_candidates_and_respects_capacity(self):
        class Provider:
            name = "selection-capacity-fixture"
            price = ModelPrice(0, 0)
            price_known = True
            max_output_tokens = 1000
            def generate(self, messages, schema):
                if "findings" in schema["properties"]:
                    self.reviewed_ids = [item["source_id"] for item in json.loads(messages[1]["content"])["sources"]]
                    return {"reviews": [], "findings": []}
                self.payload = json.loads(messages[1]["content"])
                return {"selected_source_ids": ["unsearched", "extra-b", "extra-b", "extra-a"]}

        with tempfile.TemporaryDirectory() as temp:
            provider = Provider()
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", provider,
                                  config=DeploymentConfig(mode="Limited", max_papers=3))
            state = ResearchState("Every prime is odd.", "Check parity")
            state.conjecture = Conjecture(state.question, state.question)
            state.sources = [Source(sid, sid, abstract="A source passage.")
                             for sid in ["unsearched", "extra-a", "reasoning", "attached", "extra-b"]]
            state.context_source_ids = ["attached"]
            state.reasoning_source_ids = ["reasoning"]
            state.additional_literature_source_ids = ["extra-a", "extra-b"]
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            pipeline.select_sources()
            self.assertEqual(provider.payload["capacity"], 1)
            self.assertEqual(state.selected_source_ids, ["attached", "reasoning", "extra-b"])
            self.assertEqual([item["source_id"] for item in provider.payload["candidates"]], ["extra-a", "extra-b"])
            # Later review must honor the persisted selection even if the catalog order changes.
            state.sources.sort(key=lambda source: source.id != "unsearched")
            pipeline.extract_evidence()
            self.assertEqual(provider.reviewed_ids, ["attached", "reasoning", "extra-b"])

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_reasoning_search_failure_keeps_observation_without_fabricated_sources(self, crossref, arxiv):
        class Provider:
            name = "no-source-fixture"
            price = ModelPrice(0, 0)
            price_known = True
            max_output_tokens = 1000
            def generate(self, messages, schema):
                return {"argument_searches": [{"relation": "contradicts", "observation": "Two is even.",
                                               "query": "even prime", "source_ids": ["fabricated"], "source_urls": []}]}

        with tempfile.TemporaryDirectory() as temp:
            agent = ResearchAgent(ResearchRepository(Path(temp) / "state.sqlite"),
                                  Path(temp) / "artifacts", Provider(),
                                  config=DeploymentConfig(mode="Limited", max_tools=6))
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState("Every prime is odd.", "Check parity")
            pipeline.state.conjecture = Conjecture("Every prime is odd.", "Every prime is odd.")
            pipeline.reason_generally()
            self.assertEqual(pipeline.state.general_reasoning[0]["source_ids"], [])
            self.assertEqual(pipeline.state.reasoning_source_ids, [])
            self.assertEqual(pipeline.state.conjecture.contradicting_observations, ["Two is even."])
            self.assertEqual(pipeline.state.tool_calls, 2)
            self.assertEqual((arxiv.call_count, crossref.call_count), (1, 1))

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_confidence_uses_synthesis_provider_and_records_prediction(self, *_):
        class RoutineProvider:
            name = "routine"
            def generate(self, messages, schema):
                if "normalized_statement" in schema["properties"]:
                    return {"normalized_statement": "Every prime is odd.", "mathematical_domain": "number theory",
                            "objects": ["prime numbers"], "assumptions": [], "measurable_predictions": [],
                             "ambiguities": [], "experimentable": False}
                if "argument_searches" in schema["properties"]:
                    return {"argument_searches": []}
                if "queries" in schema["properties"]:
                    return {"queries": ["prime parity", "odd prime theorem", "even prime counterexample"]}
                if "summary" in schema["properties"]:
                    return {"summary": "No evidence was retrieved.", "revised_conjecture": "",
                            "unresolved_questions": ["Check the prime 2."]}
                if "test_type" in schema["properties"]:
                    return {
                        'name': 'No finite test',
                        'parameter_ranges_json': '{}',
                        'test_type': 'not_meaningful',
                        'additional_assumptions': [],
                    }
                raise AssertionError("confidence should use the synthesis provider")

        class StrongProvider:
            name = "strong"
            def __init__(self): self.messages = None
            def generate(self, messages, schema):
                self.messages = messages
                self.schema = schema
                return {"judgment": "Supported in the tested setting; broader generality unresolved", "rationale": "The claim should be checked against 2."}

        with tempfile.TemporaryDirectory() as temp:
            strong = StrongProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", RoutineProvider(), synthesis_provider=strong)
            state = agent.run(agent.propose("Every prime is odd."))
            self.assertEqual(state.conjecture_judgment, "Supported in the tested setting; broader generality unresolved")
            self.assertEqual(state.confidence, 0.0)
            self.assertNotIn("probability_true", strong.schema["properties"])
            self.assertNotIn("enum", strong.schema["properties"]["judgment"])
            self.assertEqual(repo.get(state.id).conjecture_judgment, state.conjecture_judgment)
            self.assertEqual(state.judgment_method, "llm_judgment")
            self.assertEqual(state.judgment_rationale, "The claim should be checked against 2.")
            self.assertIn("The claim should be checked against 2.", state.report)
            self.assertIn("No relevant literature results found", state.report)
            self.assertIn("The claim should be checked against 2.", state.report)
            self.assertIn("which specific evidence most affects", strong.messages[0]["content"])
            self.assertTrue(any("The claim should be checked against 2." in event.summary
                                for event in state.trace if event.action == "confidence_estimation"))
            self.assertEqual(json.loads(strong.messages[1]["content"])["conjecture"], "Every prime is odd.")
            self.assertEqual(state.llm_calls[-1]["model"], "strong")

    def test_execution_prompt_interprets_measured_results_without_replacing_metrics(self):
        class ExecutionProvider:
            name = "execution-fixture"
            def __init__(self): self.messages = None
            def generate(self, messages, schema):
                self.messages = messages
                raw = json.loads(messages[1]["content"])["raw_result"]
                pointer = ("/control" if "control" in raw else
                           "/control_iterations/32" if "control_iterations" in raw else "/runs/baseline")
                return {"finding": "The treatment mean was higher in the supplied trials.",
                        "uncertainty": "Only three paired trials were run.",
                        "robustness": "The result needs more regimes.",
                        "relation": "supports", "confounders": ["small sample"],
                        "evidence_paths": [pointer]}

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            path.write_text(json.dumps({"control": [1, 1, 1], "treatment": [2, 2, 2],
                                        "higher_supports": True}), encoding="utf-8")
            provider = ExecutionProvider()
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts", provider)
            state = ResearchState("Treatment raises the outcome.", "Treatment raises the outcome.")
            state.conjecture = Conjecture(state.question, state.question, experimentable=True)
            source = Source("paper:method", "Treatment method")
            state.sources.append(source)
            state.context_source_ids.append(source.id)
            reviewed_source = Source("doi:reviewed", "Reviewed treatment study",
                                     abstract="A reviewed abstract discusses the treatment method.")
            state.sources.append(reviewed_source)
            state.structured_evidence.append(EvidenceItem(
                reviewed_source.id, reviewed_source.title, "A reviewed abstract discusses the treatment method.",
                "discussion", "neutral", "abstract; full text not inspected"))
            agent.chunk_store.add([Chunk("method-context", source.id, source.title, "Method",
                                         "Algorithm 1 defines the treatment update.")])
            state.experiments_planned = [ExperimentDesign(
                "design-1", "Paired test", "Treatment raises the outcome", ["treatment"], ["outcome"],
                ["same sample"], [], [], {}, [0, 1, 2], "higher outcome", [], "print('run')",
                "no increase", ["small sample"])]
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            output = SimpleNamespace(status="completed", stderr="", stdout="", runtime_seconds=0.1,
                                     artifacts=[str(path)])
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
            self.assertEqual(state.experiments_completed[0].metrics["control"], [1, 1, 1])
            self.assertEqual(state.experimental_evidence[0].relation_to_conjecture, "supports")
            self.assertEqual(state.experimental_evidence[0].finding,
                             "The treatment mean was higher in the supplied trials.")
            self.assertEqual(json.loads(provider.messages[1]["content"])["result"]["treatment"], [2, 2, 2])
            self.assertIn("Algorithm 1 defines the treatment update.",
                          json.loads(provider.messages[1]["content"])["attached_paper_passages"][0]["text"])
            self.assertEqual(json.loads(provider.messages[1]["content"])["reviewed_papers"][0]["source_id"],
                             reviewed_source.id)

            path.write_text(json.dumps({"control": [10, 12, 20], "treatment": [14, 20, 20],
                                        "control_censored": [False, False, True],
                                        "treatment_censored": [False, True, True],
                                        "higher_supports": True}), encoding="utf-8")
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
            self.assertEqual(state.experiments_completed[-1].metrics["control_censored"],
                             [False, False, True])
            self.assertTrue(any("censored counts" in question
                                for question in state.unresolved_questions))

            path.write_text(json.dumps({
                "control_iterations": {"32": [10, 11, 12], "64": [20, 21, 22]},
                "treatment_iterations": {"32": [20, 22, 24], "64": [40, 42, 44]},
                "higher_supports": {"32": True, "64": True},
                "control_censored": {"32": [False]*3, "64": [False]*3},
                "treatment_censored": {"32": [False]*3, "64": [False]*3}
            }), encoding="utf-8")
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
            self.assertEqual(len(state.experiments_completed[-1].metrics["regimes"]), 2)
            self.assertEqual(state.stochastic_trials, 9)
            self.assertEqual([item["label"] for item in state.experiments_completed[-1].metrics["regimes"]],
                             ["32", "64"])

            agent.provider = ExecutionProvider()
            path.write_text(json.dumps({"runs": {"baseline": [1, 2, 3],
                                                "changed": [4, 5, 6]}}), encoding="utf-8")
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
            self.assertEqual(state.experiments_completed[-1].metrics["runs"]["baseline"], [1, 2, 3])
            self.assertEqual(state.stochastic_trials, 9)

            class InventedPathProvider(ExecutionProvider):
                def generate(self, messages, schema):
                    answer = super().generate(messages, schema)
                    answer["evidence_paths"] = ["/runs/invented"]
                    return answer

            agent.provider = InventedPathProvider()
            prior_evidence = len(state.experimental_evidence)
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
            self.assertEqual(len(state.experimental_evidence), prior_evidence)
            self.assertIn("cited missing result data", state.failed_attempts[-1])
            self.assertTrue(state.failed_attempts[-1].startswith("experiment_interpretation:"))
            self.assertEqual(state.experiments_planned[0].execution_status, "completed")
            failure = next(event for event in reversed(state.trace)
                           if event.action == "experiment_interpretation")
            self.assertEqual(failure.status, "failed")
            self.assertEqual(failure.outputs["category"], "experiment_interpretation")
            self.assertEqual(state.trace[-1].outputs["execution_failures"], 0)
            self.assertEqual(state.trace[-1].outputs["interpretation_failures"], 1)

            class GeneralResultProvider(ExecutionProvider):
                def generate(self, messages, schema):
                    return {"finding": "A single duration was measured.", "uncertainty": "No comparison was run.",
                            "robustness": "One measurement only.", "relation": "inconclusive",
                            "confounders": ["clock variation"], "evidence_paths": ["/duration_seconds"]}

            agent.provider = GeneralResultProvider()
            path.write_text(json.dumps({"duration_seconds": 0.7, "method": "single run"}), encoding="utf-8")
            with patch.dict("os.environ", {"RESEARCHPILOT_EXECUTOR": "docker"}), \
                 patch("researchpilot.conjecture_pipeline.executor_from_env") as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
            self.assertEqual(state.experiments_completed[-1].metrics["duration_seconds"], 0.7)
            self.assertEqual(state.experimental_evidence[-1].relation_to_conjecture, "inconclusive")
            self.assertEqual(state.experimental_evidence[-1].evidence_paths, ["/duration_seconds"])

    @patch("researchpilot.literature.ArxivClient.search", return_value=[])
    @patch("researchpilot.literature.CrossrefClient.search", return_value=[])
    def test_no_provider_does_not_plan_a_hardcoded_experiment(self, *_):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / "state.sqlite")
            agent = ResearchAgent(repo, Path(temp) / "artifacts")
            state = agent.propose("Coordinate descent becomes slower when curvature varies rather than remaining flat.")
            state = agent.run(state)
            saved = repo.get(state.id)
            self.assertEqual(len(saved.literature_queries), 3)
            self.assertTrue(saved.literature_queries[1].startswith("coordinate descent"))
            self.assertEqual(saved.experiments_planned, [])
            self.assertEqual(saved.experiments_completed, [])
            self.assertEqual(saved.experimental_evidence, [])
            self.assertTrue(saved.unresolved_questions)
            self.assertIsNone(saved.confidence_factors)
            self.assertEqual(saved.confidence_method, "unavailable")
            self.assertIn("No relevant literature results found", saved.judgment_rationale)
            self.assertIn("No completed experimental results", saved.judgment_rationale)
            self.assertEqual(len(STAGES), 9)
            self.assertEqual(len(saved.plan), 9)
            self.assertEqual([event.action for event in saved.trace if event.action in STAGES], list(STAGES))
            self.assertTrue({"interpretation", "ai_general_reasoning", "additional_literature_search", "evidence_extraction", "experiment_planning",
                             "experiment_execution", "evidence_synthesis", "confidence_estimation", "final_report"}
                            <= {event.action for event in saved.trace})


if __name__ == "__main__":
    unittest.main()
