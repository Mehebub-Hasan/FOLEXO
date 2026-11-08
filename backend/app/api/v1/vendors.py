"""Vendor directory endpoints (minimal Phase 5 scope: list + create).

Powers the review page's vendor selector and quick-create modal. Full CRUD
(archive, merge, payment terms editing) arrives with the vendors page.
"""

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_workspace, require_write_role
from app.core.security import UserPayload
from app.database.session import get_session
from app.models import Organization, OrganizationMember, Vendor

router = APIRouter(prefix="/vendors", tags=["vendors"])


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = None
    email: str | None = None
    default_currency: str | None = Field(default=None, max_length=3)


class VendorOut(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None
    email: str | None
    default_currency: str | None
    created_at: datetime


@router.get("", response_model=list[VendorOut])
async def list_vendors(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[VendorOut]:
    """Vendor directory for the workspace (name-substring search, ≤ 100 rows)."""
    statement = select(Vendor).where(Vendor.organization_id == workspace.id)
    if q:
        statement = statement.where(Vendor.name_norm.ilike(f"%{q.lower()}%"))
    statement = statement.order_by(Vendor.name).limit(100)
    vendors = (await session.execute(statement)).scalars().all()
    return [
        VendorOut(
            id=vendor.id,
            name=vendor.name,
            tax_id=vendor.tax_id,
            email=vendor.email,
            default_currency=vendor.default_currency,
            created_at=vendor.created_at,
        )
        for vendor in vendors
    ]


@router.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    payload: VendorCreate,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    member: Annotated[OrganizationMember, Depends(require_write_role)],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VendorOut:
    vendor = Vendor(
        organization_id=workspace.id,
        name=payload.name,
        name_norm=re.sub(r"[^a-z0-9]+", "-", payload.name.strip().lower()).strip("-"),
        tax_id=payload.tax_id,
        email=payload.email,
        default_currency=payload.default_currency,
        created_by_id=current_user.id,
    )
    session.add(vendor)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A vendor with this name already exists in the workspace.",
        ) from exc
    await session.refresh(vendor)
    return VendorOut(
        id=vendor.id,
        name=vendor.name,
        tax_id=vendor.tax_id,
        email=vendor.email,
        default_currency=vendor.default_currency,
        created_at=vendor.created_at,
    )
