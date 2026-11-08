"""FastAPI application factory.

Phase 2 keeps the surface minimal: CORS + a dependency-free liveness probe.
Routers, auth dependencies, and DB-backed checks land in Phase 3.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import invoices, vendors, workspaces
from app.core.config import settings
from app.database.session import dispose_engine
from app.modules.uploads.router import router as uploads_router
from app.workers.enqueue import close_arq_pool


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

    app.include_router(workspaces.router, prefix="/api/v1")
    app.include_router(invoices.router, prefix="/api/v1")
    app.include_router(uploads_router, prefix="/api/v1")
    app.include_router(vendors.router, prefix="/api/v1")

    return app


app = create_app()
