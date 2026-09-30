from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Header, Request

from app.observability.logging import SafeLoggerAdapter, new_logger


def correlation_id(
    x_correlation_id: Annotated[str | None, Header()] = None,
) -> str:
    if x_correlation_id:
        stripped = x_correlation_id.strip()
        if stripped:
            return stripped
    return str(uuid.uuid4())


def request_logger(
    request: Request,
    correlation_id: Annotated[str, Header(alias="X-Correlation-ID")] = "",
) -> SafeLoggerAdapter:
    applied_cid: str = getattr(request.state, "correlation_id", "") or str(uuid.uuid4())
    return new_logger(correlation_id=applied_cid)


CorrelationID = Annotated[str, Header(alias="X-Correlation-ID")]
