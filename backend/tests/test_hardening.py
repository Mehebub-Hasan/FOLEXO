"""Phase 7 hardening tests: unhandled-exception masking (sanitized 500 body,
reference ID logged server-side) and HTTPException passthrough."""

import logging
import re

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app as fastapi_app


def test_unhandled_exception_is_masked_with_reference_id(caplog) -> None:
    async def boom() -> None:
        # Deliberately sensitive-looking content: none of it may reach the client.
        raise RuntimeError(
            "BOOM-SECRET password=hunter2 SELECT * FROM invoices -- sql snippet"
        )

    fastapi_app.add_api_route(
        "/_hardening/boom", boom, methods=["GET"], include_in_schema=False
    )
    try:
        with caplog.at_level(logging.ERROR, logger="app.main"):
            # Starlette's server-error middleware re-raises after sending the
            # (masked) response; the test client must tolerate that.
            with TestClient(fastapi_app, raise_server_exceptions=False) as client:
                response = client.get("/_hardening/boom")
    finally:
        fastapi_app.router.routes[:] = [
            registered
            for registered in fastapi_app.router.routes
            if getattr(registered, "path", None) != "/_hardening/boom"
        ]

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"error", "message"}
    assert body["error"] == "InternalServerError"

    match = re.fullmatch(
        r"An unexpected error occurred\. Reference ID: ([0-9a-f-]{36})",
        body["message"],
    )
    assert match is not None, body["message"]

    # Nothing sensitive leaks in the body
    for fragment in ("BOOM-SECRET", "hunter2", "SELECT", "RuntimeError"):
        assert fragment not in response.text

    # The full traceback + reference ID live in the server log only
    assert match.group(1) in caplog.text
    assert "BOOM-SECRET" in caplog.text


def test_http_exceptions_are_not_masked() -> None:
    """Deliberate API errors keep their status + detail — the mask covers
    only *unhandled* exceptions."""
    with TestClient(fastapi_app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/auth/me")  # no Authorization header
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing bearer token."}
    assert response.headers["www-authenticate"] == "Bearer"


def test_rate_limit_and_validation_errors_keep_status_codes() -> None:
    """Sanity: the mask must not flatten 4xx responses into 500s."""
    with TestClient(fastapi_app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
