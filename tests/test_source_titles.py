import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from researchpilot.agent.tools import default_registry
from researchpilot.conjecture_pipeline import ConjecturePipeline, deduplicate_sources
from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.storage import ResearchRepository
from researchpilot.literature import ArxivClient, CrossrefClient, has_article_title
from researchpilot.models import Conjecture, ResearchState, Source


class SourceTitleTests(unittest.TestCase):
    def test_legacy_arxiv_lookup_accepts_returned_version_and_preserves_full_id(self):
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>http://arxiv.org/abs/math/0702226v1</id>
        <title>A randomized Kaczmarz algorithm with exponential convergence</title>
        </entry></feed>'''
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', return_value=io.BytesIO(xml)):
            source = ArxivClient(tmp).lookup('math/0702226')
            self.assertEqual(source.arxiv_id, 'math/0702226')
            self.assertTrue(has_article_title(source))

    def test_exact_doi_lookup_and_cache(self):
        record = {'message': {'DOI': '10.1137/20M1350947',
                             'title': ['Randomized Kaczmarz Converges Along Small Singular Vectors']}}
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(record).encode())) as request:
            client = CrossrefClient(tmp)
            first = client.lookup('10.1137/20m1350947')
            self.assertFalse(client.last_cache_hit)
            self.assertEqual(first, client.lookup('10.1137/20m1350947'))
            self.assertTrue(client.last_cache_hit)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(has_article_title(first))

    def test_url_placeholder_is_replaced_by_scholarly_metadata(self):
        state = ResearchState('Question', 'Objective')
        state.sources = [Source('arxiv:math/0702226', 'https://arxiv.org/abs/math/0702226',
                                arxiv_id='math/0702226', url='https://arxiv.org/pdf/math/0702226')]
        with tempfile.TemporaryDirectory() as tmp, patch('researchpilot.literature.ArxivClient.lookup',
            return_value=Source('arxiv:math/0702226', 'Actual article title', arxiv_id='math/0702226')):
            registry = default_registry(state, Path(tmp), cache_root=Path(tmp))
            outcome = registry.call('source_metadata', {'source_id': 'arxiv:math/0702226'})
            self.assertEqual(outcome.status, 'completed')
            self.assertEqual(state.sources[0].title, 'Actual article title')
            self.assertEqual(state.sources[0].id, 'arxiv:math/0702226')

    def test_metadata_failures_include_network_diagnostics(self):
        cases = [
            (Source('arxiv:math/0702226', 'arxiv:math/0702226', arxiv_id='math/0702226'),
             TimeoutError('The read operation timed out'), 'arxiv', None, 'TimeoutError'),
            (Source('doi:10.1137/20m1350947', 'doi:10.1137/20m1350947', doi='10.1137/20m1350947'),
             HTTPError('https://api.crossref.org/works/test', 503, 'Service Unavailable', {}, io.BytesIO()),
             'crossref', 503, 'HTTPError'),
            (Source('arxiv:math/0702226', 'arxiv:math/0702226', arxiv_id='math/0702226'),
             URLError(TimeoutError('connection timed out')), 'arxiv', None, 'TimeoutError'),
        ]
        for source, error, service, status, cause in cases:
            if isinstance(error, HTTPError):
                self.addCleanup(error.close)
            with self.subTest(service=service, error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                state = ResearchState('Question', 'Objective')
                state.sources = [source]
                registry = default_registry(state, Path(tmp), cache_root=Path(tmp))
                with patch('urllib.request.urlopen', side_effect=error), \
                     patch('researchpilot.agent.tools.time.perf_counter', side_effect=[100, 112.1]), \
                     self.assertLogs('researchpilot.agent.tools', level='WARNING') as logs:
                    outcome = registry.call('source_metadata', {'source_id': source.id})
                self.assertEqual(outcome.status, 'failed')
                self.assertEqual(outcome.data['source_id'], source.id)
                self.assertEqual(outcome.data['service'], service)
                self.assertEqual(outcome.data['timeout_seconds'], 12)
                self.assertEqual(outcome.data['elapsed_ms'], 12100)
                self.assertFalse(outcome.data['cache_hit'])
                self.assertEqual(outcome.data['error_type'], type(error).__name__)
                self.assertEqual(outcome.data['cause_type'], cause)
                self.assertEqual(outcome.data['http_status'], status)
                self.assertIn(source.id, outcome.summary)
                self.assertIn('timeout=12s', outcome.summary)
                self.assertIn(outcome.data['request_url'], logs.output[0])
                self.assertIsNotNone(logs.records[0].exc_info)

    def test_metadata_timeout_diagnostics_persist_in_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = ResearchRepository(Path(tmp) / 'state.sqlite')
            agent = ResearchAgent(repo, Path(tmp) / 'artifacts')
            state = ResearchState('Question', 'Objective')
            state.conjecture = Conjecture('Question', 'Question')
            state.sources = [Source('arxiv:math/0702226', 'arxiv:math/0702226', arxiv_id='math/0702226')]
            pipeline = ConjecturePipeline(agent)
            pipeline.state = state
            with patch('urllib.request.urlopen', side_effect=TimeoutError('The read operation timed out')), \
                 patch('researchpilot.agent.tools.time.perf_counter', side_effect=[100, 112]), \
                 self.assertLogs('researchpilot.agent.tools', level='WARNING'):
                pipeline.extract_evidence()
            repo.save(state)
            saved = repo.get(state.id)
            event = next(e for e in saved.trace if e.action == 'source_metadata')
            self.assertEqual(event.status, 'failed')
            self.assertEqual(event.inputs, {'source_id': 'arxiv:math/0702226'})
            self.assertEqual(event.latency_ms, 12000)
            self.assertEqual(event.outputs['request_url'],
                             'https://export.arxiv.org/api/query?id_list=math%2F0702226')
            self.assertEqual(event.outputs['error_message'], 'The read operation timed out')

    def test_deduplication_keeps_real_title_even_when_placeholder_record_wins(self):
        placeholder = Source('arxiv:1', 'https://arxiv.org/abs/1', arxiv_id='1')
        paper = Source('arxiv:1', 'Article title', arxiv_id='1')
        result = deduplicate_sources([placeholder, paper], attached_ids=['arxiv:1'])
        self.assertEqual(result[0].title, 'Article title')
