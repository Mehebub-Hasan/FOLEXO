"""Workspace (organization) endpoints.

Phase 2 scope: workspace creation with automatic owner enrollment, the
``/auth/me`` hydration endpoint, and ``/workspaces/current`` — the minimal
consumer of the ``get_current_workspace`` guardrail (frontend workspace
switching hydrates against it in Phase 6).

Error envelope + richer membership endpoints (invite/role change) arrive in
Phase 3 per docs/API.md; these routes return plain HTTPException details for now.
"""

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_workspace
from app.core.security import UserPayload
from app.database.session import get_session
from app.models import Organization, OrganizationMember, OrganizationRole

router = APIRouter(tags=["workspaces"])


# ── Schemas ──────────────────────────────────────────────────────────────


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$",
        description="Derived from name when omitted.",
    )
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    default_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class WorkspaceOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    slug: str
    country_code: str | None
    default_currency: str
    created_at: datetime


class WorkspaceWithRole(WorkspaceOut):
    role: OrganizationRole


class ProfileOut(BaseModel):
    id: uuid.UUID
    email: str


class AuthMeResponse(BaseModel):
    profile: ProfileOut
    workspaces: list[WorkspaceWithRole]


# ── Helpers ──────────────────────────────────────────────────────────────

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_INVALID_CHARS.sub("-", name.lower()).strip("-")
    return slug or "workspace"


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/auth/me", response_model=AuthMeResponse)
async def auth_me(
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthMeResponse:
    """Authenticated profile + every workspace the user belongs to."""
    rows = (
        await session.execute(
            select(Organization, OrganizationMember.role)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == current_user.id)
            .order_by(OrganizationMember.created_at)
        )
    ).all()
    workspaces = [
        WorkspaceWithRole(
            id=org.id,
            name=org.name,
            slug=org.slug,
            country_code=org.country_code,
            default_currency=org.default_currency,
            created_at=org.created_at,
            role=role,
        )
        for org, role in rows
    ]
    return AuthMeResponse(
        profile=ProfileOut(id=current_user.id, email=current_user.email),
        workspaces=workspaces,
    )


@router.post("/workspaces", response_model=WorkspaceWithRole, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceWithRole:
    """Create a workspace; the creator is enrolled as ``owner`` atomically."""
    workspace = Organization(
        name=payload.name,
        slug=payload.slug or _slugify(payload.name),
        country_code=payload.country_code,
        default_currency=payload.default_currency,
        created_by_id=current_user.id,
    )
    session.add(workspace)
    try:
        # Flush inside the guard: slug-uniqueness violations surface here,
        # not at commit — both must map to 409.
        await session.flush()
        session.add(
            OrganizationMember(
                organization_id=workspace.id,
                user_id=current_user.id,
                role=OrganizationRole.OWNER,
            )
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspace slug already exists.",
        ) from exc
    await session.refresh(workspace)

    return WorkspaceWithRole(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        country_code=workspace.country_code,
        default_currency=workspace.default_currency,
        created_at=workspace.created_at,
        role=OrganizationRole.OWNER,
    )


@router.get("/workspaces/current", response_model=WorkspaceOut)
async def get_current_workspace_details(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
) -> Organization:
    """Details of the workspace addressed by ``X-Workspace-ID``.

    403 unless the caller is a member — the primary isolation guardrail probe.
    """
    return workspace
