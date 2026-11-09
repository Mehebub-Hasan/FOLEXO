"""Vendors router — directory, supplier history, manual CRUD.

Mounted under /api/v1/vendors (main.py).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_workspace, require_write_role
from app.core.security import UserPayload
from app.database.session import get_session
from app.models import Organization, OrganizationMember
from app.modules.vendors.schemas import (
    VendorCreateRequest,
    VendorDetailResponse,
    VendorEntry,
    VendorPatchRequest,
)
from app.modules.vendors.service import (
    DuplicateVendorError,
    VendorNotFoundError,
    create_vendor as create_vendor_record,
    get_vendor_with_invoices,
    list_vendors_with_metrics,
    update_vendor,
)

router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.get("", response_model=list[VendorEntry])
async def list_vendors(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[VendorEntry]:
    """Directory rows with aggregated metrics (invoice_count, approved-only
    total_spend, last_invoice_date). Search matches name or tax identifier."""
    return await list_vendors_with_metrics(session, workspace.id, search=search, limit=limit)


@router.get("/{vendor_id}", response_model=VendorDetailResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VendorDetailResponse:
    """Vendor details + paginated invoice history.

    Foreign-workspace ids are indistinguishable from unknown ones (404)."""
    detail = await get_vendor_with_invoices(
        session, workspace.id, vendor_id, limit=limit, offset=offset
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found."
        )
    return detail


@router.post("", response_model=VendorEntry, status_code=status.HTTP_201_CREATED)
async def create_vendor_endpoint(
    payload: VendorCreateRequest,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    member: Annotated[OrganizationMember, Depends(require_write_role)],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VendorEntry:
    """Manual vendor creation with normalized-name duplicate protection."""
    try:
        vendor = await create_vendor_record(
            session,
            workspace.id,
            payload,
            created_by_id=current_user.id,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A vendor with this name already exists in the workspace.",
        ) from exc
    await session.refresh(vendor)
    return VendorEntry(
        id=vendor.id,
        name=vendor.name,
        normalized_name=vendor.name_norm,
        email=vendor.email,
        address=vendor.address_line1,
        tax_identifier=vendor.tax_id,
        default_currency=vendor.default_currency,
        invoice_count=0,
        total_spend=None,
        last_invoice_date=None,
    )


@router.patch("/{vendor_id}", response_model=VendorDetailResponse)
async def patch_vendor(
    vendor_id: uuid.UUID,
    payload: VendorPatchRequest,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    member: Annotated[OrganizationMember, Depends(require_write_role)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VendorDetailResponse:
    vendor = await update_vendor(session, workspace.id, vendor_id, payload)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found."
        )
    detail = await get_vendor_with_invoices(session, workspace.id, vendor_id)
    if detail is None:  # pragma: no cover — vendor just updated in this org
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found."
        )
    return detail
