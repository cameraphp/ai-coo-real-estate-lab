#!/usr/bin/env python3
"""Standard-library smoke client for the locally running demo container.

Polls ``GET /health``, then sends one read-only tenant A RAG query and checks that the answer is
grounded in tenant A's synthetic documents with the fixture's PII-like values redacted. It never
creates or changes a workflow, calls a tool, reads a secret, or writes a file. It accepts loopback
URLs only, so it cannot be pointed at a remote service.

Usage: python scripts/container_smoke.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

SERVICE_NAME = "ai-coo-real-estate-lab"
DEMO_ACTOR_ID = "tenant-a-operator"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0
REQUEST_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.5

# The README's tenant A grounded example (walkthrough step 11). Typed filters only; the tenant
# comes from the demo actor, and the reranker is server-owned.
RAG_QUERY: dict[str, Any] = {
    "question": "Which Austin property supports a three-bedroom family tour?",
    "top_k": 3,
    "filters": {"city": "Austin", "document_type": "property_brief"},
}

# Tenant A source document IDs in data/rag/synthetic_documents.v1.json (corpus v1). This is the
# single place the smoke expectations are defined; update it only with a new corpus version.
TENANT_A_SOURCE_DOCUMENT_IDS = frozenset(
    {
        "doc-a-brief-prop-001",
        "doc-a-brief-prop-002",
        "doc-a-brief-prop-004",
        "doc-a-tour-procedure-austin",
        "doc-a-tour-confirmation-policy",
        "doc-a-followup-procedure",
        "doc-a-eligibility-note",
        "doc-a-conversation-summary-lead-017",
        "doc-a-inbound-note-web-form",
    }
)

# Fictional email and phone fragments that the corpus redactor must remove before indexing.
# doc-a-brief-prop-001, which the query above cites, contains them. Never printed.
RAW_PII_FRAGMENTS = ("tours.north@lab-demo.example", "lab-demo.example", "555-0142", "tours.north")


class SmokeError(Exception):
    """A readiness or assertion failure. Messages never contain response bodies."""


def validate_base_url(raw: str) -> str:
    parts = urlsplit(raw)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("base URL has an invalid port") from exc
    if parts.scheme != "http":
        raise ValueError("base URL must use http")
    if parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError("base URL host must be loopback (127.0.0.1, localhost, or ::1)")
    if parts.username or parts.password:
        raise ValueError("base URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("base URL must not contain a path, query, or fragment")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"http://{host}" + (f":{port}" if port is not None else "")


def _timeout_seconds(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be between {MIN_TIMEOUT_SECONDS:g} and {MAX_TIMEOUT_SECONDS:g}"
        )
    return value


def _base_url(raw: str) -> str:
    try:
        return validate_base_url(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_json_object(body: str, what: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"{what} response is not JSON") from exc
    if not isinstance(payload, dict):
        raise SmokeError(f"{what} response is not a JSON object")
    return payload


def check_health_response(status: int, body: str) -> None:
    if status != 200:
        raise SmokeError(f"health returned HTTP {status}")
    payload = _parse_json_object(body, "health")
    if payload.get("status") != "ok":
        raise SmokeError("health status is not 'ok'")
    if payload.get("service") != SERVICE_NAME:
        raise SmokeError("health reports an unexpected service name")


def check_rag_response(status: int, body: str) -> dict[str, Any]:
    """Assert the grounded tenant A contract and return a summary that is safe to print."""
    if status != 200:
        raise SmokeError(f"RAG query returned HTTP {status}")
    lowered = body.lower()
    leaked = sum(1 for fragment in RAW_PII_FRAGMENTS if fragment.lower() in lowered)
    if leaked:
        raise SmokeError(f"RAG response contains {leaked} raw PII-like fixture fragment(s)")
    payload = _parse_json_object(body, "RAG")
    if payload.get("answer_status") != "GROUNDED":
        raise SmokeError("RAG answer_status is not GROUNDED")
    if payload.get("synthetic_data_only") is not True:
        raise SmokeError("RAG response is not marked synthetic_data_only")
    citations = payload.get("citations")
    if not isinstance(citations, list) or not citations:
        raise SmokeError("RAG response has no citations")
    source_ids: set[str] = set()
    for citation in citations:
        source_id = citation.get("source_document_id") if isinstance(citation, dict) else None
        if source_id not in TENANT_A_SOURCE_DOCUMENT_IDS:
            raise SmokeError("RAG citation is not from tenant A's synthetic fixture set")
        source_ids.add(source_id)
    return {
        "rag_answer_status": "GROUNDED",
        "citation_count": len(citations),
        "cited_source_document_ids": sorted(source_ids),
        "raw_pii_fragments_found": 0,
        "synthetic_data_only": True,
    }


def _request(
    url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, str]:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Only the status is needed; an error body is never parsed or printed.
        exc.close()
        return exc.code, ""


def wait_for_health(base_url: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_problem = "no response"
    while True:
        try:
            status, body = _request(f"{base_url}/health")
            check_health_response(status, body)
            return
        except SmokeError as exc:
            last_problem = str(exc)
        except (OSError, http.client.HTTPException) as exc:
            # Connection refused or reset while the container is still starting.
            last_problem = type(exc).__name__
        if time.monotonic() >= deadline:
            raise SmokeError(
                f"service not healthy within {timeout_seconds:g}s (last problem: {last_problem})"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def run_rag_query(base_url: str) -> dict[str, Any]:
    try:
        status, body = _request(
            f"{base_url}/api/v1/rag/query",
            data=json.dumps(RAG_QUERY).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Demo-Actor-ID": DEMO_ACTOR_ID,
                "X-Correlation-ID": "container-smoke",
            },
        )
    except (OSError, http.client.HTTPException) as exc:
        raise SmokeError(f"RAG query failed ({type(exc).__name__})") from exc
    return check_rag_response(status, body)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the locally running demo container (loopback URLs only)."
    )
    parser.add_argument("--base-url", type=_base_url, default="http://127.0.0.1:8000")
    parser.add_argument(
        "--timeout-seconds",
        type=_timeout_seconds,
        default=30.0,
        help="How long to wait for /health to succeed (1-120, default 30).",
    )
    args = parser.parse_args(argv)

    try:
        wait_for_health(args.base_url, args.timeout_seconds)
        summary = {"health": "ok", "service": SERVICE_NAME, **run_rag_query(args.base_url)}
    except SmokeError as exc:
        print(f"container smoke failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"container_smoke": "passed", **summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
