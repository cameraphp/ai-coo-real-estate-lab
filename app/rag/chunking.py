"""Deterministic token-window chunking with stable chunk IDs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain.models import DocumentType
from app.rag.text import normalize_whitespace

MIN_CHUNK_SIZE_TOKENS = 16
MAX_CHUNK_SIZE_TOKENS = 512
MAX_CHUNK_OVERLAP_TOKENS = 256


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size_tokens: int = 80
    chunk_overlap_tokens: int = 20

    def __post_init__(self) -> None:
        if not MIN_CHUNK_SIZE_TOKENS <= self.chunk_size_tokens <= MAX_CHUNK_SIZE_TOKENS:
            raise ValueError(
                f"chunk_size_tokens must be between {MIN_CHUNK_SIZE_TOKENS} "
                f"and {MAX_CHUNK_SIZE_TOKENS}."
            )
        if not 0 <= self.chunk_overlap_tokens <= MAX_CHUNK_OVERLAP_TOKENS:
            raise ValueError(
                f"chunk_overlap_tokens must be between 0 and {MAX_CHUNK_OVERLAP_TOKENS}."
            )
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens.")


@dataclass(frozen=True)
class Chunk:
    """A redacted, normalized slice of one synthetic document, with its metadata."""

    chunk_id: str
    source_document_id: str
    tenant_id: str
    title: str
    document_type: DocumentType
    city: str | None
    property_id: str | None
    chunk_index: int
    text: str
    content_hash: str
    # Markers inside this chunk. Overlapping chunks can each carry the same marker.
    redaction_count: int
    # Values redacted from the whole source document, title included.
    document_redaction_count: int
    fact_key: str | None
    fact_value: str | None
    untrusted_instruction: bool


def chunk_id_for(source_document_id: str, chunk_index: int) -> str:
    return f"{source_document_id}#chunk-{chunk_index:03d}"


def content_hash_for(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def split_into_windows(text: str, config: ChunkingConfig) -> list[str]:
    """Overlapping whitespace-token windows. Never returns an empty window."""
    tokens = normalize_whitespace(text).split(" ")
    if tokens == [""]:
        return []
    stride = config.chunk_size_tokens - config.chunk_overlap_tokens
    windows: list[str] = []
    start = 0
    while True:
        windows.append(" ".join(tokens[start : start + config.chunk_size_tokens]))
        if start + config.chunk_size_tokens >= len(tokens):
            return windows
        start += stride
