"""Server-owned RAG settings. Every value is bounded and validated at startup.

None of these are secrets, and none of them can be set by an API client.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.models import RAG_TOP_K_LIMIT, RerankerKind
from app.rag.chunking import (
    MAX_CHUNK_OVERLAP_TOKENS,
    MAX_CHUNK_SIZE_TOKENS,
    MIN_CHUNK_SIZE_TOKENS,
    ChunkingConfig,
)
from app.rag.embeddings import MAX_EMBEDDING_DIMENSIONS, MIN_EMBEDDING_DIMENSIONS
from app.rag.grounding import GroundingPolicy

DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "rag" / "synthetic_documents.v1.json"
)


@dataclass(frozen=True)
class RagConfig:
    corpus_path: Path
    chunking: ChunkingConfig
    embedding_dimensions: int
    reranker: RerankerKind
    max_top_k: int
    grounding: GroundingPolicy

    def __post_init__(self) -> None:
        if not 1 <= self.max_top_k <= RAG_TOP_K_LIMIT:
            raise ValueError(f"max_top_k must be between 1 and {RAG_TOP_K_LIMIT}.")


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    rag_corpus_path: Path | None = None
    rag_chunk_size_tokens: int = Field(
        default=80, ge=MIN_CHUNK_SIZE_TOKENS, le=MAX_CHUNK_SIZE_TOKENS
    )
    rag_chunk_overlap_tokens: int = Field(default=20, ge=0, le=MAX_CHUNK_OVERLAP_TOKENS)
    rag_embedding_dimensions: int = Field(
        default=128, ge=MIN_EMBEDDING_DIMENSIONS, le=MAX_EMBEDDING_DIMENSIONS
    )
    rag_reranker: RerankerKind = RerankerKind.LEXICAL
    rag_max_top_k: int = Field(default=RAG_TOP_K_LIMIT, ge=1, le=RAG_TOP_K_LIMIT)
    rag_min_evidence_score: float = Field(default=0.15, ge=0.0, le=1.0)
    rag_min_query_term_coverage: float = Field(default=0.7, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _overlap_is_smaller_than_chunk(self) -> RagSettings:
        if self.rag_chunk_overlap_tokens >= self.rag_chunk_size_tokens:
            raise ValueError("RAG_CHUNK_OVERLAP_TOKENS must be smaller than RAG_CHUNK_SIZE_TOKENS.")
        return self

    def rag_config(self) -> RagConfig:
        return RagConfig(
            corpus_path=self.rag_corpus_path or DEFAULT_CORPUS_PATH,
            chunking=ChunkingConfig(
                chunk_size_tokens=self.rag_chunk_size_tokens,
                chunk_overlap_tokens=self.rag_chunk_overlap_tokens,
            ),
            embedding_dimensions=self.rag_embedding_dimensions,
            reranker=self.rag_reranker,
            max_top_k=self.rag_max_top_k,
            grounding=GroundingPolicy(
                min_evidence_score=self.rag_min_evidence_score,
                min_query_term_coverage=self.rag_min_query_term_coverage,
            ),
        )
