from __future__ import annotations

from app.domain.models import NormalizedLead
from app.repositories.properties import SyntheticJsonPropertyRepository
from app.services.property_retrieval import PropertyRetrievalService


def _lead(**overrides):
    base = {
        "lead_id": "lead-retrieval-001",
        "first_name": "Avery",
        "last_name": "Stone",
        "email": "avery.stone@example.com",
        "phone": "+15550100101",
        "preferred_city": "Austin",
        "budget_max": 650000,
        "min_bedrooms": 3,
        "has_message": True,
    }
    base.update(overrides)
    return NormalizedLead.model_validate(base)


def test_city_match_ranks_highest() -> None:
    service = PropertyRetrievalService(SyntheticJsonPropertyRepository())
    lead = _lead(preferred_city="Austin", budget_max=900000, min_bedrooms=1)
    results = service.retrieve(lead)
    assert results
    austin_ids = [r.property_id for r in results if r.city == "Austin"]
    top = results[0]
    assert top.property_id in austin_ids
    for other in results[1:]:
        assert top.rank_score >= other.rank_score


def test_budget_within_budget_scores_higher() -> None:
    service = PropertyRetrievalService(SyntheticJsonPropertyRepository())
    low_budget = service.retrieve(
        _lead(budget_max=500000, preferred_city="Phoenix", min_bedrooms=3)
    )
    high_budget = service.retrieve(
        _lead(budget_max=1000000, preferred_city="Phoenix", min_bedrooms=3)
    )
    assert low_budget
    assert high_budget
    assert high_budget[0].rank_score >= low_budget[0].rank_score


def test_bedroom_mismatch_lowers_score() -> None:
    service = PropertyRetrievalService(SyntheticJsonPropertyRepository())
    easy = service.retrieve(_lead(min_bedrooms=1))
    hard = service.retrieve(_lead(min_bedrooms=5))
    assert easy
    assert hard
    assert easy[0].rank_score >= hard[0].rank_score


def test_at_most_three_results() -> None:
    service = PropertyRetrievalService(SyntheticJsonPropertyRepository())
    results = service.retrieve(
        _lead(budget_max=10_000_000, min_bedrooms=1, preferred_city="Austin")
    )
    assert len(results) <= 3


def test_ties_broken_by_property_id() -> None:
    service = PropertyRetrievalService(SyntheticJsonPropertyRepository())
    results_a = service.retrieve(
        _lead(budget_max=10_000_000, min_bedrooms=1, preferred_city="Austin")
    )
    results_b = service.retrieve(
        _lead(budget_max=10_000_000, min_bedrooms=1, preferred_city="Austin")
    )
    assert [r.property_id for r in results_a] == [r.property_id for r in results_b]
