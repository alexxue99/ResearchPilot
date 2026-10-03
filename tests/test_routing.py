import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from researchpilot.agent.routing import providers_from_env
from researchpilot.deployment import DeploymentConfig
from researchpilot.storage import ResearchRepository
from researchpilot.worker import research_agent_factory


class ProviderRoutingTests(unittest.TestCase):
    def test_standalone_worker_loads_workspace_env(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, ".env.local").write_text(
                "RESEARCHPILOT_PROVIDER=openai\nOPENAI_API_KEY=test-key\nRESEARCHPILOT_STRONG_MODEL=test-model\n",
                encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                repo = ResearchRepository(Path(temp) / "state.sqlite")
                agent = research_agent_factory(temp)(repo, SimpleNamespace(lease_token="test"))
                try:
                    self.assertEqual(agent.provider.name, "openai:test-model")
                finally:
                    agent.trace_exporter.close()

    def test_configured_credentials_enable_provider_by_default(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key", "RESEARCHPILOT_STRONG_MODEL": "test-model"}, clear=True
        ):
            routine, synthesis = providers_from_env(DeploymentConfig(), Path(temp))
            self.assertEqual(routine.name, "openai:test-model")
            self.assertIs(synthesis, routine)

    def test_explicit_deterministic_mode_disables_provider(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key", "RESEARCHPILOT_STRONG_MODEL": "test-model",
                           "RESEARCHPILOT_PROVIDER": "deterministic"}, clear=True
        ):
            self.assertEqual(providers_from_env(DeploymentConfig(), Path(temp)), (None, None))

    def test_key_without_model_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "RESEARCHPILOT_STRONG_MODEL"):
                providers_from_env(DeploymentConfig(), Path(temp))

    def test_full_does_not_fall_back_to_retired_model_variable(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "retired-model"}, clear=True
        ):
            config = DeploymentConfig.from_env()
            self.assertEqual(config.routine_model, "")
            self.assertEqual(config.synthesis_model, "")
            with self.assertRaisesRegex(ValueError, "RESEARCHPILOT_STRONG_MODEL"):
                providers_from_env(config, Path(temp))

    def test_limited_routine_has_room_for_structured_design(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"OPENAI_API_KEY": "test-key", "RESEARCHPILOT_STRONG_MODEL": "test-model",
                           "OPENAI_INPUT_COST_PER_MILLION": "1",
                           "OPENAI_OUTPUT_COST_PER_MILLION": "1"}, clear=True
        ):
            routine, _ = providers_from_env(DeploymentConfig(mode="Limited", routine_model="test-model",
                                                       synthesis_model="test-model"), Path(temp))
            self.assertGreater(routine.provider.max_output_tokens, 450)
