import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import io
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from researchpilot.agent.cached import CachedProvider
from researchpilot.agent.tools import default_registry
from researchpilot.api import create_app
from researchpilot.deployment import DeploymentConfig, DailyQuota
from researchpilot.models import ResearchState, Source
from researchpilot.literature import ArxivClient
from researchpilot.pricing import ModelPrice
from researchpilot.reporting import render_bounded_report, render_conjecture_report


class DecisionProvider:
    name = "fake:cheap"
    price = ModelPrice(1, 1)
    price_known = True
    max_output_tokens = 20

    def __init__(self):
        self.calls = 0
        self.last_usage = {}
        self.last_cost_usd = 0

    def generate(self, messages, schema):
        self.calls += 1
        self.last_usage = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
        self.last_cost_usd = self.price.cost(self.last_usage)
        return {"action": "noop", "arguments_json": "{}", "trace_summary": "Tried another step."}


class DeploymentLimitTests(unittest.TestCase):
    def test_quota_migration_preserves_daily_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'demo_quota.sqlite'
            day = int(time.time() // 86400)
            with closing(sqlite3.connect(legacy)) as db:
                db.execute('CREATE TABLE demo_quota (subject TEXT, day INTEGER, used INTEGER NOT NULL, PRIMARY KEY(subject, day))')
                db.execute('INSERT INTO demo_quota VALUES (?, ?, ?)', ('caller', day, 1))
                db.commit()
            path = Path(tmp) / 'quota.sqlite'
            quota = DailyQuota(path, 2, legacy_path=legacy)
            self.assertEqual(quota.remaining('caller'), 1)
            self.assertTrue(quota.take('caller'))
            self.assertFalse(quota.take('caller'))
            self.assertEqual(DailyQuota(path, 2, legacy_path=legacy).remaining('caller'), 0)
            with closing(sqlite3.connect(path)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn('research_quota', tables)
            self.assertNotIn('demo_quota', tables)

    def test_paper_cap_blocks_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ResearchState("question", "objective")
            state.sources = [Source(f"doi:{i}", f"Paper {i}", url=f"https://arxiv.org/pdf/{i}") for i in range(3)]
            state.inspected_papers = ["doi:0", "doi:1"]
            registry = default_registry(state, Path(tmp), config=DeploymentConfig(mode="Limited", max_papers=2))
            with patch("researchpilot.agent.tools.PaperDownloader") as downloader:
                outcome = registry.call("download_paper", {"source_id": "doi:2", "url": "https://arxiv.org/pdf/2"})
            self.assertEqual(outcome.status, "limited")
            downloader.assert_not_called()
            self.assertNotIn("propose_python", registry.names)

    def test_exact_cache_skips_second_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = DecisionProvider()
            cached = CachedProvider(provider, Path(tmp) / "cache.sqlite", "prompt-v1", 3600)
            messages = [{"role": "user", "content": "same query"}]
            schema = {"type": "object"}
            self.assertEqual(cached.generate(messages, schema), cached.generate(messages, schema))
            self.assertEqual(provider.calls, 1)
            self.assertTrue(cached.cache_hit)
            CachedProvider(provider, Path(tmp) / "cache.sqlite", "prompt-v2", 3600).generate(messages, schema)
            self.assertEqual(provider.calls, 2)

    def test_arxiv_search_yields_downloadable_metadata_and_cache(self):
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>http://arxiv.org/abs/2401.12345v1</id><title>Example Paper</title>
          <summary>Measured result.</summary><published>2024-01-15T00:00:00Z</published>
          <author><name>A. Author</name></author></entry></feed>'''
        with tempfile.TemporaryDirectory() as tmp:
            client = ArxivClient(Path(tmp))
            with patch("urllib.request.urlopen", return_value=io.BytesIO(xml)) as call:
                first = client.search("example", 1)
                second = client.search("example", 1)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(first, second)
            self.assertTrue(client.last_cache_hit)
            self.assertEqual(first[0].url, "https://arxiv.org/pdf/2401.12345v1")

    def test_compact_report_keeps_references(self):
        state = ResearchState("A research question", "objective")
        state.sources = [Source("arxiv:1", "Paper", url="https://arxiv.org/pdf/1")]
        state.conclusions = ["Finding. " * 400]
        report = render_bounded_report(state, 250)
        self.assertLessEqual(len(report), 1000)
        self.assertIn("# References", report)
        self.assertIn("arxiv:1", report)

    def test_reports_show_provider_cost_and_unknown_pricing(self):
        state = ResearchState("A research question", "objective")
        state.model_calls = 1
        state.input_tokens = 120
        state.output_tokens = 30
        state.token_usage = 150
        state.estimated_cost_usd = 0.00042
        state.llm_calls = [{"stage": "planning", "model": "openai:example",
                            "input_tokens": 120, "output_tokens": 30,
                            "estimated_cost_usd": 0.00042, "cache_hit": False}]
        full = render_conjecture_report(state)
        compact = render_bounded_report(state, 250)
        self.assertIn("# Usage", full)
        self.assertIn("$0.000420 USD estimated", full)
        self.assertIn("planning (openai:example): 120 input, 30 output tokens", full)
        self.assertIn("$0.000420 USD estimated", compact)
        state.cost_estimate_complete = False
        self.assertIn("Unavailable (model prices were not configured", render_conjecture_report(state))
        self.assertNotIn("$0.000420", render_bounded_report(state, 250))

    def test_cached_model_response_is_visible_without_live_request(self):
        state = ResearchState("A research question", "objective")
        state.llm_calls = [{"stage": "interpretation", "model": "openai:example",
                            "input_tokens": 0, "output_tokens": 0,
                            "estimated_cost_usd": 0.0, "cache_hit": True}]
        report = render_conjecture_report(state)
        self.assertIn("Reused model responses: 1", report)
        self.assertIn("responses reused from cache", report)

    def test_anonymous_daily_quota(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                "RESEARCHPILOT_MODE": "Limited", "RESEARCHPILOT_PROVIDER": "deterministic",
                "RESEARCHPILOT_WORKERS": "0", "RESEARCHPILOT_LIMITED_RUNS_PER_DAY": "2"}):
            with TestClient(create_app(tmp)) as client:
                initial_quota = client.get("/deployment/quota").json()
                responses = [client.post("/research", json={"question": "Find papers on optimization.",
                             "execute": True}) for _ in range(3)]
                statuses = [response.status_code for response in responses]
                final_quota = client.get("/deployment/quota").json()
            self.assertEqual(statuses, [200, 200, 429])
            self.assertEqual((initial_quota["remaining"], initial_quota["limit"]), (2, 2))
            self.assertEqual(final_quota["remaining"], 0)
            self.assertTrue(final_quota["enabled"])
            self.assertGreater(final_quota["resets_at"], time.time())
            self.assertIn("2 research runs per UTC day", responses[-1].json()["detail"]["message"])
            self.assertGreater(int(responses[-1].headers["retry-after"]), 0)


if __name__ == "__main__":
    unittest.main()
