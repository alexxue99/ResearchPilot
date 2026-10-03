import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.agent.tools import ToolOutcome
from researchpilot.conjecture_pipeline import ConjecturePipeline
from researchpilot.deployment import DeploymentConfig
from researchpilot.models import ExperimentDesign, ExperimentResult, ResearchState, Source
from researchpilot.storage import ResearchRepository, state_from_dict


class ExecutionStatusTests(unittest.TestCase):
    def state(self):
        state = ResearchState('Claim', 'Claim')
        state.experiments_planned = [ExperimentDesign(
            'design-1', 'Test', 'Claim', [], [], [], [], [], {}, [], '', [],
            code="print('test')", test_type='falsifying')]
        return state

    def test_timeout_is_saved_without_becoming_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / 'state.sqlite')
            pipeline = ConjecturePipeline(ResearchAgent(repo, Path(temp) / 'artifacts'))
            pipeline.state = self.state()
            pipeline.state.failed_attempts = ['arxiv_search failed: TimeoutError: search timed out']
            output = SimpleNamespace(status='timeout', stderr='container execution exceeded timeout')
            with patch.dict('os.environ', {'RESEARCHPILOT_EXECUTOR': 'docker'}), \
                 patch('researchpilot.conjecture_pipeline.executor_from_env') as factory:
                factory.return_value.run.return_value = output
                pipeline.execute_experiments()
                self.assertEqual(factory.call_args.args[1], 300)
            repo.save(pipeline.state)
            saved = repo.get(pipeline.state.id)
            self.assertEqual(saved.experiments_planned[0].execution_status, 'timeout')
            self.assertIn('exceeded timeout', saved.experiments_planned[0].execution_error)
            self.assertEqual(saved.experiments_completed, [])
            self.assertEqual(saved.experimental_evidence, [])
            failure = next(event for event in saved.trace if event.action == 'experiment_attempt')
            self.assertEqual(failure.status, 'failed')
            self.assertEqual(failure.outputs['category'], 'experiment_execution')
            self.assertEqual(failure.outputs['design_id'], 'design-1')
            self.assertIn('exceeded timeout', failure.summary)
            summary = saved.trace[-1]
            self.assertEqual(summary.outputs['execution_failures'], 1)
            self.assertIn('1 execution failures', summary.summary)

    def test_success_summary_excludes_search_and_other_stage_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / 'state.sqlite')
            pipeline = ConjecturePipeline(ResearchAgent(repo, Path(temp) / 'artifacts'))
            pipeline.state = self.state()
            pipeline.state.experiments_planned[0].code = ''
            pipeline.state.experiments_completed = [ExperimentResult('result-1', 'design-1', 'completed', {}, {})]
            pipeline.state.failed_attempts = [
                'arxiv_search failed: TimeoutError: timed out',
                'literature_search failed: HTTPError: HTTP Error 429',
                'experiment_planning: ProviderError: unavailable',
                'experiment_interpretation: ValueError: missing result data',
                'visualization: ValueError: invalid SVG',
            ]
            pipeline.execute_experiments()
            summary = repo.get(pipeline.state.id).trace[-1]
            self.assertEqual(summary.outputs, {'executed': 1, 'execution_failures': 0,
                                              'interpretation_failures': 1, 'visualization_failures': 1})
            self.assertIn('0 execution failures', summary.summary)

    def test_search_outcomes_are_checkpointed_with_service_and_query(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / 'state.sqlite')
            pipeline = ConjecturePipeline(ResearchAgent(repo, Path(temp) / 'artifacts'))
            pipeline.state = self.state()
            registry = Mock()
            registry.call.return_value = ToolOutcome('failed', 'arxiv_search failed: HTTPError: HTTP Error 429')
            self.assertEqual(pipeline._search_sources(registry, 'arxiv_search', 'Fourier features'), [])
            saved = repo.get(pipeline.state.id)
            event = saved.trace[-1]
            self.assertEqual(event.action, 'literature_search')
            self.assertEqual(event.status, 'failed')
            self.assertIn('arXiv', event.summary)
            self.assertIn('429', event.summary)
            self.assertEqual(event.inputs['query'], 'Fourier features')
            self.assertEqual(event.outputs['category'], 'literature_search')
            self.assertEqual(saved.tool_calls, 1)
            self.assertEqual(len(saved.failed_attempts), 1)
            pipeline.state.sources.append(Source('source-1', 'Fourier features'))
            registry.call.return_value = ToolOutcome('completed', 'Retrieved 1 scholarly records.',
                                                     {'sources': [{'id': 'source-1'}]})
            self.assertEqual(pipeline._search_sources(registry, 'literature_search', 'ridge'), ['source-1'])
            event = repo.get(pipeline.state.id).trace[-1]
            self.assertEqual(event.status, 'completed')
            self.assertIn('Crossref', event.summary)
            self.assertEqual(len(pipeline.state.failed_attempts), 1)

    def test_legacy_timeout_and_explicit_reset(self):
        payload = self.state().to_dict()
        design = payload['experiments_planned'][0]
        del design['execution_status']
        del design['execution_error']
        payload['failed_attempts'] = ['experiment: RuntimeError: executor timeout: exceeded timeout']
        self.assertEqual(state_from_dict(payload).experiments_planned[0].execution_status, 'timeout')
        design['execution_status'] = 'planned'
        self.assertEqual(state_from_dict(payload).experiments_planned[0].execution_status, 'planned')

    def test_legacy_failures_are_not_assigned_to_multiple_designs(self):
        payload = self.state().to_dict()
        design = payload['experiments_planned'][0]
        del design['execution_status']
        payload['experiments_planned'].append({**design, 'id': 'design-2'})
        payload['failed_attempts'] = ['experiment: RuntimeError: executor timeout: exceeded timeout']
        self.assertTrue(all(d.execution_status == 'planned' for d in state_from_dict(payload).experiments_planned))

    def test_experiment_limits_in_full_and_limited_modes(self):
        for config in (DeploymentConfig(),
                       DeploymentConfig(mode="Limited", max_repetitions=20, max_experiment_seconds=600)):
            self.assertEqual(config.experiment_timeout_seconds, 300)
            self.assertEqual(config.experiment_seed_limit, 5)
        config = DeploymentConfig(mode="Limited", max_repetitions=3, max_experiment_seconds=120)
        self.assertEqual(config.experiment_timeout_seconds, 120)
        self.assertEqual(config.experiment_seed_limit, 3)
        with patch.dict('os.environ', {'RESEARCHPILOT_MODE': 'Limited'}, clear=True):
            config = DeploymentConfig.from_env()
            self.assertEqual(config.experiment_timeout_seconds, 60)
            self.assertEqual(config.experiment_seed_limit, 5)

    def test_saved_design_over_seed_cap_is_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ResearchRepository(Path(temp) / 'state.sqlite')
            pipeline = ConjecturePipeline(ResearchAgent(repo, Path(temp) / 'artifacts'))
            pipeline.state = self.state()
            pipeline.state.experiments_planned[0].seeds = list(range(6))
            with patch.dict('os.environ', {'RESEARCHPILOT_EXECUTOR': 'docker'}), \
                 patch('researchpilot.conjecture_pipeline.executor_from_env') as factory:
                pipeline.execute_experiments()
                factory.assert_not_called()
            design = pipeline.state.experiments_planned[0]
            self.assertEqual(design.execution_status, 'skipped')
            self.assertIn('maximum is 5', design.execution_error)
