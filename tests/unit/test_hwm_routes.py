"""Unit tests for Phase 5.C2 HWM crystallization timeline route.

Tests run in mock mode (CLOUD_MOCK_MODE=true) to avoid real bucket access.
Auth is disabled at the module level so requests reach route handlers.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes import hwm as _hwm_mod
from client_reporting_api.core import hwm_reader as _hwm_reader_mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_mode() -> Generator[None]:
    """Enable mock mode and disable auth for all tests in this module."""
    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True

    cfg = _hwm_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


@pytest.fixture
def _client_a_auth() -> Generator[None]:
    """Override _require_auth to return external caller authenticated as client-A."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="client-A",
            user_id="user-a",
            role="external",
            subscription_tier="pro",
            display_name="Client A",
            is_internal=False,
        )

    app.dependency_overrides[_hwm_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_hwm_mod._require_auth, None)


@pytest.fixture
def _admin_auth() -> Generator[None]:
    """Override _require_auth to return internal admin caller."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_hwm_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_hwm_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/hwm-timeline
# ---------------------------------------------------------------------------


class TestGetHwmTimeline:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/hwm-timeline")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "rows" in payload
        assert isinstance(payload["rows"], list)
        assert len(payload["rows"]) >= 1

    def test_mock_rows_have_required_fields(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/hwm-timeline")
        assert response.status_code == 200
        rows = response.json()["rows"]
        for row in rows:
            assert "share_class_id" in row
            assert "period_start" in row
            assert "period_end" in row
            assert "recognition_type" in row
            assert "amount_usd" in row
            assert "recognized_at" in row
            assert "source_event_id" in row

    def test_mock_contains_performance_fee_crystallization(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/hwm-timeline")
        rows = response.json()["rows"]
        types = {r["recognition_type"] for r in rows}
        assert "PERFORMANCE_FEE_CRYSTALLIZATION" in types

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/hwm-timeline")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/hwm-timeline")
        assert response.status_code == 200

    def test_date_range_params_accepted(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/clients/client-A/hwm-timeline",
            params={"date_from": "2026-01-01", "date_to": "2026-06-30"},
        )
        assert response.status_code == 200

    def test_client_id_in_response_matches_path(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        for cid in ("demo", "client-X", "client-B"):
            response = client.get(f"/api/v1/clients/{cid}/hwm-timeline")
            assert response.json()["client_id"] == cid


# ---------------------------------------------------------------------------
# _hwm_from_rows helper
# ---------------------------------------------------------------------------


class TestHwmFromRows:
    def test_empty_rows_returns_empty_list(self) -> None:
        result = _hwm_mod._hwm_from_rows("c1", [])
        assert result == {"client_id": "c1", "rows": []}

    def test_maps_all_fee_recognition_fields(self) -> None:
        rows: list[dict[str, object]] = [
            {
                "share_class_id": "USDT",
                "period_start": "2026-01-01T00:00:00+00:00",
                "period_end": "2026-04-01T00:00:00+00:00",
                "recognition_type": "PERFORMANCE_FEE_CRYSTALLIZATION",
                "amount_usd": "5000.00",
                "recognized_at": "2026-04-01T00:01:00+00:00",
                "source_event_id": "evt-abc",
            }
        ]
        result = _hwm_mod._hwm_from_rows("c1", rows)
        assert result["client_id"] == "c1"
        assert len(result["rows"]) == 1  # type: ignore[arg-type]
        row = result["rows"][0]  # type: ignore[index]
        assert row["share_class_id"] == "USDT"
        assert row["recognition_type"] == "PERFORMANCE_FEE_CRYSTALLIZATION"
        assert row["amount_usd"] == "5000.00"
        assert row["source_event_id"] == "evt-abc"

    def test_handles_none_fields_gracefully(self) -> None:
        rows: list[dict[str, object]] = [
            {
                "share_class_id": None,
                "period_start": None,
                "period_end": None,
                "recognition_type": None,
                "amount_usd": None,
                "recognized_at": None,
                "source_event_id": None,
            }
        ]
        result = _hwm_mod._hwm_from_rows("c1", rows)
        row = result["rows"][0]  # type: ignore[index]
        assert row["share_class_id"] is None
        assert row["period_start"] is None
        assert row["amount_usd"] is None

    def test_amount_usd_coerced_to_string(self) -> None:
        from decimal import Decimal

        rows: list[dict[str, object]] = [
            {
                "share_class_id": "USDT",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
                "recognition_type": "MANAGEMENT_FEE",
                "amount_usd": Decimal("200.50"),
                "recognized_at": "2026-04-01",
                "source_event_id": "evt-mgmt",
            }
        ]
        result = _hwm_mod._hwm_from_rows("c1", rows)
        row = result["rows"][0]  # type: ignore[index]
        assert row["amount_usd"] == "200.50"

    def test_multiple_rows_preserved(self) -> None:
        rows: list[dict[str, object]] = [
            {
                "share_class_id": f"SC{i}",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
                "recognition_type": "PERFORMANCE_FEE_CRYSTALLIZATION",
                "amount_usd": str(i * 100),
                "recognized_at": "2026-04-01",
                "source_event_id": f"evt-{i}",
            }
            for i in range(5)
        ]
        result = _hwm_mod._hwm_from_rows("c1", rows)
        assert len(result["rows"]) == 5  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _mock_hwm_timeline helper
# ---------------------------------------------------------------------------


class TestMockHwmTimeline:
    def test_returns_expected_client_id(self) -> None:
        result = _hwm_mod._mock_hwm_timeline("my-client")
        assert result["client_id"] == "my-client"

    def test_contains_multiple_share_classes(self) -> None:
        result = _hwm_mod._mock_hwm_timeline("demo")
        rows = result["rows"]
        share_classes = {r["share_class_id"] for r in rows}  # type: ignore[union-attr]
        assert len(share_classes) >= 2

    def test_rows_have_all_required_keys(self) -> None:
        result = _hwm_mod._mock_hwm_timeline("demo")
        for row in result["rows"]:  # type: ignore[union-attr]
            for key in (
                "share_class_id",
                "period_start",
                "period_end",
                "recognition_type",
                "amount_usd",
                "recognized_at",
                "source_event_id",
            ):
                assert key in row


# ---------------------------------------------------------------------------
# hwm_reader._blob_date
# ---------------------------------------------------------------------------


class TestBlobDate:
    def test_parses_iso_date_from_segment(self) -> None:
        assert _hwm_reader_mod._blob_date(
            "client-A/fee_recognition/2026-04-01/rows.parquet"
        ) == date(2026, 4, 1)

    def test_returns_none_for_no_date_in_path(self) -> None:
        assert _hwm_reader_mod._blob_date("some/path/without/dates/rows.parquet") is None

    def test_handles_nested_path(self) -> None:
        result = _hwm_reader_mod._blob_date("c1/fee_recognition/2026-01-15/shard.parquet")
        assert result == date(2026, 1, 15)

    def test_returns_none_for_empty_string(self) -> None:
        assert _hwm_reader_mod._blob_date("") is None
