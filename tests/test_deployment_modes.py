import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.agent.routing import providers_from_env
from researchpilot.api import create_app
from researchpilot.conjecture_pipeline import ConjecturePipeline
from researchpilot.deployment import DeploymentConfig
from researchpilot.models import Conjecture, ResearchState
from researchpilot.pricing import ModelPrice
from researchpilot.storage import ResearchRepository


class DeploymentModeTests(unittest.TestCase):
    def test_modes_and_time_limits(self):
        for mode, timeout, allowed in [('Full', 300, True), ('Limited', 60, True), ('Restricted', 60, False)]:
            with patch.dict('os.environ', {'RESEARCHPILOT_MODE': mode.lower()}, clear=True):
                config = DeploymentConfig.from_env()
                self.assertEqual(config.mode, mode)
                self.assertEqual(config.experiment_timeout_seconds, timeout)
                self.assertEqual(config.experiments_allowed, allowed)
                self.assertEqual(config.bounded, mode != 'Full')
                self.assertEqual(config.experiment_seed_limit, 5)
        with patch.dict('os.environ', {'RESEARCHPILOT_MODE': 'demo'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Full, Limited, or Restricted'):
                DeploymentConfig.from_env()

    def test_routing_uses_the_required_model_in_every_mode(self):
        env = {'OPENAI_API_KEY': 'test-key',
               'RESEARCHPILOT_STRONG_MODEL': 'strong', 'RESEARCHPILOT_WEAK_MODEL': 'weak',
               'RESEARCHPILOT_SYNTHESIS_MODEL': 'old-synthesis',
               'OPENAI_INPUT_COST_PER_MILLION': '1', 'OPENAI_OUTPUT_COST_PER_MILLION': '1'}
        for mode, expected in [('Full', ('strong', 'strong')),
                               ('Limited', ('weak', 'strong')), ('Restricted', ('weak', 'weak'))]:
            with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {**env, 'RESEARCHPILOT_MODE': mode}, clear=True):
                config = DeploymentConfig.from_env()
                routine, synthesis = providers_from_env(config, temp)
                self.assertEqual((routine.name, synthesis.name), tuple('openai:' + m for m in expected))
                inner = getattr(routine, 'provider', routine)
                self.assertEqual(inner.timeout, config.model_timeout_seconds)
                self.assertEqual(inner.planning_timeout, config.planning_timeout_seconds)
                if mode != 'Limited':
                    self.assertIs(routine, synthesis)

    def test_restricted_requires_only_the_weak_model(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {
            'RESEARCHPILOT_MODE': 'Restricted', 'OPENAI_API_KEY': 'test-key',
            'RESEARCHPILOT_WEAK_MODEL': 'weak', 'OPENAI_INPUT_COST_PER_MILLION': '1',
            'OPENAI_OUTPUT_COST_PER_MILLION': '1'}, clear=True):
            routine, synthesis = providers_from_env(DeploymentConfig.from_env(), temp)
            self.assertIs(routine, synthesis)
            self.assertEqual(routine.name, 'openai:weak')

    def test_mode_settings_do_not_use_retired_environment_aliases(self):
        with patch.dict('os.environ', {
            'RESEARCHPILOT_MODE': 'Limited', 'RESEARCHPILOT_DEMO_MAX_STEPS': '99',
            'RESEARCHPILOT_LIMITED_MAX_STEPS': '12'}, clear=True):
            self.assertEqual(DeploymentConfig.from_env().max_steps, 12)
        with patch.dict('os.environ', {
            'RESEARCHPILOT_MODE': 'Restricted', 'RESEARCHPILOT_RESTRICTED_MAX_STEPS': '8'}, clear=True):
            config = DeploymentConfig.from_env()
            self.assertEqual(config.max_steps, 8)
            self.assertFalse(config.experiments_allowed)

    def test_restricted_plans_code_but_never_executes(self):
        class Provider:
            name = 'fixture:weak'
            price = ModelPrice(0, 0)
            price_known = True
            max_output_tokens = 1000
            last_usage = {}
            last_cost_usd = 0

            def generate(self, messages, schema):
                if 'test_type' in schema['properties']:
                    return {'name': 'Finite test', 'baselines': ['control'], 'metrics': ['error'],
                            'seeds': [1, 2], 'algorithm_steps': ['Compare treatment and control'],
                            'parameter_ranges_json': '{}', 'additional_assumptions': [], 'test_type': 'falsifying'}
                return {'code': "print('planned')", 'visualization_code': ''}

        with tempfile.TemporaryDirectory() as temp:
            config = DeploymentConfig(mode='Restricted')
            agent = ResearchAgent(ResearchRepository(Path(temp) / 'state.sqlite'), Path(temp) / 'artifacts',
                                  Provider(), config=config)
            pipeline = ConjecturePipeline(agent)
            pipeline.state = ResearchState('Claim', 'Claim')
            pipeline.state.conjecture = Conjecture('Claim', 'Claim', experimentable=True)
            with patch('researchpilot.conjecture_pipeline.executor_from_env') as factory:
                pipeline.plan_experiments()
                self.assertEqual(len(pipeline.state.experiments_planned), 1)
                self.assertTrue(pipeline.state.experiments_planned[0].code)
                pipeline.execute_experiments()
                pipeline.execute_experiments(design_ids={pipeline.state.experiments_planned[0].id})
                factory.assert_not_called()
            self.assertEqual(pipeline.state.experiments_completed, [])
            self.assertEqual(pipeline.state.experiments_planned[0].execution_status, 'skipped')

    def test_restricted_api_rejects_execution_and_advertises_policy(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {
            'RESEARCHPILOT_MODE': 'Restricted', 'RESEARCHPILOT_PROVIDER': 'deterministic',
            'RESEARCHPILOT_WORKERS': '0', 'RESEARCHPILOT_EXECUTOR': 'docker'}, clear=True):
            with TestClient(create_app(temp)) as client:
                policy = client.get('/deployment/quota').json()
                self.assertEqual(policy['mode'], 'Restricted')
                self.assertFalse(policy['experiments_allowed'])
                response = client.post('/research/missing/experiments/missing/rerun')
                self.assertEqual(response.status_code, 403)
                self.assertIn('planning remains available', response.json()['detail'])
