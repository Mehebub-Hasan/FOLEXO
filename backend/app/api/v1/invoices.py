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
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_workspace, require_write_role
from app.core.config import settings
from app.core.security import UserPayload
from app.database.session import get_session
from app.models import (
    AuditLog,
    DocumentFile,
    ExtractionEngine,
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Organization,
    OrganizationMember,
    Vendor,
)
from app.modules.uploads.service import (
    StorageClient,
    StorageError,
    get_storage_client,
    validate_upload_constraints,
)
from app.modules.duplicates.detector import check_duplicates
from app.modules.validation.engine import validate_invoice_financials
from app.core.rate_limit import reprocess_rate_limit
from app.workers.enqueue import get_enqueue_process_invoice

router = APIRouter(tags=["invoices"])

logger = logging.getLogger(__name__)


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
    InvoiceStatus.QUEUED: 0,
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


# ── Review & approval workflow (Phase 5 step 2) ──────────────────────────

DOWNLOAD_URL_TTL_SECONDS = 900  # 15 minutes
_WRITABLE_STATUSES = (InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.READY)
_REPROCESSABLE_STATUSES = (InvoiceStatus.FAILED, InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.READY)


class VendorOut(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None
    email: str | None
    default_currency: str | None


class LineItemOut(BaseModel):
    id: uuid.UUID
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    tax_rate: Decimal | None
    line_total: Decimal | None
    page_number: int | None
    confidence: Decimal | None
    bbox: dict[str, Any] | None
    source_engine: ExtractionEngine | None
    is_corrected: bool


class InvoiceFullOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    document_id: uuid.UUID
    vendor_id: uuid.UUID | None
    status: InvoiceStatus
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency_code: str | None
    po_number: str | None
    subtotal_amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    extraction_engine: ExtractionEngine | None
    confidence_min: Decimal | None
    confidence_avg: Decimal | None
    is_possible_duplicate: bool
    validation_errors: list[dict[str, Any]] | None
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class InvoiceDetailResponse(BaseModel):
    invoice: InvoiceFullOut
    vendor: VendorOut | None
    document: DocumentFileOut
    line_items: list[LineItemOut]
    file_url: str
    field_boxes: dict[str, Any] | None


async def _load_field_boxes(session: AsyncSession, invoice_id: uuid.UUID) -> dict[str, Any] | None:
    normalized = (
        await session.execute(
            select(ExtractionRun.normalized_output)
            .where(
                ExtractionRun.invoice_id == invoice_id,
                ExtractionRun.status == ExtractionRunStatus.SUCCEEDED,
            )
            .order_by(ExtractionRun.finished_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    if isinstance(normalized, dict):
        boxes = normalized.get("bounding_boxes")
        return boxes if isinstance(boxes, dict) else None
    return None


async def _build_detail(
    session: AsyncSession, invoice: Invoice, storage: StorageClient
) -> InvoiceDetailResponse:
    """Assemble the full review payload (header, vendor, items, signed URL)."""
    vendor = await session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    document = await session.get(DocumentFile, invoice.document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invoice document record is missing.",
        )
    items = (
        (
            await session.execute(
                select(InvoiceItem)
                .where(InvoiceItem.invoice_id == invoice.id)
                .order_by(InvoiceItem.line_number)
            )
        )
        .scalars()
        .all()
    )
    field_boxes = await _load_field_boxes(session, invoice.id)

    try:
        file_url = await storage.create_signed_download_url(
            bucket=settings.supabase_storage_bucket,
            path=document.storage_path,
            expires_in_seconds=DOWNLOAD_URL_TTL_SECONDS,
        )
    except StorageError:
        # Storage outage must not hide the review data — the URL is a
        # convenience, the structured payload is the product.
        logger.warning("could not sign download URL for invoice %s", invoice.id)
        file_url = ""

    return InvoiceDetailResponse(
        invoice=InvoiceFullOut(
            id=invoice.id,
            organization_id=invoice.organization_id,
            document_id=invoice.document_id,
            vendor_id=invoice.vendor_id,
            status=invoice.status,
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            currency_code=invoice.currency_code,
            po_number=invoice.po_number,
            subtotal_amount=invoice.subtotal_amount,
            tax_amount=invoice.tax_amount,
            total_amount=invoice.total_amount,
            extraction_engine=invoice.extraction_engine,
            confidence_min=invoice.confidence_min,
            confidence_avg=invoice.confidence_avg,
            is_possible_duplicate=invoice.is_possible_duplicate,
            validation_errors=invoice.validation_errors,
            approved_by_id=invoice.approved_by_id,
            approved_at=invoice.approved_at,
            rejection_reason=invoice.rejection_reason,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
        ),
        vendor=VendorOut(
            id=vendor.id,
            name=vendor.name,
            tax_id=vendor.tax_id,
            email=vendor.email,
            default_currency=vendor.default_currency,
        )
        if vendor
        else None,
        document=DocumentFileOut(
            id=document.id,
            storage_path=document.storage_path,
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            file_size_bytes=document.file_size_bytes,
            content_hash=document.content_hash,
            created_at=document.created_at,
        ),
        line_items=[
            LineItemOut(
                id=item.id,
                line_number=item.line_number,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                tax_rate=item.tax_rate,
                line_total=item.line_total,
                page_number=item.page_number,
                confidence=item.confidence,
                bbox=item.bbox,
                source_engine=item.source_engine,
                is_corrected=item.is_corrected,
            )
            for item in items
        ],
        file_url=file_url,
        field_boxes=field_boxes,
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_invoice_detail(
    invoice_id: uuid.UUID,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> InvoiceDetailResponse:
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None or invoice.organization_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    return await _build_detail(session, invoice, storage)


class PatchLineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class PatchInvoiceRequest(BaseModel):
    vendor_id: uuid.UUID | None = None
    invoice_number: str | None = Field(default=None, max_length=255)
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, max_length=3)
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    line_items: list[PatchLineItem] | None = None


async def _apply_patch(
    invoice: Invoice, payload: PatchInvoiceRequest, session: AsyncSession
) -> list[InvoiceItem]:
    """Apply provided fields only (model_fields_set distinguishes absent vs null)."""
    provided = payload.model_fields_set

    if "vendor_id" in provided:
        if payload.vendor_id is not None:
            vendor = (
                await session.execute(
                    select(Vendor).where(
                        Vendor.id == payload.vendor_id,
                        Vendor.organization_id == invoice.organization_id,
                    )
                )
            ).scalar_one_or_none()
            if vendor is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Unknown vendor for this workspace.",
                )
        invoice.vendor_id = payload.vendor_id
    if "invoice_number" in provided:
        invoice.invoice_number = payload.invoice_number
    if "invoice_date" in provided:
        invoice.invoice_date = payload.invoice_date
    if "due_date" in provided:
        invoice.due_date = payload.due_date
    if "currency" in provided:
        normalized = (payload.currency or "").strip().upper()
        if normalized and not re.fullmatch(r"[A-Z]{3}", normalized):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="currency must be an ISO 4217 code.",
            )
        invoice.currency_code = normalized or None
    if "subtotal" in provided:
        invoice.subtotal_amount = payload.subtotal
    if "tax_amount" in provided:
        invoice.tax_amount = payload.tax_amount
    if "total_amount" in provided:
        invoice.total_amount = payload.total_amount

    # Line items: provided list REPLACES the rows; human-edited rows are
    # marked corrected so a later reprocess preserves them (FR-9).
    if payload.line_items is None:
        return []
    await session.execute(
        delete(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id)
    )
    rows: list[InvoiceItem] = []
    for position, item in enumerate(payload.line_items, start=1):
        row = InvoiceItem(
            organization_id=invoice.organization_id,
            invoice_id=invoice.id,
            line_number=position,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
            source_engine=ExtractionEngine.MANUAL,
            is_corrected=True,
        )
        rows.append(row)
        session.add(row)
    return rows


@router.patch("/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
async def patch_invoice(
    invoice_id: uuid.UUID,
    payload: PatchInvoiceRequest,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    member: Annotated[OrganizationMember, Depends(require_write_role)],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> InvoiceDetailResponse:
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None or invoice.organization_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    if invoice.status not in _WRITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invoice in status '{invoice.status.value}' cannot be edited.",
        )

    changed_fields = sorted(payload.model_fields_set)
    item_rows = await _apply_patch(invoice, payload, session)
    if payload.line_items is None:
        item_rows = (
            (
                await session.execute(
                    select(InvoiceItem)
                    .where(InvoiceItem.invoice_id == invoice.id)
                    .order_by(InvoiceItem.line_number)
                )
            )
            .scalars()
            .all()
        )

    # Re-run the deterministic engine on the merged result; issues are
    # recorded, never silently repaired, and the invoice stays in review.
    issues = validate_invoice_financials(invoice, list(item_rows))
    invoice.validation_errors = [issue.model_dump(mode="json") for issue in issues]

    session.add(
        AuditLog(
            organization_id=invoice.organization_id,
            actor_id=current_user.id,
            action="invoice.corrected",
            entity_type="invoice",
            entity_id=invoice.id,
            meta={"changed_fields": changed_fields},
        )
    )
    await session.commit()
    await session.refresh(invoice)
    return await _build_detail(session, invoice, storage)


@router.post("/invoices/{invoice_id}/approve", response_model=InvoiceDetailResponse)
async def approve_invoice(
    invoice_id: uuid.UUID,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    member: Annotated[OrganizationMember, Depends(require_write_role)],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> InvoiceDetailResponse:
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None or invoice.organization_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    if invoice.status not in _WRITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only invoices in needs_review or ready can be approved "
            f"(current: '{invoice.status.value}').",
        )

    invoice.status = InvoiceStatus.APPROVED
    invoice.status_changed_at = datetime.now(timezone.utc)
    invoice.approved_by_id = current_user.id
    invoice.approved_at = invoice.status_changed_at
    session.add(
        AuditLog(
            organization_id=invoice.organization_id,
            actor_id=current_user.id,
            action="invoice.approved",
            entity_type="invoice",
            entity_id=invoice.id,
            meta={
                "invoice_number": invoice.invoice_number,
                "total_amount": str(invoice.total_amount) if invoice.total_amount else None,
                "currency": invoice.currency_code,
            },
        )
    )
    await session.commit()
    await session.refresh(invoice)
    return await _build_detail(session, invoice, storage)


@router.post("/invoices/{invoice_id}/reprocess", response_model=InvoiceDetailResponse)
async def reprocess_invoice(
    invoice_id: uuid.UUID,
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    member: Annotated[OrganizationMember, Depends(require_write_role)],
    current_user: Annotated[UserPayload, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
    enqueue: Annotated[
        Callable[[str], Awaitable[bool]],
        Depends(get_enqueue_process_invoice),
    ],
    _rate_limit: Annotated[None, Depends(reprocess_rate_limit)],
) -> InvoiceDetailResponse:
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None or invoice.organization_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    if invoice.status not in _REPROCESSABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invoice in status '{invoice.status.value}' cannot be reprocessed.",
        )

    # Enqueue first: if the broker is down the invoice keeps its current
    # status instead of getting stuck in `queued` forever.
    enqueued = await enqueue(str(invoice.id))
    if not enqueued:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Extraction queue is unavailable. Try again shortly.",
        )

    previous_status = invoice.status
    invoice.status = InvoiceStatus.QUEUED
    invoice.status_changed_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(
            organization_id=invoice.organization_id,
            actor_id=current_user.id,
            action="invoice.reprocessed",
            entity_type="invoice",
            entity_id=invoice.id,
            meta={"previous_status": previous_status.value},
        )
    )
    await session.commit()
    await session.refresh(invoice)
    return await _build_detail(session, invoice, storage)
