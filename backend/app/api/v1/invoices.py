"""Invoice ingestion + listing.

POST /api/v1/invoices (step 2 of the upload flow): called by the client AFTER
it has pushed the file bytes to storage using the presigned upload URL.
Creates the `document_files` record (metadata for the stored object) and the
`invoices` row in status `uploaded`, plus an audit entry. Extraction is
enqueued in Phase 5 — the invoice simply waits in `uploaded` until then.

GET /api/v1/invoices: keyset-paginated inbox listing scoped to the caller's
workspace, with comma-separated status filtering (docs/API.md §1.5 envelope).
"""

import base64
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_workspace
from app.core.security import UserPayload
from app.database.session import get_session
from app.models import (
    AuditLog,
    DocumentFile,
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceStatus,
    Organization,
    Vendor,
)
from app.modules.uploads.service import (
    validate_upload_constraints,
)
from app.workers.enqueue import get_enqueue_process_invoice

router = APIRouter(tags=["invoices"])


class InvoiceCreateRequest(BaseModel):
    storage_path: str = Field(min_length=1)
    file_hash: str = Field(description="Hex-encoded SHA-256 digest of the uploaded file")
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1)
    file_size_bytes: int = Field(gt=0)

    @field_validator("file_hash")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("file_hash must be a 64-character hex-encoded SHA-256 digest")
        return normalized


class DocumentFileOut(BaseModel):
    id: uuid.UUID
    storage_path: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    content_hash: str
    created_at: datetime


class InvoiceOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    document_id: uuid.UUID
    vendor_id: uuid.UUID | None
    status: InvoiceStatus
    created_at: datetime
    updated_at: datetime


class InvoiceCreateResponse(BaseModel):
    invoice: InvoiceOut
    document: DocumentFileOut


@router.post(
    "/invoices",
    response_model=InvoiceCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invoice(
    payload: InvoiceCreateRequest,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    enqueue: Annotated[
        Callable[[str], Awaitable[bool]],
        Depends(get_enqueue_process_invoice),
    ],
) -> InvoiceCreateResponse:
    # Re-validate upload constraints — the confirm step is reachable without
    # having gone through presign, so it cannot trust the earlier call.
    validate_upload_constraints(payload.file_size_bytes, payload.mime_type)

    # Tenant-scoping guardrail: the stored object must live inside the
    # caller's workspace prefix.
    if not payload.storage_path.startswith(f"{workspace.id}/"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="storage_path is outside the current workspace.",
        )

    document = DocumentFile(
        id=uuid.uuid4(),  # explicit: PK defaults apply at flush, and the invoice below needs the id now
        organization_id=workspace.id,
        storage_path=payload.storage_path,
        original_filename=payload.original_filename,
        mime_type=payload.mime_type,
        file_size_bytes=payload.file_size_bytes,
        content_hash=payload.file_hash,
        uploaded_by_id=current_user.id,
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=workspace.id,
        document_id=document.id,
        status=InvoiceStatus.UPLOADED,
    )
    audit_entry = AuditLog(
        organization_id=workspace.id,
        actor_id=current_user.id,
        action="invoice.uploaded",
        entity_type="invoice",
        entity_id=invoice.id,
        meta={
            "original_filename": payload.original_filename,
            "file_size_bytes": payload.file_size_bytes,
            "mime_type": payload.mime_type,
            "sha256": payload.file_hash,
        },
    )
    session.add_all([document, invoice, audit_entry])
    await session.commit()

    # Server-managed columns (created_at/updated_at) are populated by the INSERT.
    await session.refresh(document)
    await session.refresh(invoice)

    # Hand off to the extraction pipeline. Redis unavailability must never
    # fail an upload: the invoice stays `uploaded` and is picked up later
    # (sweeper / manual reprocess).
    await enqueue(str(invoice.id))

    return InvoiceCreateResponse(
        invoice=InvoiceOut(
            id=invoice.id,
            organization_id=invoice.organization_id,
            document_id=invoice.document_id,
            vendor_id=invoice.vendor_id,
            status=invoice.status,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
        ),
        document=DocumentFileOut(
            id=document.id,
            storage_path=document.storage_path,
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            file_size_bytes=document.file_size_bytes,
            content_hash=document.content_hash,
            created_at=document.created_at,
        ),
    )


# ── GET /api/v1/invoices — inbox listing ─────────────────────────────────


class InvoiceListItem(BaseModel):
    id: uuid.UUID
    status: InvoiceStatus
    original_filename: str
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    currency_code: str | None
    created_at: datetime


class Pagination(BaseModel):
    next_cursor: str | None
    has_more: bool


class ListInvoicesResponse(BaseModel):
    data: list[InvoiceListItem]
    pagination: Pagination


def _encode_cursor(created_at: datetime, invoice_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{invoice_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_raw, id_raw = raw.rsplit("|", 1)
        return datetime.fromisoformat(created_raw), uuid.UUID(id_raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor.",
        ) from exc


def _parse_status_filter(status_param: str | None) -> list[InvoiceStatus]:
    if not status_param:
        return []
    statuses: list[InvoiceStatus] = []
    for part in status_param.split(","):
        part = part.strip().lower()
        if not part:
            continue
        try:
            statuses.append(InvoiceStatus(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown invoice status: '{part}'.",
            ) from exc
    return statuses


@router.get("/invoices", response_model=ListInvoicesResponse)
async def list_invoices(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> ListInvoicesResponse:
    predicates: list = [Invoice.organization_id == workspace.id]
    statuses = _parse_status_filter(status_filter)
    if statuses:
        predicates.append(Invoice.status.in_(statuses))
    if cursor:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        predicates.append(
            or_(
                Invoice.created_at < cursor_created_at,
                and_(Invoice.created_at == cursor_created_at, Invoice.id < cursor_id),
            )
        )

    statement = (
        select(Invoice, DocumentFile.original_filename, Vendor.name)
        .join(DocumentFile, DocumentFile.id == Invoice.document_id)
        .outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
        .where(*predicates)
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .limit(limit + 1)
    )
    rows = (await session.execute(statement)).all()

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(page[-1].Invoice.created_at, page[-1].Invoice.id) if has_more and page else None
    )

    items = [
        InvoiceListItem(
            id=invoice.id,
            status=invoice.status,
            original_filename=filename,
            vendor_name=vendor_name,
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            total_amount=invoice.total_amount,
            currency_code=invoice.currency_code,
            created_at=invoice.created_at,
        )
        for invoice, filename, vendor_name in page
    ]
    return ListInvoicesResponse(
        data=items, pagination=Pagination(next_cursor=next_cursor, has_more=has_more)
    )


# ── GET /api/v1/invoices/{id}/status — polling for the pipeline ──────────


_STATUS_PROGRESS: dict[InvoiceStatus, int] = {
    InvoiceStatus.UPLOADED: 0,
    InvoiceStatus.PROCESSING: 50,
}


class InvoiceStatusResponse(BaseModel):
    invoice_id: uuid.UUID
    status: InvoiceStatus
    progress_percent: int
    error_message: str | None = None


@router.get("/invoices/{invoice_id}/status", response_model=InvoiceStatusResponse)
async def get_invoice_status(
    invoice_id: uuid.UUID,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvoiceStatusResponse:
    """Pipeline status for one invoice — polled every ~2 s while pending.

    Cross-tenant (and unknown) ids return 404, never confirming existence.
    """
    row = (
        await session.execute(
            select(Invoice.status).where(
                Invoice.id == invoice_id,
                Invoice.organization_id == workspace.id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )
    invoice_status = row[0]

    error_message: str | None = None
    if invoice_status == InvoiceStatus.FAILED:
        error_message = (
            await session.execute(
                select(ExtractionRun.error_message)
                .where(
                    ExtractionRun.invoice_id == invoice_id,
                    ExtractionRun.status == ExtractionRunStatus.FAILED,
                )
                .order_by(ExtractionRun.finished_at.desc().nulls_last())
                .limit(1)
            )
        ).scalar_one_or_none()

    return InvoiceStatusResponse(
        invoice_id=invoice_id,
        status=invoice_status,
        progress_percent=_STATUS_PROGRESS.get(invoice_status, 100),
        error_message=error_message,
    )
