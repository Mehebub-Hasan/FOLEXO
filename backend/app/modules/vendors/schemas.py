"""Vendor API schemas.

Field naming follows the API contract (tax_identifier, normalized_name,
address) and maps onto the storage columns (tax_id, name_norm, address_*).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models import InvoiceStatus


class VendorEntry(BaseModel):
    """Directory row with aggregated invoice metrics."""

    id: uuid.UUID
    name: str
    normalized_name: str
    email: str | None
    address: str | None
    tax_identifier: str | None
    default_currency: str | None
    invoice_count: int
    total_spend: Decimal | None
    last_invoice_date: date | None


class VendorInvoiceRow(BaseModel):
    id: uuid.UUID
    status: InvoiceStatus
    invoice_number: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    currency_code: str | None
    created_at: datetime


class VendorDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    normalized_name: str
    email: str | None
    address: str | None
    tax_identifier: str | None
    notes: str | None
    default_currency: str | None
    invoice_count: int
    total_spend: Decimal | None
    last_invoice_date: date | None
    invoices: list[VendorInvoiceRow]
    pagination: dict[str, int]


class VendorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tax_identifier: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    default_currency: str | None = Field(default=None, max_length=3)


class VendorPatchRequest(BaseModel):
    email: str | None = None
    address: str | None = None
    tax_identifier: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
