"""Dashboard router — mounted under /api/v1/dashboard (main.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace
from app.database.session import get_session
from app.models import Organization
from app.modules.dashboard.schemas import DashboardSummary
from app.modules.dashboard.service import dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    workspace: Annotated[Organization, Depends(get_current_workspace)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardSummary:
    return await dashboard_summary(session, workspace)
