import io
import json
import math
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from researchpilot.agent.orchestrator import ResearchAgent
from researchpilot.agent.tools import default_registry
from researchpilot.downloads import PaperDownloader
from researchpilot.embeddings import HashEmbeddingProvider, SentenceTransformerEmbeddingProvider
from researchpilot.executor import DockerPythonExecutor, executor_from_env
from researchpilot.ingestion import PaperIngestor
from researchpilot.models import ResearchState, Source
from researchpilot.pricing import ModelPrice, price_from_env
from researchpilot.rag import Chunk, semantic_chunks
from researchpilot.rag_store import PersistentChunkStore
from researchpilot.storage import ResearchRepository


class RetrievalRegressionTests(unittest.TestCase):
    def test_chunker_preserves_first_line_and_bounds_long_paragraphs(self):
        text = "Important first sentence.\nSecond sentence."
        chunks = semantic_chunks("s", "title", text)
        self.assertIn("Important first sentence", chunks[0].text)
        self.assertTrue(all(len(c.text) <= 20 for c in semantic_chunks("s", "t", "x" * 250, max_chars=20)))

    def test_theorem_and_equation_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.md"
            path.write_text("# Results\nTheorem 1. For x > 0, x squared is positive.\n\n$$x^2 > 0$$", encoding="utf-8")
            parsed = PaperIngestor().ingest(path, Source("s", "Paper"))
            self.assertEqual({b["kind"] for b in parsed.math_blocks}, {"statement", "equation"})
            self.assertTrue(any("Theorem 1." in c.text for c in parsed.chunks))

    def test_updated_text_invalidates_vectors_from_other_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentChunkStore(Path(tmp) / "rag.sqlite")
            a, b = HashEmbeddingProvider(32), HashEmbeddingProvider(64)
            original = Chunk("c", "s", "Title", "Results", "old text")
            store.add([original], a); store.add([original], b)
            store.add([Chunk("c", "s", "Title", "Results", "new text")], a)
            self.assertEqual(store.hybrid_search("old", b), [])

    def test_bad_vectors_leave_database_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentChunkStore(Path(tmp) / "rag.sqlite")
            bad = SimpleNamespace(model="bad", dimensions=32, embed=lambda texts: [[math.nan] * 32])
            with self.assertRaises(ValueError): store.add([Chunk("c", "s", "t", "r", "text")], bad)
            self.assertEqual(store.count(), 0)

    def test_semantic_adapter_uses_local_weights_and_revision(self):
        encoder = MagicMock()
        encoder.get_sentence_embedding_dimension.return_value = 2
        encoder.encode.return_value.tolist.return_value = [[0.6, 0.8]]
        constructor = MagicMock(return_value=encoder)
        with patch.dict("sys.modules", {"sentence_transformers": SimpleNamespace(SentenceTransformer=constructor)}):
            provider = SentenceTransformerEmbeddingProvider("cached-model", "abc123")
            self.assertEqual(provider.embed(["hello"]), [[0.6, 0.8]])
        self.assertTrue(constructor.call_args.kwargs["local_files_only"])
        self.assertFalse(constructor.call_args.kwargs["trust_remote_code"])

    def test_retrieval_is_scoped_and_claim_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentChunkStore(Path(tmp) / "rag.sqlite")
            embedder = HashEmbeddingProvider()
            store.add([Chunk("a", "s", "Paper", "Results", "Gradient variance decreases with batch size."),
                       Chunk("b", "other", "Other", "Results", "Gradient variance decreases with batch size.")], embedder)
            state = ResearchState("q", "o", sources=[Source("s", "Paper")])
            tools = default_registry(state, tmp, chunk_store=store, embedder=embedder)
            outcome = tools.call("retrieve_passages", {"query": "gradient variance"})
            self.assertEqual([p["id"] for p in outcome.data["passages"]], ["a"])
            outcome = tools.call("record_literature_claim", {"claim": "Gradient variance decreases with batch size.", "source_id": "s", "chunk_id": "a"})
            self.assertEqual(outcome.status, "completed")
            self.assertTrue(state.evidence[0].support)
            bad = tools.call("record_literature_claim", {"claim": "variance", "source_id": "s", "chunk_id": "b"})
            self.assertEqual(bad.status, "invalid")


class DownloadTests(unittest.TestCase):
    def target(self, root):
        return PaperDownloader(root, {"papers.example"}, max_bytes=100)

    def test_private_addresses_and_unapproved_urls_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for url in ("http://papers.example/a", "https://elsewhere.example/a", "https://user:pw@papers.example/a"):
                with self.assertRaises(ValueError): self.target(tmp)._target(url)
            with patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("127.0.0.1", 443))]):
                with self.assertRaises(ValueError): self.target(tmp)._target("https://papers.example/a")

    def _response(self, body, status=200, headers=None):
        response = MagicMock()
        response.status = status
        response.getheader.side_effect = lambda name: (headers or {}).get(name)
        response.read1.side_effect = io.BytesIO(body).read
        return response

    def test_download_pins_address_and_records_digest(self):
        with tempfile.TemporaryDirectory() as tmp, patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.216.34", 443))]), patch("researchpilot.downloads._PinnedHTTPSConnection") as connection:
            connection.return_value.getresponse.return_value = self._response(b"%PDF-1.7\nexample")
            path = self.target(tmp).download("https://papers.example/a")
            self.assertTrue(path.is_file())
            self.assertTrue(path.with_suffix(".provenance.json").is_file())
            self.assertEqual(connection.call_args.args[:2], ("papers.example", "93.184.216.34"))

    def test_redirect_revalidates_destination(self):
        with tempfile.TemporaryDirectory() as tmp, patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.216.34", 443))]), patch("researchpilot.downloads._PinnedHTTPSConnection") as connection:
            connection.return_value.getresponse.return_value = self._response(b"", 302, {"Location": "https://127.0.0.1/private"})
            with self.assertRaises(ValueError): self.target(tmp).download("https://papers.example/a")
            self.assertEqual(connection.call_count, 1)

    def test_oversize_and_non_pdf_rejected_without_artifacts(self):
        for body in (b"%PDF-" + b"x" * 100, b"<html>login</html>"):
            with tempfile.TemporaryDirectory() as tmp, patch.object(PaperDownloader, "_target", return_value=(SimpleNamespace(hostname="papers.example", path="/a", query=""), "93.184.216.34")), patch("researchpilot.downloads._PinnedHTTPSConnection") as connection:
                connection.return_value.getresponse.return_value = self._response(body)
                with self.assertRaises(ValueError): self.target(tmp).download("https://papers.example/a")
                self.assertEqual(list(Path(tmp).iterdir()), [])


class ExecutionRegressionTests(unittest.TestCase):
    def test_code_proposal_never_executes_or_accepts_claimed_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ResearchState("q", "o")
            tools = default_registry(state, tmp)
            design = tools.call("design_experiment", {"name": "study", "hypothesis": "test"})
            code = "from pathlib import Path\nPath('unexpected.txt').write_text('ran')"
            outcome = tools.call("propose_python", {"design_id": design.data["design_id"], "code": code, "metrics": {"accuracy": 100}})
            self.assertEqual(outcome.status, "proposed")
            self.assertEqual(state.experiments_completed, [])
            self.assertEqual(state.experiments_planned[0].code, code)
            self.assertFalse((Path(tmp) / 'unexpected.txt').exists())
            self.assertEqual(tools.call("execute_python", {"design_id": design.data["design_id"], "code": "print(42)"}).status, "unsupported")

    def test_non_object_tool_arguments_are_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = default_registry(ResearchState("q", "o"), tmp)
            self.assertEqual(tools.call("propose_python", ["code", "design_id"]).status, "invalid")

    def test_docker_timeout_removes_only_named_container(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as run:
            run.side_effect = [subprocess.TimeoutExpired("docker", 1), SimpleNamespace(returncode=0)]
            result = DockerPythonExecutor(tmp, timeout_seconds=1).run("while True: pass", "trial")
            command = run.call_args_list[0].args[0]
            name = command[command.index("--name") + 1]
            self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "--force", name])
            self.assertEqual(result.status, "timeout")
            self.assertIn("never", command)

    def test_executor_configuration_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"RESEARCHPILOT_EXECUTOR": "invalid"}):
            with self.assertRaises(ValueError): executor_from_env(tmp)


class ScientificRegressionTests(unittest.TestCase):
    def test_rk_alias_does_not_trigger_hardcoded_spectrum_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = ResearchAgent(ResearchRepository(Path(tmp) / "db.sqlite"), Path(tmp) / "artifacts")
            state = agent.run(agent.propose("Plot convergence of RK on matrices with three singular-value distributions."))
            self.assertEqual(state.experiments_completed, [])
            self.assertEqual(state.sources, [])
            self.assertEqual(state.artifacts, [])


class PricingTests(unittest.TestCase):
    def test_cached_input_and_invalid_rates(self):
        price = ModelPrice(2, 8, .5)
        self.assertAlmostEqual(price.cost({"input_tokens": 1000, "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 500}}), .00205)
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError): ModelPrice(value, 1)

    def test_versioned_table_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text(json.dumps({"as_of": "2026-09-14", "currency": "USD", "providers": {
                "openai": {"fixture-model": {"input_per_million": 2, "output_per_million": 8}}}}))
            with patch.dict(os.environ, {"RESEARCHPILOT_PRICE_TABLE": str(path)}, clear=True):
                self.assertEqual(price_from_env("openai", "fixture-model"), ModelPrice(2, 8))
                self.assertIsNone(price_from_env("openai", "unknown"))
                with patch.dict(os.environ, {"OPENAI_INPUT_COST_PER_MILLION": "1", "OPENAI_OUTPUT_COST_PER_MILLION": "3"}):
                    self.assertEqual(price_from_env("openai", "fixture-model"), ModelPrice(1, 3))


@unittest.skipUnless(os.environ.get("RESEARCHPILOT_TEST_DOCKER") == "1", "opt-in Docker runtime validation")
class DockerRuntimeTests(unittest.TestCase):
    def test_nonroot_readonly_network_and_artifacts(self):
        code = """import os, socket
from pathlib import Path
assert os.getuid() != 0
try:
    Path('/forbidden').write_text('x')
except OSError:
    pass
else:
    raise AssertionError('root filesystem was writable')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network was accessible')
Path('result.txt').write_text('isolation smoke test passed')
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = DockerPythonExecutor(tmp).run(code, "smoke")
            self.assertEqual(result.status, "completed", result.stderr)
            self.assertTrue(any(p.endswith("result.txt") for p in result.artifacts))
