from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status

from app.domain.models import DemoPrincipal, ErrorResponse
from app.observability.logging import SafeLoggerAdapter, new_logger
from app.security.demo_identity import UnknownDemoActorError, resolve_demo_principal

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]+$"
IDEMPOTENCY_KEY_MIN_LENGTH = 8
IDEMPOTENCY_KEY_MAX_LENGTH = 128


def resolve_correlation_id(raw: str | None) -> str:
    if raw:
        stripped = raw.strip()
        if stripped:
            return stripped
    return str(uuid.uuid4())


def correlation_id(
    x_correlation_id: Annotated[str | None, Header()] = None,
) -> str:
    return resolve_correlation_id(x_correlation_id)


def request_logger(
    request: Request,
    correlation_id: Annotated[str, Header(alias="X-Correlation-ID")] = "",
) -> SafeLoggerAdapter:
    applied_cid: str = getattr(request.state, "correlation_id", "") or str(uuid.uuid4())
    return new_logger(correlation_id=applied_cid)


def api_error(
    status_code: int,
    correlation_id: str,
    error: str,
    detail: str | None = None,
    **extra: Any,
) -> HTTPException:
    """Build the project's safe error envelope: ``{"detail": ErrorResponse}``."""
    body = ErrorResponse(correlation_id=correlation_id, error=error, detail=detail, **extra)
    return HTTPException(
        status_code=status_code,
        detail=body.model_dump(mode="json", exclude_none=True),
        headers={"X-Correlation-ID": correlation_id},
    )


def demo_principal(
    cid: Annotated[str, Depends(correlation_id)],
    x_demo_actor_id: Annotated[str | None, Header(alias="X-Demo-Actor-ID")] = None,
) -> DemoPrincipal:
    """Resolve the static demo actor. This is NOT production authentication."""
    try:
        return resolve_demo_principal(x_demo_actor_id)
    except UnknownDemoActorError as exc:
        raise api_error(status.HTTP_401_UNAUTHORIZED, cid, "unknown_demo_actor", str(exc)) from exc


CorrelationID = Annotated[str, Header(alias="X-Correlation-ID")]
