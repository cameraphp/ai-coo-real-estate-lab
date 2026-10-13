from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.models import DocumentType, RerankerKind
from app.main import AppSettings, build_app
from app.observability.logging import new_logger
from app.rag.chunking import ChunkingConfig, split_into_windows
from app.rag.config import DEFAULT_CORPUS_PATH, RagSettings
from app.rag.corpus import CorpusValidationError, load_corpus, parse_corpus
from app.rag.embeddings import DeterministicHashEmbeddingProvider, cosine_similarity
from app.rag.evaluation import load_dataset, run_evaluation
from app.rag.index_cli import summarize
from app.rag.ingestion import build_chunks, build_index
from app.rag.redaction import EMAIL_MARKER, PHONE_MARKER, redact_pii
from app.rag.text import content_tokens
from tests.rag_support import (
    EVALUATION_DATASET_PATH,
    Q_REDACTED,
    RAW_EMAIL,
    RAW_PHONE,
    RAW_PII_FRAGMENTS,
    REPO_ROOT,
    default_config,
    default_corpus,
    default_index,
    rag_query,
)
from tests.support import OPERATOR_A, actor, captured_app_logs, make_client


def _document(**overrides: Any) -> dict[str, Any]:
    return {
        "source_document_id": "doc-x-sample",
        "tenant_id": "tenant-a",
        "title": "Synthetic sample",
        "document_type": "property_brief",
        "content": "Synthetic sample content for a unit test.",
        "is_synthetic": True,
        **overrides,
    }


def _corpus(*documents: dict[str, Any]) -> dict[str, Any]:
    return {"corpus_version": "v1", "description": "test corpus", "documents": list(documents)}


# --- Corpus fixture -------------------------------------------------------------


def test_corpus_fixture_validates_and_every_document_is_synthetic() -> None:
    corpus = default_corpus()
    assert corpus.corpus_version == "v1"
    assert len(corpus.documents) >= 12
    assert all(document.is_synthetic is True for document in corpus.documents)
    assert {document.tenant_id for document in corpus.documents} == {"tenant-a", "tenant-b"}
    assert {document.document_type for document in corpus.documents} == set(DocumentType)
    ids = [document.source_document_id for document in corpus.documents]
    assert len(ids) == len(set(ids))


def test_corpus_fixture_contains_the_required_controlled_cases() -> None:
    corpus = default_corpus()
    raw = DEFAULT_CORPUS_PATH.read_text(encoding="utf-8")
    # The raw fixture holds the fictional PII so that redaction has something to prove.
    assert RAW_EMAIL in raw and RAW_PHONE in raw
    tenant_a_facts = {
        document.fact_value
        for document in corpus.documents
        if document.tenant_id == "tenant-a" and document.fact_key == "tour_confirmation_lead_time"
    }
    assert tenant_a_facts == {"24_hours", "48_hours"}
    assert sum(1 for d in corpus.documents if d.tenant_id == "tenant-b") >= 2


@pytest.mark.parametrize(
    ("document", "failing_field"),
    [
        (_document(is_synthetic=False), "is_synthetic"),
        ({k: v for k, v in _document().items() if k != "is_synthetic"}, "is_synthetic"),
        (_document(document_type="customer_contract"), "document_type"),
        (_document(tenant_id="acme"), "tenant_id"),
        (_document(source_document_id="DOC 1"), "source_document_id"),
        (_document(property_id="123-main-street"), "property_id"),
        (_document(content="   "), "content"),
        (_document(unexpected_field="x"), "unexpected_field"),
        (_document(fact_key="tour_confirmation_lead_time"), "documents.0"),
    ],
)
def test_invalid_or_non_synthetic_records_fail_ingestion_clearly(
    document: dict[str, Any], failing_field: str
) -> None:
    with pytest.raises(CorpusValidationError) as error:
        parse_corpus(_corpus(document))
    assert failing_field in str(error.value)
    # The message names the field but never repeats the record's content.
    assert "Synthetic sample content" not in str(error.value)


def test_duplicate_document_ids_fail_ingestion() -> None:
    with pytest.raises(CorpusValidationError):
        parse_corpus(_corpus(_document(), _document()))


def test_one_bad_record_fails_the_whole_corpus_rather_than_being_skipped() -> None:
    with pytest.raises(CorpusValidationError):
        parse_corpus(_corpus(_document(), _document(source_document_id="doc-y", is_synthetic=0)))


def test_app_construction_fails_when_the_corpus_is_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "corpus.json"
    bad.write_text(json.dumps(_corpus(_document(is_synthetic=False))), encoding="utf-8")
    with pytest.raises(CorpusValidationError):
        build_app(AppSettings(rag_corpus_path=bad))
    with pytest.raises(CorpusValidationError):
        build_app(AppSettings(rag_corpus_path=tmp_path / "missing.json"))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CorpusValidationError):
        load_corpus(tmp_path / "broken.json")


# --- Redaction ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected", "count"),
    [
        (f"Contact {RAW_EMAIL} today.", f"Contact {EMAIL_MARKER} today.", 1),
        (f"Call {RAW_PHONE}.", f"Call {PHONE_MARKER}.", 1),
        ("Call +1 (555) 010-0101 now", f"Call {PHONE_MARKER} now", 1),
        ("Call 512-555-0142 or 512.555.0143", f"Call {PHONE_MARKER} or {PHONE_MARKER}", 2),
        ("Call +15550100101", f"Call {PHONE_MARKER}", 1),
        (
            f"Write {RAW_EMAIL} or ring {RAW_PHONE}.",
            f"Write {EMAIL_MARKER} or ring {PHONE_MARKER}.",
            2,
        ),
    ],
)
def test_email_and_phone_values_are_replaced(text: str, expected: str, count: int) -> None:
    result = redact_pii(text)
    assert result.text == expected
    assert result.redaction_count == count


@pytest.mark.parametrize(
    "text",
    [
        "The synthetic price is 599000 demo dollars.",
        "Reviewed on 2026-10-12 for prop-demo-001.",
        "Each tour lasts 45 minutes for lead 017.",
    ],
)
def test_redaction_leaves_non_pii_text_untouched(text: str) -> None:
    assert redact_pii(text).text == text
    assert redact_pii(text).redaction_count == 0


def test_indexed_chunks_are_redacted_before_embedding() -> None:
    index = default_index()
    for chunk in index.chunks:
        for fragment in RAW_PII_FRAGMENTS:
            assert fragment not in chunk.text
            assert fragment not in chunk.title

    redacted = [chunk for chunk in index.chunks if chunk.redaction_count]
    assert redacted, "expected at least one chunk carrying redaction markers"
    assert {chunk.source_document_id for chunk in redacted} == {"doc-a-brief-prop-001"}
    for chunk in redacted:
        assert EMAIL_MARKER in chunk.text and PHONE_MARKER in chunk.text
        assert chunk.document_redaction_count == 2
        entry = index.store.get(chunk.tenant_id, chunk.chunk_id)
        assert entry is not None
        # Tokens that exist only inside the raw email or phone never reached the embedder.
        assert not {"lab", "example", "512", "0142"} & set(entry.content_tokens)
        assert entry.vector == index.embedding.embed_tokens(content_tokens(chunk.text))


def test_redacted_values_never_reach_api_logs_audit_or_evaluation_output() -> None:
    client = make_client()
    with captured_app_logs() as logs:
        response = rag_query(client, Q_REDACTED)
        status = client.get("/api/v1/rag/index/status", headers=actor(OPERATOR_A))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_status"] == "GROUNDED"
    assert {c["source_document_id"] for c in body["citations"]} == {"doc-a-brief-prop-001"}
    assert any(
        EMAIL_MARKER in c["excerpt"] and PHONE_MARKER in c["excerpt"] for c in body["citations"]
    )
    assert status.json()["redaction_count"] == 2

    audit_text = "\n".join(
        event.model_dump_json()
        for event in client.app.state.rag_audit_repository.list_for_tenant("tenant-a")
    )
    config = default_config()
    report = run_evaluation(
        load_dataset(EVALUATION_DATASET_PATH),
        default_corpus(),
        config,
        new_logger(),
        include_cases=True,
    )
    summary_text = json.dumps(summarize(default_index(config)))

    assert logs and audit_text
    for text in (
        response.text,
        status.text,
        "\n".join(logs),
        audit_text,
        report.model_dump_json(),
        summary_text,
    ):
        for fragment in RAW_PII_FRAGMENTS:
            assert fragment not in text


# --- Chunking -------------------------------------------------------------------


def test_chunking_is_deterministic() -> None:
    first, second = default_index(), default_index()
    assert first.chunks == second.chunks
    assert first.fingerprint() == second.fingerprint()
    for chunk in first.chunks:
        left = first.store.get(chunk.tenant_id, chunk.chunk_id)
        right = second.store.get(chunk.tenant_id, chunk.chunk_id)
        assert left is not None and right is not None
        assert left.vector == right.vector


def test_chunk_ids_metadata_and_windows_are_stable() -> None:
    corpus = default_corpus()
    config = ChunkingConfig(chunk_size_tokens=80, chunk_overlap_tokens=20)
    document = next(d for d in corpus.documents if d.source_document_id == "doc-a-brief-prop-001")
    chunks = build_chunks(document, config)

    assert [chunk.chunk_id for chunk in chunks] == [
        "doc-a-brief-prop-001#chunk-000",
        "doc-a-brief-prop-001#chunk-001",
    ]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    for chunk in chunks:
        assert chunk.text and chunk.text == " ".join(chunk.text.split())
        assert len(chunk.text.split(" ")) <= 80
        assert chunk.content_hash.startswith("sha256:")
        assert (chunk.tenant_id, chunk.city, chunk.property_id) == (
            "tenant-a",
            "Austin",
            "prop-demo-001",
        )
        assert chunk.document_type is DocumentType.PROPERTY_BRIEF
    # Consecutive windows share exactly the configured overlap.
    assert chunks[0].text.split(" ")[-20:] == chunks[1].text.split(" ")[:20]


def test_windows_never_produce_an_empty_chunk() -> None:
    config = ChunkingConfig(chunk_size_tokens=16, chunk_overlap_tokens=4)
    assert split_into_windows("   ", config) == []
    assert split_into_windows("one  two\nthree", config) == ["one two three"]
    words = [f"w{n}" for n in range(40)]
    windows = split_into_windows(" ".join(words), config)
    assert [w.split(" ")[0] for w in windows] == ["w0", "w12", "w24"]
    assert all(windows)
    assert windows[-1].split(" ")[-1] == "w39"
    exact = split_into_windows(" ".join(words[:16]), config)
    assert len(exact) == 1


def test_every_indexed_chunk_is_non_empty_and_has_a_unique_id() -> None:
    index = default_index()
    ids = [chunk.chunk_id for chunk in index.chunks]
    assert len(ids) == len(set(ids))
    assert all(chunk.text.strip() for chunk in index.chunks)
    assert any(chunk.chunk_index > 0 for chunk in index.chunks), "expected a multi-chunk document"


# --- Settings validation --------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "overlap"), [(80, 80), (80, 120), (8, 0), (1000, 20), (80, -1), (400, 300)]
)
def test_chunking_config_rejects_invalid_combinations(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size_tokens=size, chunk_overlap_tokens=overlap)


@pytest.mark.parametrize(
    "overrides",
    [
        {"rag_chunk_size_tokens": 80, "rag_chunk_overlap_tokens": 80},
        {"rag_chunk_size_tokens": 20, "rag_chunk_overlap_tokens": 40},
        {"rag_chunk_size_tokens": 4},
        {"rag_embedding_dimensions": 8},
        {"rag_embedding_dimensions": 4096},
        {"rag_reranker": "cross-encoder"},
        {"rag_max_top_k": 0},
        {"rag_max_top_k": 6},
        {"rag_min_evidence_score": 1.5},
        {"rag_min_query_term_coverage": 0},
    ],
)
def test_settings_reject_out_of_range_values_at_startup(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RagSettings(_env_file=None, **overrides)
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, **overrides)


def test_settings_defaults_match_the_documented_configuration() -> None:
    config = RagSettings(_env_file=None).rag_config()
    assert config.corpus_path == DEFAULT_CORPUS_PATH
    assert (config.chunking.chunk_size_tokens, config.chunking.chunk_overlap_tokens) == (80, 20)
    assert config.embedding_dimensions == 128
    assert config.reranker is RerankerKind.LEXICAL
    assert config.max_top_k == 5
    assert config.grounding.min_evidence_score == 0.15
    assert config.grounding.min_query_term_coverage == 0.7


def test_env_example_lists_exactly_the_rag_settings_the_code_reads() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    documented = {
        line.split("=", 1)[0].lower() for line in lines if line.startswith("RAG_") and "=" in line
    }
    assert documented == {name for name in RagSettings.model_fields if name.startswith("rag_")}


# --- Embeddings -----------------------------------------------------------------


def test_embedding_is_stable_normalized_and_lexical() -> None:
    provider = DeterministicHashEmbeddingProvider(128)
    tokens = content_tokens("The Austin property supports a family tour.")
    vector = provider.embed_tokens(tokens)

    assert vector == provider.embed_tokens(tokens)
    assert len(vector) == 128
    assert sum(value * value for value in vector) == pytest.approx(1.0)
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)
    assert provider.embed_tokens([]) == tuple([0.0] * 128)
    # Shared tokens raise similarity; this is lexical overlap, not semantics.
    near = provider.embed_tokens(content_tokens("Austin family tour"))
    far = provider.embed_tokens(content_tokens("mortgage forecast"))
    assert cosine_similarity(vector, near) > cosine_similarity(vector, far)


@pytest.mark.parametrize("dimensions", [0, 16, 2048])
def test_embedding_dimensions_are_bounded(dimensions: int) -> None:
    with pytest.raises(ValueError):
        DeterministicHashEmbeddingProvider(dimensions)


def test_index_is_identical_across_processes_with_different_hash_seeds() -> None:
    """Chunk IDs and vectors come from hashlib, so PYTHONHASHSEED must not matter."""
    script = (
        "from tests.rag_support import default_index\n"
        "index = default_index()\n"
        "chunk = index.chunks[0]\n"
        "print(index.fingerprint(), index.store.get(chunk.tenant_id, chunk.chunk_id).vector)\n"
    )
    outputs = set()
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.add(result.stdout)
    assert len(outputs) == 1
    assert default_index().fingerprint() in outputs.pop()


def test_index_build_rejects_mismatched_vectors_and_duplicate_chunks() -> None:
    index = default_index()
    chunk = index.chunks[0]
    entry = index.store.get(chunk.tenant_id, chunk.chunk_id)
    assert entry is not None
    with pytest.raises(ValueError):
        index.store.add(entry)
    with pytest.raises(ValueError):
        build_index(default_corpus(), index.chunking, _WrongSizeEmbedding())


class _WrongSizeEmbedding:
    label = "test-wrong-size"
    dimensions = 128

    def embed_tokens(self, tokens: Any) -> tuple[float, ...]:
        return (1.0, 0.0)
