"""Contract tests for scripts/container_smoke.py.

The real proof is the container smoke run. These tests run the script's response checks against
the in-process app, so a drift between the app and the smoke expectations fails here first, and
show that the checks reject wrong responses. No network or container is involved.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.rag_support import RAW_PII_FRAGMENTS, default_corpus
from tests.support import OPERATOR_A, actor, make_client

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "container_smoke.py"


def _load_smoke_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("container_smoke", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def _grounded_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "answer_status": "GROUNDED",
        "synthetic_data_only": True,
        "citations": [{"source_document_id": "doc-a-brief-prop-001"}],
    }
    body.update(overrides)
    return body


def test_smoke_checks_pass_against_the_in_process_app() -> None:
    client = make_client()
    health = client.get("/health")
    smoke.check_health_response(health.status_code, health.text)

    assert smoke.DEMO_ACTOR_ID == OPERATOR_A
    response = client.post("/api/v1/rag/query", json=smoke.RAG_QUERY, headers=actor(OPERATOR_A))
    summary = smoke.check_rag_response(response.status_code, response.text)

    assert summary["rag_answer_status"] == "GROUNDED"
    assert summary["citation_count"] >= 1
    assert summary["raw_pii_fragments_found"] == 0
    assert all(source_id.startswith("doc-a-") for source_id in summary["cited_source_document_ids"])
    # The query cites the brief that contains the redacted email and phone, so the PII check
    # exercises real redaction rather than an unrelated document.
    assert "doc-a-brief-prop-001" in summary["cited_source_document_ids"]


def test_tenant_a_source_ids_match_the_committed_corpus() -> None:
    tenant_a_ids = {
        document.source_document_id
        for document in default_corpus().documents
        if document.tenant_id == "tenant-a"
    }
    assert tenant_a_ids == smoke.TENANT_A_SOURCE_DOCUMENT_IDS


def test_pii_fragments_cover_the_fixture_values() -> None:
    assert set(smoke.RAW_PII_FRAGMENTS) <= set(RAW_PII_FRAGMENTS)
    assert "tours.north@lab-demo.example" in smoke.RAW_PII_FRAGMENTS
    assert "555-0142" in smoke.RAW_PII_FRAGMENTS


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (403, _grounded_body(), "HTTP 403"),
        (200, _grounded_body(answer_status="INSUFFICIENT_EVIDENCE"), "not GROUNDED"),
        (200, _grounded_body(citations=[]), "no citations"),
        (200, _grounded_body(synthetic_data_only=False), "synthetic_data_only"),
        (
            200,
            _grounded_body(citations=[{"source_document_id": "doc-b-followup-procedure"}]),
            "not from tenant A",
        ),
        (
            200,
            _grounded_body(citations=[{"source_document_id": "doc-a-brief-prop-001"}, "x"]),
            "not from tenant A",
        ),
        (200, _grounded_body(answer="Call (512) 555-0142"), "PII-like"),
    ],
)
def test_rag_check_rejects_wrong_responses(status: int, body: dict[str, Any], message: str) -> None:
    with pytest.raises(smoke.SmokeError, match=message) as excinfo:
        smoke.check_rag_response(status, json.dumps(body))
    assert "555-0142" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, ""),
        (200, "not json"),
        (200, json.dumps({"status": "ok", "service": "other-service"})),
        (200, json.dumps(["ok"])),
    ],
)
def test_health_check_rejects_wrong_responses(status: int, body: str) -> None:
    with pytest.raises(smoke.SmokeError):
        smoke.check_health_response(status, body)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_base_url_accepts_loopback_http(raw: str, expected: str) -> None:
    assert smoke.validate_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://127.0.0.1:8000",
        "http://example.com:8000",
        "http://10.0.0.5:8000",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.1:8000?x=1",
        "http://user:pass@127.0.0.1:8000",
        "http://127.0.0.1:notaport",
        "file:///etc/passwd",
    ],
)
def test_base_url_rejects_non_loopback_or_decorated_urls(raw: str) -> None:
    with pytest.raises(ValueError):
        smoke.validate_base_url(raw)


@pytest.mark.parametrize(
    "argv",
    [
        ["--base-url", "http://example.com"],
        ["--timeout-seconds", "0"],
        ["--timeout-seconds", "500"],
        ["--timeout-seconds", "soon"],
    ],
)
def test_cli_rejects_invalid_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        smoke.main(argv)
    assert excinfo.value.code == 2
