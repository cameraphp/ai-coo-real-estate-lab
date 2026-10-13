from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.models import RagAnswerStatus, RerankerKind
from app.observability.logging import new_logger
from app.rag import index_cli
from app.rag.evaluation import (
    MIN_CASES,
    EvaluationError,
    EvaluationReport,
    EvaluationScenario,
    _score_retrieval,
    load_dataset,
    main,
    percentile,
    principal_for_tenant,
    run_evaluation,
    validate_against_corpus,
)
from tests.rag_support import (
    EVALUATION_DATASET_PATH,
    INJECTION_FRAGMENTS,
    RAW_PII_FRAGMENTS,
    default_config,
    default_corpus,
    default_index,
    reranker_config,
)

REQUIRED_METRICS = {
    "dataset_version",
    "case_count",
    "reranker",
    "recall_at_k",
    "precision_at_k",
    "mrr",
    "answer_groundedness",
    "insufficient_evidence_accuracy",
    "unsupported_claim_rate",
    "retrieval_latency_p50_ms",
    "retrieval_latency_p95_ms",
}
TIMING_FIELDS = {"retrieval_latency_p50_ms", "retrieval_latency_p95_ms"}
DATASET_ARGS = ["--dataset", str(EVALUATION_DATASET_PATH)]


def _run(kind: RerankerKind, include_cases: bool = True) -> EvaluationReport:
    return run_evaluation(
        load_dataset(EVALUATION_DATASET_PATH),
        default_corpus(),
        reranker_config(kind),
        new_logger(),
        include_cases=include_cases,
    )


def _raw_dataset() -> dict[str, Any]:
    return json.loads(EVALUATION_DATASET_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _without_timing(report: EvaluationReport) -> dict[str, Any]:
    return report.model_dump(mode="json", exclude=TIMING_FIELDS)


# --- Fixture -------------------------------------------------------------------------


def test_evaluation_fixture_validates_and_covers_every_scenario() -> None:
    dataset = load_dataset(EVALUATION_DATASET_PATH)
    assert dataset.dataset_version == "v1"
    assert dataset.corpus_version == default_corpus().corpus_version
    assert len(dataset.cases) >= MIN_CASES == 12
    assert {case.scenario for case in dataset.cases} == set(EvaluationScenario)
    assert {case.tenant_id for case in dataset.cases} == {"tenant-a", "tenant-b"}
    assert len({case.case_id for case in dataset.cases}) == len(dataset.cases)
    validate_against_corpus(dataset, default_corpus())
    for case in dataset.cases:
        expects_reason = case.expected_answer_status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
        assert (case.expected_insufficient_evidence_reason is not None) == expects_reason


# --- Metrics -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(RerankerKind))
def test_runner_reports_every_required_metric(kind: RerankerKind) -> None:
    report = _run(kind)
    payload = report.model_dump(mode="json")

    assert set(payload) >= REQUIRED_METRICS
    assert payload["reranker"] == kind.value
    assert payload["case_count"] == len(load_dataset(EVALUATION_DATASET_PATH).cases) == 15
    assert payload["retrieval_scored_case_count"] == 9
    for name in ("recall_at_k", "precision_at_k", "mrr", "answer_status_accuracy"):
        assert 0.0 <= payload[name] <= 1.0
    assert 0.0 <= payload["retrieval_latency_p50_ms"] <= payload["retrieval_latency_p95_ms"]
    # Safety properties hold under both server configurations.
    assert payload["answer_groundedness"] == 1.0
    assert payload["unsupported_claim_rate"] == 0.0
    assert payload["insufficient_evidence_accuracy"] == 1.0
    assert payload["cross_tenant_retrieval_count"] == 0
    assert payload["recall_at_k"] == 1.0


def test_metrics_are_deterministic_except_timing() -> None:
    for kind in RerankerKind:
        assert _without_timing(_run(kind)) == _without_timing(_run(kind))


def test_reranker_configurations_differ_only_where_ranking_order_matters() -> None:
    """An observation about this fixture, not a claim that one reranker is better."""
    lexical, vector_only = _run(RerankerKind.LEXICAL), _run(RerankerKind.NONE)
    assert lexical.cases is not None and vector_only.cases is not None

    by_id_lexical = {case.case_id: case for case in lexical.cases}
    by_id_vector = {case.case_id: case for case in vector_only.cases}
    differing = {
        case_id
        for case_id in by_id_lexical
        if by_id_lexical[case_id].reciprocal_rank != by_id_vector[case_id].reciprocal_rank
    }
    assert differing == {"b-reranking-sensitive"}
    assert by_id_vector["b-reranking-sensitive"].reciprocal_rank == 0.5
    assert by_id_lexical["b-reranking-sensitive"].reciprocal_rank == 1.0
    assert (lexical.mrr, vector_only.mrr) == (1.0, 0.9444)
    assert lexical.recall_at_k == vector_only.recall_at_k
    assert lexical.precision_at_k == vector_only.precision_at_k
    assert [c.answer_status for c in lexical.cases] == [c.answer_status for c in vector_only.cases]


def test_per_case_results_match_the_expected_statuses() -> None:
    report = _run(RerankerKind.LEXICAL)
    dataset = load_dataset(EVALUATION_DATASET_PATH)
    assert report.cases is not None
    for case, result in zip(dataset.cases, report.cases, strict=True):
        assert result.case_id == case.case_id
        assert result.status_matches_expected, case.case_id
        assert result.answer_status is case.expected_answer_status
        assert result.insufficient_evidence_reason is case.expected_insufficient_evidence_reason
        assert (result.recall_at_k is None) == (not case.expected_relevant_source_document_ids)
        owners = {doc[:6] for doc in result.retrieved_source_document_ids}
        assert owners <= {f"doc-{case.tenant_id[-1]}-"}


def test_token_and_cost_fields_are_not_applicable() -> None:
    payload = _run(RerankerKind.LEXICAL, include_cases=False).model_dump(mode="json")
    cost_like = {k: v for k, v in payload.items() if "cost" in k or "token" in k}
    assert cost_like == {
        "external_model_token_usage": "not_applicable",
        "external_model_cost": "not_applicable",
    }
    assert "synthetic" in payload["scope_note"] and "Not a benchmark" in payload["scope_note"]


def test_retrieval_scoring_helpers() -> None:
    assert _score_retrieval(["a", "b", "c"], ["b"], 3) == (1.0, 1 / 3, 0.5)
    assert _score_retrieval(["a", "b"], ["a", "b"], 3) == (1.0, 2 / 3, 1.0)
    assert _score_retrieval(["a"], ["x", "a"], 1) == (0.5, 1.0, 1.0)
    assert _score_retrieval([], ["a"], 3) == (0.0, 0.0, 0.0)
    assert percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.50) == 3.0
    assert percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.95) == 5.0
    assert percentile([7.0], 0.95) == 7.0


# --- Clear failures ------------------------------------------------------------------


def _mutated(mutate: Any) -> dict[str, Any]:
    payload = _raw_dataset()
    mutate(payload)
    return payload


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda d: d["cases"][0].update(
                expected_relevant_source_document_ids=["doc-a-does-not-exist"]
            ),
            "unknown document",
        ),
        (lambda d: d["cases"][0].update(tenant_id="tenant-z"), "unknown tenant"),
        (
            lambda d: d["cases"][0].update(
                expected_relevant_source_document_ids=["doc-b-brief-prop-003"]
            ),
            "not in the case's tenant",
        ),
        (lambda d: d.update(corpus_version="v9"), "corpus v9"),
        (lambda d: d["cases"][0].update(expected_answer_status="MAYBE"), "expected_answer_status"),
        (
            lambda d: d["cases"][0].update(expected_insufficient_evidence_reason="no_evidence"),
            "expected_insufficient_evidence_reason",
        ),
        (
            lambda d: d["cases"][0].update(
                expected_insufficient_evidence_reason="no_relevant_evidence"
            ),
            "failed validation",
        ),
        (lambda d: d["cases"][0].update(scenario="vibes"), "scenario"),
        (lambda d: d["cases"][0].update(tenant="tenant-a"), "tenant"),
        (lambda d: d["cases"][1].update(case_id=d["cases"][0]["case_id"]), "failed validation"),
        (lambda d: d.update(cases=d["cases"][:11]), "cases"),
    ],
    ids=[
        "unknown_document",
        "unknown_tenant",
        "cross_tenant_document",
        "corpus_version_mismatch",
        "invalid_status",
        "invalid_reason",
        "reason_on_grounded_case",
        "invalid_scenario",
        "extra_field",
        "duplicate_case_id",
        "too_few_cases",
    ],
)
def test_invalid_dataset_fails_clearly(tmp_path: Path, mutate: Any, message: str) -> None:
    path = _write(tmp_path, _mutated(mutate))
    with pytest.raises(EvaluationError) as error:
        run_evaluation(
            load_dataset(path),
            default_corpus(),
            reranker_config(RerankerKind.LEXICAL),
            new_logger(),
        )
    assert message in str(error.value)
    # A failure never leaks the question text.
    assert "three-bedroom" not in str(error.value)


def test_case_top_k_above_the_configured_limit_fails_clearly() -> None:
    config = default_config(max_top_k=2)
    with pytest.raises(EvaluationError) as error:
        run_evaluation(
            load_dataset(EVALUATION_DATASET_PATH), default_corpus(), config, new_logger()
        )
    assert "RAG_MAX_TOP_K" in str(error.value)


def test_unknown_tenant_has_no_evaluation_principal() -> None:
    assert principal_for_tenant("tenant-a").tenant_id == "tenant-a"
    assert principal_for_tenant("tenant-b").actor_id == "tenant-b-operator"
    with pytest.raises(EvaluationError):
        principal_for_tenant("tenant-z")


# --- CLI -----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["none", "lexical"])
def test_cli_prints_a_json_report_for_each_reranker(
    kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*DATASET_ARGS, "--reranker", kind]) == 0
    captured = capsys.readouterr()

    payload = json.loads(captured.out)  # stdout is the report and nothing else
    assert set(payload) >= REQUIRED_METRICS
    assert payload["reranker"] == kind
    assert payload["dataset_version"] == "v1"
    assert payload["external_model_cost"] == "not_applicable"
    assert "cases" not in payload


def test_cli_defaults_to_the_configured_reranker(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_RERANKER", "none")
    assert main(DATASET_ARGS) == 0
    assert json.loads(capsys.readouterr().out)["reranker"] == "none"


def test_cli_details_and_output_file_hold_identifiers_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "report.json"
    assert main([*DATASET_ARGS, "--reranker", "lexical", "--details", "--output", str(target)]) == 0
    printed = capsys.readouterr().out

    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(printed)
    payload = json.loads(printed)
    assert len(payload["cases"]) == payload["case_count"]
    assert set(payload["cases"][0]) == {
        "case_id",
        "scenario",
        "answer_status",
        "insufficient_evidence_reason",
        "status_matches_expected",
        "retrieved_source_document_ids",
        "cited_source_document_ids",
        "recall_at_k",
        "precision_at_k",
        "reciprocal_rank",
    }
    questions = [case["question"] for case in _raw_dataset()["cases"]]
    for fragment in (*questions, *RAW_PII_FRAGMENTS, *INJECTION_FRAGMENTS, "45 minutes"):
        assert fragment not in printed


def test_cli_fails_clearly_on_a_bad_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--dataset", str(tmp_path / "missing.json")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "evaluation failed" in captured.err and "not found" in captured.err

    broken = _write(tmp_path, _mutated(lambda d: d["cases"][0].update(tenant_id="tenant-z")))
    assert main(["--dataset", str(broken)]) == 2
    assert "unknown tenant" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        main([*DATASET_ARGS, "--reranker", "cross-encoder"])


def test_index_cli_prints_a_reproducible_safe_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert index_cli.main() == 0
    first = capsys.readouterr().out
    assert index_cli.main() == 0
    assert capsys.readouterr().out == first

    summary = json.loads(first)
    assert summary["corpus_version"] == "v1"
    assert (summary["document_count"], summary["chunk_count"]) == (14, 15)
    assert summary["redaction_count"] == 2
    assert summary["quarantined_document_count"] == 1
    assert summary["tenants"]["tenant-a"]["document_count"] == 9
    assert summary["tenants"]["tenant-b"]["document_count"] == 5
    assert summary["index_fingerprint"] == default_index().fingerprint()
    assert summary["embedding"] == "local-deterministic-hashed-lexical"
    for fragment in (*RAW_PII_FRAGMENTS, "three bedrooms", "REDACTED"):
        assert fragment not in first


def test_index_cli_fails_clearly_on_an_invalid_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_CORPUS_PATH", str(tmp_path / "missing.json"))
    assert index_cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "index validation failed" in captured.err
