import tempfile
import unittest
from pathlib import Path

from researchpilot.embeddings import HashEmbeddingProvider
from researchpilot.rag import Chunk
from researchpilot.rag_store import PersistentChunkStore


class SemanticFixture:
    model = "semantic-fixture-v1"; dimensions = 3
    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "car" in lowered or "automobile" in lowered: vectors.append([1.0, 0.0, 0.0])
            elif "matrix" in lowered or "linear algebra" in lowered: vectors.append([0.0, 1.0, 0.0])
            else: vectors.append([0.0, 0.0, 1.0])
        return vectors


class EmbeddingStoreTests(unittest.TestCase):
    def test_hash_embeddings_are_deterministic_and_normalized(self):
        provider = HashEmbeddingProvider(64)
        first, second = provider.embed(["randomized projection", "randomized projection"])
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0)

    def test_hybrid_search_recovers_semantic_lexical_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentChunkStore(Path(tmp) / "rag.sqlite"); provider = SemanticFixture()
            chunks = [Chunk("c1", "s", "Transport", "Methods", "An automobile uses four wheels."),
                      Chunk("c2", "s", "Algebra", "Methods", "A matrix represents a linear map.")]
            store.add(chunks, provider)
            reopened = PersistentChunkStore(Path(tmp) / "rag.sqlite")
            result = reopened.hybrid_search("car", provider, 1)
            self.assertEqual(result[0][0].id, "c1")
            self.assertGreater(result[0][2]["semantic"], 0.9)
