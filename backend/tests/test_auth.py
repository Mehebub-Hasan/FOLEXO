"""Phase 2 guardrail tests: token verification + workspace (tenant) isolation."""

import uuid
from datetime import timedelta

from httpx import AsyncClient

from app.core.security import create_access_token
from tests.conftest import auth_headers

# ── 401 guardrails ───────────────────────────────────────────────────────


async def test_unauthenticated_access_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_malformed_authorization_header_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


async def test_invalid_token_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


async def test_expired_token_returns_401(client: AsyncClient) -> None:
    token = create_access_token(
        subject=str(uuid.uuid4()),
        email="expired@example.com",
        expires_delta=timedelta(seconds=-120),  # beyond the 10 s leeway
    )
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


async def test_tampered_token_returns_401(client: AsyncClient) -> None:
    token = create_access_token(subject=str(uuid.uuid4()), email="a@example.com")
    tampered = token[:-4] + "aaaa"
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert response.status_code == 401


# ── /api/v1/auth/me ──────────────────────────────────────────────────────


async def test_auth_me_returns_profile_and_workspaces(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers=auth_headers(seeded["user_a_id"], "a@example.com")
    )
    assert response.status_code == 200
    body = response.json()

    assert body["profile"]["id"] == str(seeded["user_a_id"])
    assert body["profile"]["email"] == "a@example.com"

    workspace_ids = {workspace["id"] for workspace in body["workspaces"]}
    assert str(seeded["workspace_a_id"]) in workspace_ids
    assert str(seeded["workspace_b_id"]) not in workspace_ids  # other tenant's workspace

    own = next(w for w in body["workspaces"] if w["id"] == str(seeded["workspace_a_id"]))
    assert own["role"] == "owner"
    assert own["slug"] == "acme-ltd"


async def test_auth_me_returns_empty_workspaces_for_new_user(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers=auth_headers(uuid.uuid4(), "lonely@example.com")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["email"] == "lonely@example.com"
    assert body["workspaces"] == []


# ── Workspace isolation guardrails ───────────────────────────────────────


async def test_member_can_access_own_workspace(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    response = await client.get(
        "/api/v1/workspaces/current",
        headers={
            **auth_headers(seeded["user_a_id"], "a@example.com"),
            "X-Workspace-ID": str(seeded["workspace_a_id"]),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(seeded["workspace_a_id"])
    assert body["slug"] == "acme-ltd"


async def test_cross_tenant_access_returns_403(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    """User A presenting User B's workspace id must get 403, not the resource."""
    response = await client.get(
        "/api/v1/workspaces/current",
        headers={
            **auth_headers(seeded["user_a_id"], "a@example.com"),
            "X-Workspace-ID": str(seeded["workspace_b_id"]),
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Access to this workspace is forbidden."


async def test_nonexistent_workspace_returns_403(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    """Unknown ids are indistinguishable from other tenants' ids (no probing)."""
    response = await client.get(
        "/api/v1/workspaces/current",
        headers={
            **auth_headers(seeded["user_a_id"], "a@example.com"),
            "X-Workspace-ID": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 403


async def test_workspace_header_missing_returns_422(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    response = await client.get(
        "/api/v1/workspaces/current", headers=auth_headers(seeded["user_a_id"])
    )
    assert response.status_code == 422


# ── POST /api/v1/workspaces ──────────────────────────────────────────────


async def test_create_workspace_enrolls_creator_as_owner(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Gamma Co"},
        headers=auth_headers(uuid.uuid4(), "founder@gamma.example"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Gamma Co"
    assert body["slug"] == "gamma-co"
    assert body["role"] == "owner"
    assert body["default_currency"] == "USD"


async def test_created_workspace_appears_in_auth_me(client: AsyncClient) -> None:
    user_id = uuid.uuid4()
    created = await client.post(
        "/api/v1/workspaces",
        json={"name": "Delta Ltd", "default_currency": "EUR"},
        headers=auth_headers(user_id, "d@example.com"),
    )
    assert created.status_code == 201

    me = await client.get("/api/v1/auth/me", headers=auth_headers(user_id, "d@example.com"))
    workspaces = me.json()["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["id"] == created.json()["id"]
    assert workspaces[0]["role"] == "owner"
    assert workspaces[0]["default_currency"] == "EUR"


async def test_duplicate_slug_returns_409(client: AsyncClient, seeded) -> None:
    first = await client.post(
        "/api/v1/workspaces",
        json={"name": "Zeta Corp"},
        headers=auth_headers(seeded["user_b_id"], "b@example.com"),
    )
    assert first.status_code == 201
    assert first.json()["slug"] == "zeta-corp"

    # A different tenant cannot take the now-existing slug.
    second = await client.post(
        "/api/v1/workspaces",
        json={"name": "Zeta Corp"},
        headers=auth_headers(seeded["user_a_id"], "a@example.com"),
    )
    assert second.status_code == 409
