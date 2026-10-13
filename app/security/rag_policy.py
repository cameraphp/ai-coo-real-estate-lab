"""Role allowlist for the read-only RAG endpoints.

RAG is read-only and tenant-scoped, so every demo role may use it for its own
tenant. The allowlist is explicit so that a future role is denied by default.
"""

from __future__ import annotations

from app.domain.models import DemoPrincipal, Role
from app.security.tool_policy import AuthorizationError

RAG_QUERY_ROLES: frozenset[Role] = frozenset({Role.OPERATOR, Role.APPROVER, Role.VIEWER})


def authorize_rag_query(principal: DemoPrincipal) -> None:
    if principal.role not in RAG_QUERY_ROLES:
        raise AuthorizationError(f"Role '{principal.role.value}' may not query the RAG index.")
