"""Dashboard summary schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models import InvoiceStatus


class RecentInvoice(BaseModel):
    id: uuid.UUID
    vendor_name: str | None
    invoice_number: str | None
    total_amount: Decimal | None
    currency_code: str | None
    status: InvoiceStatus
    created_at: datetime


class VendorSpend(BaseModel):
    vendor_name: str
    total_spend: Decimal
    currency: str  # workspace base currency


class DashboardSummary(BaseModel):
    total_invoices_count: int
    pending_review_count: int
    approved_invoices_count: int
    failed_invoices_count: int
    total_approved_spend: Decimal | None
    base_currency: str
    unpriced_currencies: list[str] = []
    recent_activity: list[RecentInvoice]
    spend_by_vendor: list[VendorSpend]
