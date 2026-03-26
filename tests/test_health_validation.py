"""Health endpoint response validation — prevents data_freshness type mismatch regression."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    from client_reporting_api.api.main import app

    with (
        patch("unified_trading_library.core.audit_middleware.log_event"),
        patch("unified_trading_library.events_interface.log_event"),
    ):
        yield TestClient(app, raise_server_exceptions=False)


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body_is_valid_json(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert isinstance(body, dict)


def test_health_data_freshness_is_dict(client: TestClient) -> None:
    """Regression guard: data_freshness must be a dict, not str|bool."""
    body = client.get("/health").json()
    freshness = body.get("data_freshness")
    assert isinstance(freshness, dict), f"data_freshness must be dict, got {type(freshness)}"


def test_health_data_freshness_fields(client: TestClient) -> None:
    """data_freshness must contain last_processed_date (str) and stale (bool)."""
    body = client.get("/health").json()
    freshness = body["data_freshness"]
    assert isinstance(freshness.get("last_processed_date"), str)
    assert isinstance(freshness.get("stale"), bool)
