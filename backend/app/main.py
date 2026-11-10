"""FastAPI application factory.

Phase 7 hardening: a global handler masks every unhandled exception behind a
reference ID — full tracebacks stay in server logs; clients never see stack
details, SQL fragments, or configuration values.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import invoices, workspaces
from app.core.config import settings
from app.database.session import dispose_engine
from app.modules.dashboard.router import router as dashboard_router
from app.modules.exports.router import router as exports_router
from app.modules.uploads.router import router as uploads_router
from app.modules.vendors.router import router as vendors_router
from app.workers.enqueue import close_arq_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_arq_pool()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="FOLEXO — Invoice AI API",
        version="0.1.0",
        description="Accounts-payable invoice automation (AP focus).",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        # Deliberately dependency-free; DB/Redis checks arrive with Phase 3.
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def mask_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort handler: log everything server-side, return nothing."""
        reference_id = uuid.uuid4()
        logger.exception(
            "unhandled exception (reference %s) on %s %s",
            reference_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "message": f"An unexpected error occurred. Reference ID: {reference_id}",
            },
        )

    app.include_router(workspaces.router, prefix="/api/v1")
    app.include_router(invoices.router, prefix="/api/v1")
    app.include_router(uploads_router, prefix="/api/v1")
    app.include_router(vendors_router, prefix="/api/v1")
    app.include_router(exports_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")

    return app


app = create_app()
