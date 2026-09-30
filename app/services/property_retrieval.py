from __future__ import annotations

from app.domain.models import NormalizedLead, RankedProperty, SyntheticProperty
from app.repositories.properties import PropertyRepository

CITY_MATCH_SCORE = 5.0
BUDGET_MATCH_SCORE = 2.0
BEDROOM_MATCH_SCORE = 2.0
PARTIAL_BUDGET_SCORE = 1.0
MAX_RESULTS = 3


class PropertyRetrievalService:
    def __init__(self, property_repo: PropertyRepository) -> None:
        self._repo = property_repo

    def retrieve(self, lead: NormalizedLead) -> list[RankedProperty]:
        all_properties = self._repo.list_all()
        ranked = [self._score_property(p, lead) for p in all_properties]
        ranked.sort(key=lambda rp: (-rp.rank_score, rp.property_id))
        return ranked[:MAX_RESULTS]

    def _score_property(self, prop: SyntheticProperty, lead: NormalizedLead) -> RankedProperty:
        score = 0.0
        reasons: list[str] = []

        city_match = prop.city.lower() == lead.preferred_city.lower()
        if city_match:
            score += CITY_MATCH_SCORE
            reasons.append(f"City matches preferred '{lead.preferred_city}'")

        budget_fits = prop.list_price <= lead.budget_max
        near_budget = (not budget_fits) and prop.list_price <= lead.budget_max * 1.1
        if budget_fits:
            score += BUDGET_MATCH_SCORE
            reasons.append(f"List price ${prop.list_price:,} within budget ${lead.budget_max:,}")
        elif near_budget:
            score += PARTIAL_BUDGET_SCORE
            reasons.append(
                f"List price ${prop.list_price:,} within 10% of budget ${lead.budget_max:,}"
            )

        if prop.bedrooms >= lead.min_bedrooms:
            score += BEDROOM_MATCH_SCORE
            reasons.append(f"{prop.bedrooms} bedrooms meet minimum {lead.min_bedrooms} bedrooms")

        if not reasons:
            reasons.append("No strong match; ranked by residual properties.")

        return RankedProperty(
            **prop.model_dump(),
            rank_score=round(score, 2),
            rank_reason="; ".join(reasons),
        )
