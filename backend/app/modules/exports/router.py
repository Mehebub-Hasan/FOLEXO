"""Exports router — mounted under /api/v1/exports (main.py).

Sync exports capped at MAX_EXPORT_ROWS rows; async bulk exports are post-MVP.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace
from app.database.session import get_session
from app.models import Organization
from app.modules.exports.service import (
    XLSX_MEDIA_TYPE,
    build_csv,
    build_xlsx,
    export_filename,
    fetch_export_rows,
    parse_status_filter,
)

router = APIRouter(prefix="/exports", tags=["exports"])


async def _filtered_rows(
    session: AsyncSession,
    workspace: Organization,
    status_filter: Annotated[str | None, Query(alias="status", description="Comma-separated invoice statuses")] = None,
    vendor_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
):
    try:
        statuses = parse_status_filter(status_filter)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return await fetch_export_rows(
        session,
        workspace.id,
        statuses=statuses,
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/invoices.xlsx")
async def export_invoices_xlsx(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Response:
    rows = await _filtered_rows(session, workspace, status_filter, vendor_id, date_from, date_to)
    content = build_xlsx(rows)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename("xlsx")}"'
        },
    )


@router.get("/invoices.csv")
async def export_invoices_csv(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Response:
    rows = await _filtered_rows(session, workspace, status_filter, vendor_id, date_from, date_to)
    content = build_csv(rows)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename("csv")}"'
        },
    )
