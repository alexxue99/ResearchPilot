import io
import json
import os
import tempfile
import unittest
from pathlib import Path
import urllib.error
from unittest.mock import patch

from researchpilot.agent.provider import ModelLimitError, OpenAIResponsesProvider, ProviderError


class _Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class ProviderTests(unittest.TestCase):
    def test_planning_timeout_is_configurable_through_cache(self):
        from researchpilot.agent.cached import CachedProvider
        response = b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}]}'
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "OPENAI_API_KEY": "secret", "RESEARCHPILOT_STRONG_MODEL": "test-model",
                "RESEARCHPILOT_PLANNING_TIMEOUT_SECONDS": "240"}, clear=True), \
             patch("urllib.request.urlopen", return_value=_Response(response)) as request:
            raw = OpenAIResponsesProvider.from_env()
            provider = CachedProvider(raw, Path(temp) / "cache.sqlite", "v1", 3600)
            messages, schema = [{"role": "user", "content": "Plan"}], {"type": "object"}
            provider.generate_plan(messages, schema)
            self.assertEqual(request.call_args.kwargs["timeout"], 240)
            provider.generate_plan(messages, schema)
            self.assertTrue(provider.cache_hit)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(raw.timeout, 60)

    def test_planning_timeout_rejects_invalid_values(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "planning_timeout"):
                OpenAIResponsesProvider("secret", "test-model", planning_timeout=value)

    def test_transport_failure_records_safe_timeout_and_retry_details(self):
        provider = OpenAIResponsesProvider("secret", "test-model", max_retries=2)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(TimeoutError("secret detail"))), \
             patch("time.sleep"), patch("time.monotonic", side_effect=[10, 191]):
            with self.assertRaises(ProviderError) as raised:
                provider.generate([{"role": "user", "content": "Plan"}], {"type": "object"})
        self.assertIn("3 attempts: timeout", str(raised.exception))
        self.assertIn("timeout=60s; elapsed=181.0s", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_code_timeout_is_configurable_and_forwarded_through_cache(self):
        from researchpilot.agent.cached import CachedProvider
        response = b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}]}'
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "OPENAI_API_KEY": "secret", "RESEARCHPILOT_STRONG_MODEL": "test-model",
                "RESEARCHPILOT_CODE_TIMEOUT_SECONDS": "420"}, clear=True), \
             patch("urllib.request.urlopen", side_effect=[_Response(response), _Response(response)]) as request:
            raw = OpenAIResponsesProvider.from_env()
            provider = CachedProvider(raw, Path(temp) / "cache.sqlite", "v1", 3600)
            schema = {"type": "object"}
            provider.generate_code([{"role": "user", "content": "Write code"}], schema)
            self.assertEqual(request.call_args.kwargs["timeout"], 420)
            provider.generate([{"role": "user", "content": "Plan"}], schema)
            self.assertEqual(request.call_args.kwargs["timeout"], 60)
            provider.generate_code([{"role": "user", "content": "Write code"}], schema)
            self.assertTrue(provider.cache_hit)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(raw.timeout, 60)

    def test_invalid_code_timeout_is_rejected(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "code_timeout"):
                OpenAIResponsesProvider("secret", "test-model", code_timeout=value)

    def test_cached_search_preserves_provenance_without_reusing_plain_generation(self):
        from researchpilot.agent.cached import CachedProvider
        messages, schema = [{"role": "user", "content": "Find a theorem"}], {"type": "object"}
        plain = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "{}"}]}]}
        searched = {"status": "completed", "output": [
            {"type": "web_search_call", "action": {"type": "search", "sources": [
                {"url": "https://example.org/theorem", "title": "Original theorem"}]}},
            *plain["output"]]}
        with tempfile.TemporaryDirectory() as temp, patch("urllib.request.urlopen", side_effect=[
                _Response(json.dumps(plain).encode()), _Response(json.dumps(searched).encode())]) as request:
            provider = CachedProvider(OpenAIResponsesProvider("secret", "test-model"),
                                      Path(temp) / "cache.sqlite", "v1", 3600)
            self.assertEqual(provider.generate(messages, schema), {})
            self.assertFalse(provider.will_hit(messages, schema, search_options={"max_tool_calls": 2}))
            first = provider.generate_with_search(messages, schema, max_tool_calls=2)
            self.assertFalse(provider.cache_hit)
            self.assertEqual(provider.last_web_search_calls, 1)
            self.assertTrue(provider.will_hit(messages, schema, search_options={"max_tool_calls": 2}))
            second = provider.generate_with_search(messages, schema, max_tool_calls=2)
            self.assertEqual(first, second)
            self.assertEqual(first["_web_search_sources"][0]["url"], "https://example.org/theorem")
            self.assertTrue(provider.cache_hit)
            self.assertEqual(provider.last_web_search_calls, 0)
            self.assertEqual(provider.last_cost_usd, 0)
            self.assertEqual(request.call_count, 2)

    def test_search_cost_is_reserved_in_bounded_budget(self):
        from researchpilot.agent.accounting import can_call
        from researchpilot.deployment import DeploymentConfig
        from researchpilot.models import ResearchState
        provider = OpenAIResponsesProvider("secret", "test-model", max_output_tokens=100,
                                           input_cost_per_million=1, output_cost_per_million=1)
        config = DeploymentConfig(mode="Limited", max_cost_usd=0.01)
        state = ResearchState("claim", "claim")
        messages, schema = [{"role": "user", "content": "Find a source"}], {"type": "object"}
        self.assertTrue(can_call(state, provider, messages, config, schema))
        self.assertFalse(can_call(state, provider, messages, config, schema, search_options={"max_tool_calls": 2}))

    def test_search_enabled_generation_retains_tool_sources_and_citations(self):
        output = {"argument_searches": [{"observation": "A mathematical observation"}]}
        response = {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 4},
                    "output": [
                        {"type": "web_search_call", "action": {"type": "search", "sources": [
                            {"url": "https://arxiv.org/abs/2401.12345", "title": "Original theorem"}]}},
                        {"type": "web_search_call", "action": {"type": "open_page"}},
                        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(output),
                            "annotations": [{"type": "url_citation", "url": "https://example.org/theorem",
                                             "title": "Theorem statement"}]}]}]}
        provider = OpenAIResponsesProvider("secret", "test-model", input_cost_per_million=2,
                                           output_cost_per_million=8)
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(response).encode())) as call:
            result = provider.generate_with_search([{"role": "user", "content": "Corroborate this observation"}],
                                                   {"type": "object"}, max_tool_calls=2)
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent["tools"], [{"type": "web_search"}])
        self.assertEqual(sent["include"], ["web_search_call.action.sources"])
        self.assertEqual(sent["max_tool_calls"], 2)
        self.assertEqual(result["argument_searches"], output["argument_searches"])
        self.assertEqual(len(result["_web_search_sources"]), 2)
        self.assertEqual(provider.last_web_search_calls, 2)
        self.assertAlmostEqual(provider.last_cost_usd, 0.010052)
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(response).encode())) as call:
            provider.generate([{"role": "user", "content": "Ordinary stage"}], {"type": "object"})
        self.assertNotIn("tools", json.loads(call.call_args.args[0].data))

    def test_structured_response_and_usage(self):
        response = {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"plan\":[\"search\"]}"}]}]}
        provider = OpenAIResponsesProvider("secret", "test-model",
                                           input_cost_per_million=2.0,
                                           output_cost_per_million=8.0)
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(response).encode())) as call:
            result = provider.generate([{"role": "system", "content": "ground claims"}, {"role": "user", "content": "plan"}],
                                       {"type": "object", "properties": {"plan": {"type": "array", "items": {"type": "string"}}}, "required": ["plan"], "additionalProperties": False})
        self.assertEqual(result, {"plan": ["search"]})
        self.assertEqual(provider.last_usage["total_tokens"], 14)
        self.assertAlmostEqual(provider.last_cost_usd, 0.000052)
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent["text"]["format"]["type"], "json_schema")
        self.assertTrue(sent["text"]["format"]["strict"])
        self.assertNotIn("secret", json.dumps(sent))

    def test_missing_output_is_explicit_failure(self):
        provider = OpenAIResponsesProvider("secret", "test-model", max_retries=0)
        with patch("urllib.request.urlopen", return_value=_Response(b'{"status":"completed","output":[]}')):
            with self.assertRaises(ProviderError): provider.generate([{"role": "user", "content": "x"}], {"type": "object"})

    def test_incomplete_response_retains_usage_and_reason(self):
        response = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 100, "output_tokens": 4000, "total_tokens": 4100},
                    "output": []}
        provider = OpenAIResponsesProvider("secret", "test-model", input_cost_per_million=1,
                                           output_cost_per_million=2)
        with patch("urllib.request.urlopen", return_value=_Response(json.dumps(response).encode())):
            with self.assertRaisesRegex(ProviderError, "max_output_tokens"):
                provider.generate([{"role": "user", "content": "x"}], {"type": "object"})
        self.assertEqual(provider.last_usage["output_tokens"], 4000)
        self.assertGreater(provider.last_cost_usd, 0)

    def test_rate_limit_is_identified_without_exposing_provider_body(self):
        provider = OpenAIResponsesProvider("secret", "test-model", max_retries=0)
        error = urllib.error.HTTPError(provider.endpoint, 429, "rate limited", {},
                                       io.BytesIO(b'{"error":{"message":"private detail"}}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(ModelLimitError, "rate or usage limit") as raised:
                provider.generate([{"role": "user", "content": "x"}], {"type": "object"})
        self.assertNotIn("private detail", str(raised.exception))
        error.close()

    def test_context_limit_is_identified(self):
        provider = OpenAIResponsesProvider("secret", "test-model", max_retries=0)
        error = urllib.error.HTTPError(provider.endpoint, 400, "bad request", {},
                                       io.BytesIO(b'{"error":{"code":"context_length_exceeded"}}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(ModelLimitError, "context limit"):
                provider.generate([{"role": "user", "content": "x"}], {"type": "object"})
        error.close()

    def test_environment_requires_explicit_model(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "key"}, clear=True):
            with self.assertRaises(ValueError): OpenAIResponsesProvider.from_env()
