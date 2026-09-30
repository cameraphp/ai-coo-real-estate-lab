from __future__ import annotations

from app.adapters.mock_llm import MockLeadProposalAgent
from app.domain.models import (
    NormalizedLead,
    ProposedAction,
    RankedProperty,
    SyntheticProperty,
)
from app.services.property_retrieval import (
    BEDROOM_MATCH_SCORE,
    BUDGET_MATCH_SCORE,
    CITY_MATCH_SCORE,
)


def _lead(**overrides):
    base = {
        "lead_id": "lead-agent-001",
        "first_name": "Avery",
        "last_name": "Stone",
        "email": "avery.stone@example.com",
        "phone": "+15550100101",
        "preferred_city": "Austin",
        "budget_max": 650000,
        "min_bedrooms": 3,
        "has_message": False,
    }
    base.update(overrides)
    return NormalizedLead.model_validate(base)


def _ranked(pid: str, city: str, price: int, bedrooms: int) -> RankedProperty:
    base = SyntheticProperty.model_validate(
        {
            "property_id": pid,
            "city": city,
            "neighborhood": "Demo",
            "list_price": price,
            "bedrooms": bedrooms,
            "bathrooms": 2,
            "property_type": "single_family",
            "summary": "Synthetic test property.",
            "is_synthetic": True,
        }
    )
    return RankedProperty(
        **base.model_dump(),
        rank_score=CITY_MATCH_SCORE + BUDGET_MATCH_SCORE + BEDROOM_MATCH_SCORE,
        rank_reason="Test reason",
    )


def test_mock_agent_is_deterministic() -> None:
    agent = MockLeadProposalAgent()
    properties = [
        _ranked("p1", "Austin", 500000, 3),
        _ranked("p2", "Austin", 600000, 4),
    ]
    lead = _lead()
    a = agent.generate_proposal(lead, properties)
    b = agent.generate_proposal(lead, properties)
    assert a.model_dump() == b.model_dump()


def test_mock_agent_does_not_recommend_outside_retrieved_set() -> None:
    agent = MockLeadProposalAgent()
    properties = [
        _ranked("p-a", "Austin", 500000, 3),
        _ranked("p-b", "Austin", 600000, 4),
    ]
    proposal = agent.generate_proposal(_lead(), properties)
    retrieved_ids = {p.property_id for p in properties}
    for pid in proposal.recommended_property_ids:
        assert pid in retrieved_ids
    assert proposal.requires_human_approval is True
    assert proposal.proposed_action == ProposedAction.DRAFT_FOLLOW_UP


def test_mock_agent_proposal_mentions_it_is_for_review() -> None:
    agent = MockLeadProposalAgent()
    properties = [_ranked("p-top", "Austin", 500000, 3)]
    proposal = agent.generate_proposal(_lead(), properties)
    assert "PROPOSAL FOR REVIEW" in proposal.message_draft
    assert "NOT SENT" in proposal.message_draft
    assert "pending operator approval" in proposal.message_draft.lower()
