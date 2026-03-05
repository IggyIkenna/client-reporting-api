"""Unit tests for client_reporting_api.auth (GoogleOAuthMiddleware)."""

from __future__ import annotations

from unittest.mock import patch

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from client_reporting_api.auth import GoogleOAuthMiddleware


def _make_app(client_id: str = "test-client", allowed_domains: list[str] | None = None) -> Starlette:
    async def homepage(request: Request) -> Response:
        return Response("OK", status_code=200)

    app = Starlette(routes=[Route("/", homepage), Route("/health", homepage)])
    app.add_middleware(GoogleOAuthMiddleware, client_id=client_id, allowed_domains=allowed_domains)
    return app


def test_missing_bearer_token_returns_401() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/", headers={})
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Bearer token"


def test_valid_token_passes_through() -> None:
    fake_claims = {"sub": "1234", "email": "user@example.com", "hd": "example.com"}
    with patch(
        "client_reporting_api.auth.id_token.verify_oauth2_token",
        return_value=fake_claims,
    ):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer fake-token"})
    assert response.status_code == 200


def test_wrong_domain_returns_403() -> None:
    fake_claims = {"sub": "1234", "email": "user@other.com", "hd": "other.com"}
    with patch(
        "client_reporting_api.auth.id_token.verify_oauth2_token",
        return_value=fake_claims,
    ):
        client = TestClient(_make_app(allowed_domains=["allowed.com"]), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer fake-token"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Domain not allowed"


def test_health_path_skips_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 200
