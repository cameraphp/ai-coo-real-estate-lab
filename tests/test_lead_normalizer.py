from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.models import InboundLead
from app.services.lead_normalizer import LeadNormalizationError, normalize_lead


def _lead_payload(**overrides: object) -> dict:
    base: dict = {
        "lead_id": "lead-test-001",
        "first_name": "Avery",
        "last_name": "Stone",
        "email": "Avery.Stone@Example.COM",
        "phone": "+1 (555) 010-0101",
        "preferred_city": "  Austin  ",
        "budget_max": 650000,
        "min_bedrooms": 3,
        "message": "  Hello   world  ",
    }
    base.update(overrides)
    return base


def _lead(**overrides: object) -> InboundLead:
    return InboundLead.model_validate(_lead_payload(**overrides))


def test_email_whitespace_and_lowercase_normalization() -> None:
    lead = _lead(email="  Avery.Stone@Example.COM  ")
    normalized = normalize_lead(lead)
    assert normalized.email == "avery.stone@example.com"


def test_phone_normalization_to_consistent_format() -> None:
    lead = _lead(phone="+1 (555) 010-0101")
    normalized = normalize_lead(lead)
    assert normalized.phone is not None
    for ch in normalized.phone:
        assert ch in "+0123456789"
    assert normalized.phone == "+15550100101"


def test_internal_whitespace_collapsed_in_names_and_city() -> None:
    lead = _lead(
        first_name="  Avery   Lee  ",
        last_name="Stone   Jr.",
        preferred_city="  New   Austin  ",
    )
    normalized = normalize_lead(lead)
    assert normalized.first_name == "Avery Lee"
    assert normalized.last_name == "Stone Jr."
    assert normalized.preferred_city == "New Austin"


def test_invalid_email_rejected_by_pydantic_validation() -> None:
    with pytest.raises(ValidationError):
        InboundLead.model_validate(_lead_payload(email="not-an-email"))


def test_zero_budget_max_rejected_by_pydantic_validation() -> None:
    with pytest.raises(ValidationError):
        InboundLead.model_validate(_lead_payload(budget_max=0))


def test_negative_budget_max_rejected_by_validation() -> None:
    with pytest.raises(ValidationError):
        InboundLead.model_validate(_lead_payload(budget_max=-10))


def test_missing_contact_raises_in_normalizer() -> None:
    with pytest.raises(LeadNormalizationError):
        normalize_lead(_lead(email=None, phone=None))


def test_invalid_phone_without_digits_raises_in_normalizer() -> None:
    with pytest.raises(LeadNormalizationError):
        normalize_lead(_lead(phone="---   (((( ))))", email=None))
