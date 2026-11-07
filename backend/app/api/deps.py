"""Reusable FastAPI dependencies — the auth + tenant-scoping guardrails.

Contract (docs/API.md §1.2, ADR-004):
- Identity comes from the Supabase-issued Bearer JWT, verified locally.
- Workspace context comes from the ``X-Workspace-ID`` header and is accepted
  ONLY if a matching ``organization_members`` row exists for the JWT subject.
  A JWT alone grants nothing: membership in the database is the source of
  truth, re-checked on every request.
- Cross-tenant ids are indistinguishable from nonexistent ones (403 for both)
  so the API never confirms the existence of another tenant's resources.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import UserPayload, verify_supabase_token
from app.database.session import get_session
from app.models import Organization, OrganizationMember

_401_HEADERS = {"WWW-Authenticate": "Bearer"}


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> UserPayload:
    """Verify the ``Authorization: Bearer <jwt>`` header → :class:`UserPayload`.

    401 for missing, malformed, invalid, or expired tokens.
    """
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers=_401_HEADERS,
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_supabase_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=_401_HEADERS,
        ) from exc


async def get_current_workspace(
    x_workspace_id: Annotated[uuid.UUID, Header(alias="X-Workspace-ID")],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Organization:
    """Resolve ``X-Workspace-ID`` to an :class:`Organization` the caller belongs to.

    403 ("Access to this workspace is forbidden.") when the authenticated user
    has no membership row for the requested organization — this covers both
    nonexistent ids and another tenant's ids.
    """
    statement = (
        select(Organization)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(
            Organization.id == x_workspace_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    workspace = (await session.execute(statement)).scalar_one_or_none()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to this workspace is forbidden.",
        )
    return workspace
