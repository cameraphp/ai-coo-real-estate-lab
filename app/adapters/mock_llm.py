from __future__ import annotations

from app.adapters.llm import LeadProposalAgentError
from app.domain.models import (
    AgentProposal,
    LeadIntent,
    NormalizedLead,
    ProposedAction,
    RankedProperty,
    Urgency,
)


class MockLeadProposalAgent:
    provider_name = "mock"

    def generate_proposal(
        self,
        lead: NormalizedLead,
        retrieved_properties: list[RankedProperty],
    ) -> AgentProposal:
        if not isinstance(lead, NormalizedLead):
            raise LeadProposalAgentError("Lead input type invalid.")
        if not isinstance(retrieved_properties, list):
            raise LeadProposalAgentError("Retrieved properties must be a list.")

        intent = self._infer_intent(lead)
        urgency = self._infer_urgency(lead)
        recommended_ids = [rp.property_id for rp in retrieved_properties]

        city_display = lead.preferred_city
        budget_display = f"${lead.budget_max:,}"
        bedrooms_display = str(lead.min_bedrooms)
        if recommended_ids:
            top_property = retrieved_properties[0]
            property_summary = (
                f"top synthetic match {top_property.property_id} " f"({top_property.neighborhood})"
            )
        else:
            property_summary = "no strong synthetic matches"

        rationale = (
            f"Deterministic mock proposal: lead intent inferred as '{intent.value}' "
            f"with urgency '{urgency.value}' for {lead.first_name} {lead.last_name}. "
            f"Preferences: city={city_display}, budget={budget_display}, "
            f"min_bedrooms={bedrooms_display}; {property_summary}."
        )

        message_draft = (
            "[PROPOSAL FOR REVIEW — NOT SENT] "
            f"Hi {lead.first_name}, thanks for reaching out about a {city_display} home "
            f"under {budget_display} with at least {bedrooms_display} bedrooms. "
            "I prepared a few synthetic matches below and would love to schedule "
            "a call to review them. "
            "This draft is pending operator approval and has not been delivered."
        )

        return AgentProposal(
            intent=intent,
            urgency=urgency,
            recommended_property_ids=recommended_ids,
            proposed_action=ProposedAction.DRAFT_FOLLOW_UP,
            message_draft=message_draft,
            rationale=rationale,
            requires_human_approval=True,
        )

    def _infer_intent(self, lead: NormalizedLead) -> LeadIntent:
        budget = lead.budget_max
        if budget >= 1500000:
            return LeadIntent.SELL
        if budget <= 0:
            return LeadIntent.UNKNOWN
        if not lead.preferred_city:
            return LeadIntent.RENT
        return LeadIntent.BUY

    def _infer_urgency(self, lead: NormalizedLead) -> Urgency:
        if lead.has_message:
            text_blob = lead.lead_id.lower()
            if "urgent" in text_blob or "rush" in text_blob or "weekend" in text_blob:
                return Urgency.HIGH
            return Urgency.MEDIUM
        if lead.min_bedrooms >= 4:
            return Urgency.MEDIUM
        return Urgency.LOW
