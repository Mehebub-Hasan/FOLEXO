"""Vendor directory service: aggregated metrics, supplier history, CRUD.

Aggregation contract (Phase 6 step 1):
- invoice_count: ALL invoices linked to the vendor (any status),
- total_spend: sum of total_amount over APPROVED invoices only,
- last_invoice_date: max invoice_date across all linked invoices.
"""

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invoice, InvoiceStatus, Vendor
from app.modules.vendors.matcher import normalize_vendor_name
from app.modules.vendors.schemas import (
    VendorCreateRequest,
    VendorDetailResponse,
    VendorEntry,
    VendorInvoiceRow,
    VendorPatchRequest,
)

logger = logging.getLogger(__name__)


def _compose_address(vendor: Vendor) -> str | None:
    parts = [vendor.address_line1, vendor.address_line2, vendor.city, vendor.postal_code]
    composed = " ".join(part.strip() for part in parts if part and part.strip())
    return composed or None


class VendorNotFoundError(LookupError):
    """Vendor does not exist within the requested organization."""


async def _metrics_for(session: AsyncSession, org_id: uuid.UUID, vendor_id: uuid.UUID) -> tuple[int, Decimal | None, date | None]:
    row = (
        await session.execute(
            select(
                func.count(Invoice.id),
                func.sum(
                    case(
                        (Invoice.status == InvoiceStatus.APPROVED, Invoice.total_amount),
                        else_=None,
                    )
                ),
                func.max(Invoice.invoice_date),
            ).where(
                Invoice.organization_id == org_id,
                Invoice.vendor_id == vendor_id,
            )
        )
    ).one()
    return int(row[0] or 0), row[1], row[2]


async def list_vendors_with_metrics(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    search: str | None = None,
    limit: int = 50,
) -> list[VendorEntry]:
    statement = (
        select(
            Vendor,
            func.count(Invoice.id).label("invoice_count"),
            func.sum(
                case(
                    (Invoice.status == InvoiceStatus.APPROVED, Invoice.total_amount),
                    else_=None,
                )
            ).label("total_spend"),
            func.max(Invoice.invoice_date).label("last_invoice_date"),
        )
        .outerjoin(
            Invoice,
            and_(
                Invoice.vendor_id == Vendor.id,
                Invoice.organization_id == Vendor.organization_id,
            ),
        )
        .where(Vendor.organization_id == org_id)
        .group_by(Vendor.id)
        .order_by(Vendor.name)
        .limit(limit)
    )
    if search:
        needle = f"%{search.strip().lower()}%"
        statement = statement.where(
            Vendor.name.ilike(f"%{search}%") | Vendor.tax_id.ilike(needle)
        )

    rows = (await session.execute(statement)).all()
    return [
        VendorEntry(
            id=vendor.id,
            name=vendor.name,
            normalized_name=vendor.name_norm,
            email=vendor.email,
            address=_compose_address(vendor),
            tax_identifier=vendor.tax_id,
            default_currency=vendor.default_currency,
            invoice_count=invoice_count,
            total_spend=total_spend,
            last_invoice_date=last_invoice_date,
        )
        for vendor, invoice_count, total_spend, last_invoice_date in rows
    ]


async def get_vendor_with_invoices(
    session: AsyncSession,
    org_id: uuid.UUID,
    vendor_id: uuid.UUID,
    *,
    limit: int = 25,
    offset: int = 0,
) -> VendorDetailResponse | None:
    vendor = (
        await session.execute(
            select(Vendor).where(
                Vendor.id == vendor_id, Vendor.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    if vendor is None:
        return None

    invoice_count, total_spend, last_invoice_date = await _metrics_for(
        session, org_id, vendor_id
    )
    total = (
        await session.execute(
            select(func.count(Invoice.id)).where(
                Invoice.organization_id == org_id, Invoice.vendor_id == vendor_id
            )
        )
    ).scalar_one()

    invoice_rows = (
        (
            await session.execute(
                select(Invoice)
                .where(
                    Invoice.organization_id == org_id,
                    Invoice.vendor_id == vendor_id,
                )
                .order_by(Invoice.invoice_date.desc().nulls_last(), Invoice.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return VendorDetailResponse(
        id=vendor.id,
        name=vendor.name,
        normalized_name=vendor.name_norm,
        email=vendor.email,
        address=_compose_address(vendor),
        tax_identifier=vendor.tax_id,
        notes=vendor.notes,
        default_currency=vendor.default_currency,
        invoice_count=invoice_count,
        total_spend=total_spend,
        last_invoice_date=last_invoice_date,
        invoices=[
            VendorInvoiceRow(
                id=invoice.id,
                status=invoice.status,
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date,
                total_amount=invoice.total_amount,
                currency_code=invoice.currency_code,
                created_at=invoice.created_at,
            )
            for invoice in invoice_rows
        ],
        pagination={"total": int(total), "limit": limit, "offset": offset},
    )


class DuplicateVendorError(RuntimeError):
    """A vendor with the same normalized name already exists in the workspace."""


async def create_vendor(
    session: AsyncSession,
    org_id: uuid.UUID,
    payload: VendorCreateRequest,
    *,
    created_by_id: uuid.UUID | None = None,
) -> Vendor:
    vendor = Vendor(
        organization_id=org_id,
        name=payload.name.strip(),
        name_norm=normalize_vendor_name(payload.name),
        tax_id=(payload.tax_identifier.strip() if payload.tax_identifier else None),
        email=payload.email,
        address_line1=(payload.address.strip() if payload.address else None),
        default_currency=(
            payload.default_currency.strip().upper()
            if payload.default_currency
            else None
        ),
        created_by_id=created_by_id,
    )
    session.add(vendor)
    await session.flush()
    logger.info("vendor created: %s (org %s)", vendor.name_norm, org_id)
    return vendor


async def update_vendor(
    session: AsyncSession,
    org_id: uuid.UUID,
    vendor_id: uuid.UUID,
    payload: VendorPatchRequest,
) -> Vendor | None:
    vendor = (
        await session.execute(
            select(Vendor).where(
                Vendor.id == vendor_id, Vendor.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    if vendor is None:
        return None

    provided = payload.model_fields_set
    if "email" in provided:
        vendor.email = payload.email
    if "address" in provided:
        vendor.address_line1 = payload.address
    if "tax_identifier" in provided:
        vendor.tax_id = payload.tax_identifier
    if "notes" in provided:
        vendor.notes = payload.notes
    await session.commit()
    await session.refresh(vendor)
    return vendor
