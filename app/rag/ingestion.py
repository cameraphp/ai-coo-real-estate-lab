"""Startup ingestion: validate -> redact -> normalize -> chunk -> embed -> index.

Runs locally against the committed fixture. There is no HTTP ingestion path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain.models import CorpusDocument, SyntheticCorpus
from app.rag.chunking import (
    Chunk,
    ChunkingConfig,
    chunk_id_for,
    content_hash_for,
    split_into_windows,
)
from app.rag.config import RagConfig
from app.rag.corpus import load_corpus
from app.rag.embeddings import DeterministicHashEmbeddingProvider, EmbeddingProvider
from app.rag.injection import contains_injection_like_text
from app.rag.redaction import count_markers, redact_pii
from app.rag.text import content_tokens, normalize_whitespace
from app.rag.vector_store import IndexEntry, InMemoryVectorStore


@dataclass(frozen=True)
class RagIndex:
    corpus_version: str
    store: InMemoryVectorStore
    embedding: EmbeddingProvider
    chunking: ChunkingConfig
    chunks: tuple[Chunk, ...]

    def fingerprint(self) -> str:
        """Stable digest of every chunk ID and content hash, for reproducibility checks."""
        digest = hashlib.sha256()
        for chunk in self.chunks:
            digest.update(f"{chunk.chunk_id}|{chunk.content_hash}\n".encode())
        return f"sha256:{digest.hexdigest()[:16]}"


def build_chunks(document: CorpusDocument, chunking: ChunkingConfig) -> list[Chunk]:
    """Redact first, so no later stage ever sees the original PII-like values."""
    redacted_title = redact_pii(document.title)
    redacted_content = redact_pii(document.content)
    title = normalize_whitespace(redacted_title.text)
    content = normalize_whitespace(redacted_content.text)
    # A document carrying an injection-like directive is quarantined as a whole.
    untrusted = contains_injection_like_text(f"{title} {content}")
    return [
        Chunk(
            chunk_id=chunk_id_for(document.source_document_id, index),
            source_document_id=document.source_document_id,
            tenant_id=document.tenant_id,
            title=title,
            document_type=document.document_type,
            city=document.city,
            property_id=document.property_id,
            chunk_index=index,
            text=text,
            content_hash=content_hash_for(text),
            redaction_count=count_markers(text),
            document_redaction_count=(
                redacted_title.redaction_count + redacted_content.redaction_count
            ),
            fact_key=document.fact_key,
            fact_value=document.fact_value,
            untrusted_instruction=untrusted,
        )
        for index, text in enumerate(split_into_windows(content, chunking))
    ]


def build_index(
    corpus: SyntheticCorpus, chunking: ChunkingConfig, embedding: EmbeddingProvider
) -> RagIndex:
    store = InMemoryVectorStore(dimensions=embedding.dimensions)
    chunks: list[Chunk] = []
    for document in corpus.documents:
        for chunk in build_chunks(document, chunking):
            tokens = tuple(content_tokens(chunk.text))
            store.add(
                IndexEntry(
                    chunk=chunk,
                    vector=embedding.embed_tokens(tokens),
                    content_tokens=tokens,
                )
            )
            chunks.append(chunk)
    return RagIndex(
        corpus_version=corpus.corpus_version,
        store=store,
        embedding=embedding,
        chunking=chunking,
        chunks=tuple(chunks),
    )


def build_index_from_config(config: RagConfig) -> RagIndex:
    return build_index(
        load_corpus(config.corpus_path),
        config.chunking,
        DeterministicHashEmbeddingProvider(config.embedding_dimensions),
    )
