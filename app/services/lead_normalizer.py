from __future__ import annotations

import re
from typing import NoReturn

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.domain.models import InboundLead, NormalizedLead

_EMAIL_ADAPTER: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)


class LeadNormalizationError(ValueError):
    pass


_WHITESPACE_RE = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"[^\d+]")


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalize_phone(raw: str | None) -> str | None:
    if raw is None:
        return None
    collapsed = _collapse_whitespace(raw)
    digits_only = _DIGITS_RE.sub("", collapsed)
    if not digits_only:
        raise LeadNormalizationError("Phone number contains no digits.")
    if digits_only.startswith("+"):
        number_part = digits_only[1:]
        if not number_part.isdigit() or len(number_part) < 7 or len(number_part) > 15:
            raise LeadNormalizationError("Phone number has invalid length after country code.")
        return digits_only
    if len(digits_only) < 7 or len(digits_only) > 15:
        raise LeadNormalizationError("Phone number has invalid digit length.")
    if len(digits_only) == 11 and digits_only.startswith("1"):
        return "+" + digits_only
    if len(digits_only) == 10:
        return "+1" + digits_only
    return "+" + digits_only


def _normalize_email(raw: str | None) -> EmailStr | None:
    if raw is None:
        return None
    collapsed = _collapse_whitespace(raw)
    lowered = collapsed.lower()
    try:
        return _EMAIL_ADAPTER.validate_python(lowered)
    except ValidationError as exc:
        raise LeadNormalizationError("Email format is invalid.") from exc


def normalize_lead(inbound: InboundLead) -> NormalizedLead:
    first_name = _collapse_whitespace(inbound.first_name)
    last_name = _collapse_whitespace(inbound.last_name)
    preferred_city = _collapse_whitespace(inbound.preferred_city)

    if not first_name or not last_name or not preferred_city:
        raise LeadNormalizationError("First name, last name, and preferred city are required.")

    email = _normalize_email(inbound.email)
    phone = _normalize_phone(inbound.phone)

    if email is None and phone is None:
        raise LeadNormalizationError("At least one contact method (email or phone) is required.")

    if inbound.budget_max <= 0:
        raise LeadNormalizationError("budget_max must be a positive integer.")
    if inbound.min_bedrooms < 1 or inbound.min_bedrooms > 20:
        raise LeadNormalizationError("min_bedrooms must be between 1 and 20.")

    message = inbound.message
    cleaned_message = _collapse_whitespace(message) if message is not None else None

    return NormalizedLead(
        lead_id=_collapse_whitespace(inbound.lead_id),
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        preferred_city=preferred_city,
        budget_max=inbound.budget_max,
        min_bedrooms=inbound.min_bedrooms,
        has_message=bool(cleaned_message),
    )


def ensure_raises() -> NoReturn:
    raise LeadNormalizationError("Validation failure path.")
