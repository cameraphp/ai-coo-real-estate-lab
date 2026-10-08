"""Static demo identities.

This is NOT production authentication. The ``X-Demo-Actor-ID`` header is an
unauthenticated, client-asserted label that maps to a fixed local principal so
that the tenant and role boundaries are visible and testable. Never expose this
service to the public internet.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.models import DemoPrincipal, Role


class UnknownDemoActorError(LookupError):
    pass


DEMO_PRINCIPALS: Mapping[str, DemoPrincipal] = MappingProxyType(
    {
        principal.actor_id: principal
        for principal in (
            DemoPrincipal(actor_id="tenant-a-operator", tenant_id="tenant-a", role=Role.OPERATOR),
            DemoPrincipal(actor_id="tenant-a-approver", tenant_id="tenant-a", role=Role.APPROVER),
            DemoPrincipal(actor_id="tenant-a-viewer", tenant_id="tenant-a", role=Role.VIEWER),
            DemoPrincipal(actor_id="tenant-b-operator", tenant_id="tenant-b", role=Role.OPERATOR),
        )
    }
)


def resolve_demo_principal(actor_id: str | None) -> DemoPrincipal:
    """Map a demo actor ID to its tenant-bound principal. Tenant is never client-supplied."""
    if actor_id is None:
        raise UnknownDemoActorError("X-Demo-Actor-ID header is required.")
    principal = DEMO_PRINCIPALS.get(actor_id.strip())
    if principal is None:
        raise UnknownDemoActorError("X-Demo-Actor-ID is not a known demo actor.")
    return principal
