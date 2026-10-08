"""Pure, read-only search over the synthetic property fixture.

No network, no writes, no CRM/calendar/messaging access, no approvals.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import SyntheticProperty
from app.repositories.properties import PropertyRepository

MAX_SEARCH_RESULTS = 3


class PropertySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    city: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z .'-]*$"
    )
    min_bedrooms: int | None = Field(default=None, strict=True, ge=1, le=10)
    max_price: int | None = Field(default=None, strict=True, gt=0, le=100_000_000)
    max_results: int = Field(default=MAX_SEARCH_RESULTS, strict=True, ge=1, le=MAX_SEARCH_RESULTS)


class PropertySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    synthetic_data_only: Literal[True] = True
    total_matches: int
    properties: list[SyntheticProperty]


class PropertySearchService:
    def __init__(self, repository: PropertyRepository) -> None:
        self._repository = repository

    def search(self, query: PropertySearchInput) -> PropertySearchResult:
        city = query.city.casefold() if query.city else None
        matches = [
            prop
            for prop in self._repository.list_all()
            if (city is None or prop.city.casefold() == city)
            and (query.min_bedrooms is None or prop.bedrooms >= query.min_bedrooms)
            and (query.max_price is None or prop.list_price <= query.max_price)
        ]
        matches.sort(key=lambda prop: (prop.list_price, prop.property_id))
        return PropertySearchResult(
            total_matches=len(matches),
            properties=matches[: query.max_results],
        )
