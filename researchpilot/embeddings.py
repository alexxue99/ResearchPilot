from __future__ import annotations

import hashlib
import math
import re
import os
from typing import Protocol


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """Deterministic, dependency-free embedding fallback.

    Feature hashing over words and character n-grams provides a stable vector retrieval
    baseline. It captures morphological similarity, not model-level semantic equivalence;
    production deployments can supply any semantic EmbeddingProvider implementation.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32: raise ValueError("embedding dimensions must be at least 32")
        self.dimensions = dimensions; self.model = f"hash-ngram-v1:{dimensions}"

    @staticmethod
    def _features(text: str) -> list[str]:
        words = re.findall(r"[a-z][a-z0-9_-]+", text.lower())
        features = [f"w:{word}" for word in words]
        for word in words:
            padded = f"^{word}$"
            features.extend(f"c:{padded[index:index+3]}" for index in range(max(0, len(padded) - 2)))
        return features

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for feature in self._features(text):
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                value = int.from_bytes(digest, "little")
                vector[value % self.dimensions] += -1.0 if value >> 63 else 1.0
            length = math.sqrt(sum(item * item for item in vector))
            vectors.append([item / length for item in vector] if length else vector)
        return vectors


class SentenceTransformerEmbeddingProvider:
    """Optional local semantic model; never downloads weights implicitly."""

    def __init__(self, model_path: str, revision: str) -> None:
        if not model_path or not revision:
            raise ValueError("semantic embeddings require a model path and explicit revision")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("Install the 'embeddings' extra to use semantic embeddings") from exc
        self._encoder = SentenceTransformer(model_path, revision=revision, device="cpu",
                                             local_files_only=True, trust_remote_code=False)
        self.dimensions = int(self._encoder.get_sentence_embedding_dimension())
        self.model = f"sentence-transformer:{model_path}@{revision}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._encoder.encode(texts, batch_size=32, normalize_embeddings=True,
                                       show_progress_bar=False).tolist()
        if len(vectors) != len(texts) or any(len(v) != self.dimensions or
                not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError("semantic model returned invalid embeddings")
        return vectors


def embedding_provider_from_env() -> EmbeddingProvider:
    provider = os.environ.get("RESEARCHPILOT_EMBEDDINGS", "hash")
    if provider == "hash":
        return HashEmbeddingProvider()
    if provider == "sentence-transformer":
        return SentenceTransformerEmbeddingProvider(os.environ.get("RESEARCHPILOT_EMBEDDING_MODEL", ""),
            os.environ.get("RESEARCHPILOT_EMBEDDING_REVISION", ""))
    raise ValueError(f"Unknown embedding provider: {provider}")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right): raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left)); right_norm = math.sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm) if left_norm and right_norm else 0.0
