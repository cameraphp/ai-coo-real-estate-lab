"""Validate the synthetic corpus and print a safe summary of the in-memory index.

    python -m app.rag.index_cli

Nothing is written to disk. The summary holds counts, configuration, and a
fingerprint only; it never prints document text or redacted values.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import ValidationError

from app.rag.config import RagSettings
from app.rag.corpus import CorpusValidationError
from app.rag.ingestion import RagIndex, build_index_from_config


def summarize(index: RagIndex) -> dict[str, Any]:
    tenants = sorted({chunk.tenant_id for chunk in index.chunks})
    per_tenant = {}
    for tenant_id in tenants:
        stats = index.store.stats(tenant_id)
        per_tenant[tenant_id] = {
            "document_count": stats.document_count,
            "chunk_count": stats.chunk_count,
            "redaction_count": stats.redaction_count,
            "quarantined_document_count": stats.quarantined_document_count,
        }
    return {
        "corpus_version": index.corpus_version,
        "synthetic_data_only": True,
        "storage": "in_memory",
        "embedding": index.embedding.label,
        "embedding_dimensions": index.embedding.dimensions,
        "chunk_size_tokens": index.chunking.chunk_size_tokens,
        "chunk_overlap_tokens": index.chunking.chunk_overlap_tokens,
        "document_count": len({chunk.source_document_id for chunk in index.chunks}),
        "chunk_count": len(index.chunks),
        "redaction_count": sum(entry["redaction_count"] for entry in per_tenant.values()),
        "quarantined_document_count": len(
            {chunk.source_document_id for chunk in index.chunks if chunk.untrusted_instruction}
        ),
        "tenants": per_tenant,
        "index_fingerprint": index.fingerprint(),
    }


def main() -> int:
    try:
        index = build_index_from_config(RagSettings().rag_config())
    except (CorpusValidationError, ValidationError) as exc:
        print(f"index validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summarize(index), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
