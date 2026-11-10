"""Dashboard aggregation service.

All monetary aggregation happens in SQL per (vendor, currency) group and is
converted to the workspace base currency in Python via the static FX table
(see fx.py). Unpriced currencies are reported, never guessed.
"""

import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy import Numeric as SANumeric
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invoice, InvoiceStatus, Organization, Vendor
from app.modules.dashboard.fx import convert_to_base
from app.modules.dashboard.schemas import (
    DashboardSummary,
    RecentInvoice,
    VendorSpend,
)

_SPEND_TYPE = SANumeric(15, 4)


async def dashboard_summary(
    session: AsyncSession, organization: Organization
) -> DashboardSummary:
    org_id = organization.id
    base_currency = (organization.default_currency or "USD").upper()

    # ── Status counts ──
    status_counts: dict[InvoiceStatus, int] = {}
    count_rows = (
        await session.execute(
            select(Invoice.status, func.count()).where(
                Invoice.organization_id == org_id
            ).group_by(Invoice.status)
        )
    ).all()
    for status_value, count in count_rows:
        status_counts[status_value] = int(count)
    total_count = sum(status_counts.values())

    # ── Approved spend by currency (converted to base in Python) ──
    currency_rows = (
        await session.execute(
            select(
                Invoice.currency_code,
                func.sum(Invoice.total_amount, type_=_SPEND_TYPE),
            )
            .where(
                Invoice.organization_id == org_id,
                Invoice.status == InvoiceStatus.APPROVED,
            )
            .group_by(Invoice.currency_code)
        )
    ).all()
    unpriced: list[str] = []
    total_approved_spend = Decimal("0")
    has_spend = False
    for currency_code, amount in currency_rows:
        if amount is None:
            continue
        converted = convert_to_base(amount, currency_code, base_currency)
        if converted is None:
            unpriced.append((currency_code or "unspecified").upper())
            continue
        total_approved_spend += converted
        has_spend = True

    # ── Recent activity (last 5) ──
    recent_rows = (
        await session.execute(
            select(Invoice, Vendor.name)
            .outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
            .where(Invoice.organization_id == org_id)
            .order_by(Invoice.created_at.desc())
            .limit(5)
        )
    ).all()
    recent_activity = [
        RecentInvoice(
            id=invoice.id,
            vendor_name=vendor_name,
            invoice_number=invoice.invoice_number,
            total_amount=invoice.total_amount,
            currency_code=invoice.currency_code,
            status=invoice.status,
            created_at=invoice.created_at,
        )
        for invoice, vendor_name in recent_rows
    ]

    # ── Top 5 suppliers by approved spend (per-vendor, per-currency groups,
    #    converted then merged in Python) ──
    vendor_rows = (
        await session.execute(
            select(
                Vendor.name,
                Invoice.currency_code,
                func.sum(Invoice.total_amount, type_=_SPEND_TYPE),
            )
            .join(Vendor, Vendor.id == Invoice.vendor_id)
            .where(
                Invoice.organization_id == org_id,
                Invoice.status == InvoiceStatus.APPROVED,
                Invoice.vendor_id.isnot(None),
            )
            .group_by(Vendor.name, Invoice.currency_code)
        )
    ).all()
    spend_by_vendor_name: dict[str, Decimal] = {}
    for vendor_name, currency_code, amount in vendor_rows:
        if amount is None:
            continue
        converted = convert_to_base(amount, currency_code, base_currency)
        if converted is None:
            if (currency_code or "unspecified").upper() not in unpriced:
                unpriced.append((currency_code or "unspecified").upper())
            continue
        spend_by_vendor_name[vendor_name] = (
            spend_by_vendor_name.get(vendor_name, Decimal("0")) + converted
        )
    top_vendors = [
        VendorSpend(vendor_name=name, total_spend=amount, currency=base_currency)
        for name, amount in sorted(
            spend_by_vendor_name.items(), key=lambda item: item[1], reverse=True
        )[:5]
    ]

    return DashboardSummary(
        total_invoices_count=total_count,
        pending_review_count=status_counts.get(InvoiceStatus.NEEDS_REVIEW, 0),
        approved_invoices_count=status_counts.get(InvoiceStatus.APPROVED, 0),
        failed_invoices_count=status_counts.get(InvoiceStatus.FAILED, 0),
        total_approved_spend=total_approved_spend if has_spend else None,
        base_currency=base_currency,
        unpriced_currencies=sorted(set(unpriced)),
        recent_activity=recent_activity,
        spend_by_vendor=top_vendors,
    )
