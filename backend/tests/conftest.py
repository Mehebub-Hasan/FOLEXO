"""Shared pytest fixtures for the FOLEXO backend test suite.

The suite runs against an in-process SQLite database (aiosqlite + StaticPool
so every session shares one schema) with the real ``get_session`` dependency
overridden — no Postgres required on developer machines. These tests exercise
HTTP/auth/tenant-scoping logic end-to-end. Postgres-specific behavior (native
enums, CHECK expressions, gen_random_uuid defaults, partial-index placement)
is covered separately by applying the Alembic migration to Supabase.

SQLite adaptations (test dialect only — production DDL is PostgreSQL):
- CheckConstraint compiles as ``CHECK (1)``: our checks use PG-only syntax
  (``~`` regex, ``char_length``) that SQLite cannot parse at DDL time.
- ``gen_random_uuid()`` server defaults are stripped from a *copy* of the
  metadata (SQLite requires constant defaults); models carry client-side
  ``default=uuid.uuid4`` so inserts still get real ids.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import CheckConstraint, MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  — populate Base.metadata
from app.database.base import NAMING_CONVENTION, Base
from app.database.session import get_session
from app.main import app
from app.models import Organization, OrganizationMember, OrganizationRole


@compiles(CheckConstraint, "sqlite")
def _skip_check_constraints_on_sqlite(constraint, compiler, **kw):
    return "CHECK (1)"


def _sqlite_metadata() -> MetaData:
    """Copy of the model metadata, safe to create on SQLite."""
    target = MetaData(naming_convention=NAMING_CONVENTION)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(target)
    for table in target.tables.values():
        for column in table.columns:
            server_default = column.server_default
            if server_default is None:
                continue
            arg = getattr(server_default, "arg", "")
            text = str(getattr(arg, "text", arg))
            if "gen_random_uuid" in text:
                # SQLite requires constant defaults; client-side uuid4 covers it.
                column.server_default = None
            elif "::" in text:
                # Strip PG casts (e.g. "'{}'::jsonb" -> "'{}'").
                column.server_default = text.split("::")[0]
    return target


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata().create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    """AsyncClient against the real app, with get_session bound to the test DB."""

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        maker = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, uuid.UUID]:
    """Two isolated tenants: user A owns workspace A, user B owns workspace B."""
    user_a_id, user_b_id = uuid.uuid4(), uuid.uuid4()
    workspace_a = Organization(
        id=uuid.uuid4(),
        name="Acme Ltd",
        slug="acme-ltd",
        default_currency="GBP",
        created_by_id=user_a_id,
    )
    workspace_b = Organization(
        id=uuid.uuid4(),
        name="Beta LLC",
        slug="beta-llc",
        default_currency="USD",
        created_by_id=user_b_id,
    )
    memberships = [
        OrganizationMember(
            organization_id=workspace_a.id,
            user_id=user_a_id,
            role=OrganizationRole.OWNER,
        ),
        OrganizationMember(
            organization_id=workspace_b.id,
            user_id=user_b_id,
            role=OrganizationRole.OWNER,
        ),
    ]
    async with session_factory() as session:
        session.add_all([workspace_a, workspace_b, *memberships])
        await session.commit()

    return {
        "user_a_id": user_a_id,
        "user_b_id": user_b_id,
        "workspace_a_id": workspace_a.id,
        "workspace_b_id": workspace_b.id,
    }


def auth_headers(user_id: uuid.UUID, email: str = "user@example.com") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token(subject=str(user_id), email=email)
    return {"Authorization": f"Bearer {token}"}
