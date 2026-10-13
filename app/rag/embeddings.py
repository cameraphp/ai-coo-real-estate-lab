"""Local deterministic hashed lexical embedding.

This is feature hashing of content tokens, not a pretrained semantic model. Two
texts are similar only when they share tokens. It exists so the demo is fully
reproducible without a model download, a network call, or an API key.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from app.domain.models import RAG_EMBEDDING_LABEL

MIN_EMBEDDING_DIMENSIONS = 32
MAX_EMBEDDING_DIMENSIONS = 1024

Vector = tuple[float, ...]


class EmbeddingProvider(Protocol):
    label: str
    dimensions: int

    def embed_tokens(self, tokens: Sequence[str]) -> Vector: ...


class DeterministicHashEmbeddingProvider:
    label = RAG_EMBEDDING_LABEL

    def __init__(self, dimensions: int = 128) -> None:
        if not MIN_EMBEDDING_DIMENSIONS <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"dimensions must be between {MIN_EMBEDDING_DIMENSIONS} "
                f"and {MAX_EMBEDDING_DIMENSIONS}."
            )
        self.dimensions = dimensions

    def embed_tokens(self, tokens: Sequence[str]) -> Vector:
        """Signed term-frequency buckets, L2-normalized. Empty input gives a zero vector."""
        buckets = [0.0] * self.dimensions
        for token in tokens:
            # hashlib, never hash(): Python's built-in is randomized per process.
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            buckets[index] += 1.0 if digest[8] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in buckets))
        if norm == 0.0:
            return tuple(buckets)
        return tuple(value / norm for value in buckets)


def cosine_similarity(left: Vector, right: Vector) -> float:
    """Cosine of two L2-normalized vectors, which is their dot product."""
    return sum(a * b for a, b in zip(left, right, strict=True))
