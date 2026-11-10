"""Phase 7 rate-limiting tests: sliding-window unit behavior + integration
through the real presign and reprocess endpoints."""

import pytest
from httpx import AsyncClient

import app.core.rate_limit as rate_limit_module
from app.core.rate_limit import (
    PRESIGN_RULE,
    REPROCESS_RULE,
    RateRule,
    hit,
    reset_rate_limits,
)
from app.main import app as fastapi_app
from app.models import InvoiceStatus
from app.modules.uploads.service import SignedUpload, get_storage_client
from app.workers.enqueue import get_enqueue_process_invoice
from tests.conftest import auth_headers
from tests.test_uploads import seed_invoice


@pytest.fixture(autouse=True)
def clean_windows():
    reset_rate_limits()
    yield
    reset_rate_limits()


class FakeStorage:
    async def create_signed_upload_url(self, *, bucket: str, path: str) -> SignedUpload:
        return SignedUpload(upload_url=f"https://storage.test/{bucket}/{path}", token="t")

    async def create_signed_download_url(
        self, *, bucket: str, path: str, expires_in_seconds: int
    ) -> str:
        return f"https://storage.test/sign/{bucket}/{path}"


def ws_headers(seeded, user: str = "user_a_id") -> dict[str, str]:
    return {
        **auth_headers(seeded[user], "user@example.com"),
        "X-Workspace-ID": str(seeded["workspace_a_id"]),
    }


# ── Configured limits (the values the spec names) ────────────────────────


def test_configured_limits_match_spec() -> None:
    assert (PRESIGN_RULE.limit, PRESIGN_RULE.window_seconds) == (30, 60)
    assert (REPROCESS_RULE.limit, REPROCESS_RULE.window_seconds) == (10, 60)


# ── Sliding-window unit behavior ─────────────────────────────────────────


def test_window_blocks_at_limit_and_reports_retry_after(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(rate_limit_module, "_now", lambda: clock["now"])
    rule = RateRule(key="test", limit=2, window_seconds=60)

    assert hit("user-1", rule) == (True, 0)
    assert hit("user-1", rule) == (True, 0)
    allowed, retry_after = hit("user-1", rule)
    assert allowed is False
    assert retry_after == 60  # oldest hit still occupies the whole window

    # A blocked attempt is not recorded: it must not extend the lockout.
    clock["now"] += 30
    assert hit("user-1", rule) == (False, 30)

    # Sliding: once the oldest hits age out, capacity returns.
    clock["now"] += 31
    assert hit("user-1", rule) == (True, 0)


def test_windows_are_per_user_and_per_rule(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit_module, "_now", lambda: 0.0)
    rule_a = RateRule(key="a", limit=1, window_seconds=60)
    rule_b = RateRule(key="b", limit=1, window_seconds=60)

    assert hit("user-1", rule_a) == (True, 0)
    assert hit("user-1", rule_a) == (False, 60)  # same user, same rule → blocked
    assert hit("user-2", rule_a) == (True, 0)  # different user → own window
    assert hit("user-1", rule_b) == (True, 0)  # different rule → own window


# ── Endpoint integration ─────────────────────────────────────────────────


async def test_presign_is_limited_per_user(client: AsyncClient, seeded) -> None:
    fastapi_app.dependency_overrides[get_storage_client] = lambda: FakeStorage()
    payload = {
        "filename": "a.pdf",
        "file_size_bytes": 1024,
        "mime_type": "application/pdf",
        "sha256_hash": "a" * 64,
    }

    for _ in range(PRESIGN_RULE.limit):
        response = await client.post(
            "/api/v1/uploads/presign", json=payload, headers=ws_headers(seeded)
        )
        assert response.status_code == 200

    blocked = await client.post(
        "/api/v1/uploads/presign", json=payload, headers=ws_headers(seeded)
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert "Rate limit" in blocked.json()["detail"]

    # Another user in the same workspace is unaffected (limits key on the
    # verified JWT subject, not on the workspace or the client address).
    other_user = await client.post(
        "/api/v1/uploads/presign",
        json=payload,
        headers={
            **auth_headers(seeded["user_b_id"], "b@example.com"),
            "X-Workspace-ID": str(seeded["workspace_b_id"]),
        },
    )
    assert other_user.status_code == 200


async def test_reprocess_is_limited_per_user(
    client: AsyncClient, seeded, session_factory
) -> None:
    async def fake_enqueue(invoice_id: str) -> bool:
        return True

    fastapi_app.dependency_overrides[get_storage_client] = lambda: FakeStorage()
    fastapi_app.dependency_overrides[get_enqueue_process_invoice] = lambda: fake_enqueue
    headers = ws_headers(seeded)

    # One invoice per call: a reprocessed invoice moves to `queued` and is
    # not reprocessable again, so reusing one invoice would return 409.
    invoices = [
        await seed_invoice(
            session_factory,
            organization_id=seeded["workspace_a_id"],
            uploader_id=seeded["user_a_id"],
            filename=f"rl-{index}.pdf",
            status=InvoiceStatus.NEEDS_REVIEW,
        )
        for index in range(REPROCESS_RULE.limit + 1)
    ]

    for invoice in invoices[: REPROCESS_RULE.limit]:
        response = await client.post(
            f"/api/v1/invoices/{invoice.id}/reprocess", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["invoice"]["status"] == "queued"

    blocked = await client.post(
        f"/api/v1/invoices/{invoices[-1].id}/reprocess", headers=headers
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1

    # The blocked invoice was not touched.
    async with session_factory() as session:
        from app.models import Invoice

        untouched = await session.get(Invoice, invoices[-1].id)
        assert untouched is not None
        assert untouched.status == InvoiceStatus.NEEDS_REVIEW
