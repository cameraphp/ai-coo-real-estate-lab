from __future__ import annotations

from typing import Protocol

from app.domain.models import AgentProposal, NormalizedLead, RankedProperty


class LeadProposalAgentError(RuntimeError):
    pass


class LeadProposalAgent(Protocol):
    provider_name: str

    def generate_proposal(
        self,
        lead: NormalizedLead,
        retrieved_properties: list[RankedProperty],
    ) -> AgentProposal: ...
