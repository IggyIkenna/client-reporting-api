"""Unit tests for Phase 4 attribution routes (nav / pnl / positions / attribution).

Tests run in mock mode (CLOUD_MOCK_MODE=true) to avoid real bucket access.
Auth is disabled at the module level so requests reach route handlers.
Entitlement logic is tested via dependency_overrides (same pattern as allocators tests).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from unified_api_contracts import (
    EventOrigin,
    EventType,
    LedgerAssetClass,
    LedgerRow,
)
from unified_trading_library import AuthContext

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import attribution as _attr_mod
from client_reporting_api.core.ledger_views import compute_pnl_entries

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_mode() -> Generator[None]:
    """Enable mock mode and disable auth for all tests in this module."""
    # Routes use UTL's create_api_auth which reads disable_auth via @lru_cache
    # of UnifiedCloudConfig. Patch UTL's _get_auth_config directly (2026-05-17,
    # per client_reporting_api_coverage_below_floor).
    from unified_trading_library.cloud_interface import (  # noqa: qg-deep-import
        api_auth as _utl_api_auth,  # test needs @lru_cache _get_auth_config
    )

    _utl_api_auth._get_auth_config.cache_clear()
    _orig_utl_get_auth_config = _utl_api_auth._get_auth_config
    _utl_api_auth._get_auth_config = lambda: (True, False, None)  # type: ignore[misc,assignment]

    cfg = _attr_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _utl_api_auth._get_auth_config = _orig_utl_get_auth_config  # type: ignore[misc,assignment]
    _utl_api_auth._get_auth_config.cache_clear()
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

    app.dependency_overrides[_attr_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_attr_mod._require_auth, None)


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

    app.dependency_overrides[_attr_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_attr_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/nav
# ---------------------------------------------------------------------------


class TestGetNav:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/nav")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "snapshots" in payload
        assert isinstance(payload["snapshots"], list)
        assert len(payload["snapshots"]) >= 1
        snapshot = payload["snapshots"][0]
        assert "date" in snapshot
        assert "nav_usd" in snapshot
        assert "nav_in_share_class" in snapshot

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/nav")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/nav")
        assert response.status_code == 200

    def test_date_range_params_accepted(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/clients/client-A/nav",
            params={"date_from": "2026-01-01", "date_to": "2026-05-01"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/pnl
# ---------------------------------------------------------------------------


class TestGetPnl:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/pnl")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "entries" in payload
        assert isinstance(payload["entries"], list)
        assert len(payload["entries"]) >= 1
        entry = payload["entries"][0]
        assert "period_tag" in entry
        assert "total_pnl" in entry
        assert "strategy_alpha_total" in entry
        assert "execution_alpha_total" in entry

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/pnl")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/pnl")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/positions
# ---------------------------------------------------------------------------


class TestGetPositions:
    def test_returns_200_honest_empty_ledger(self, _client_a_auth: None) -> None:
        # Real ledger-derived (P3.4 + P5.1) — the ledger seam is empty until
        # engine-wiring populates GCS, so this is an HONEST empty/zero response.
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/positions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert payload["positions"] == []  # honest empty, NOT mock data
        balances = payload["balances"]
        assert balances["by_venue"] == []
        assert balances["by_instrument"] == []
        assert balances["by_share_class"] == []
        totals = payload["totals"]
        assert totals["realized_pnl"] == "0"  # NOT "0.00" placeholder
        assert totals["unrealized_pnl"] == "0"
        assert totals["total_pnl"] == "0"

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/positions")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/positions")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/clients/{client_id}/attribution
# ---------------------------------------------------------------------------


class TestGetAttribution:
    def test_returns_200_with_mock_data(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/attribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["client_id"] == "client-A"
        assert "rows" in payload
        assert isinstance(payload["rows"], list)
        assert len(payload["rows"]) >= 1
        row = payload["rows"][0]
        assert "strategy_id" in row
        assert "factor" in row
        assert "layer" in row
        assert "amount" in row

    def test_mock_has_strategy_and_execution_layers(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-A/attribution")
        assert response.status_code == 200
        rows = response.json()["rows"]
        layers = {row["layer"] for row in rows}
        assert "STRATEGY" in layers
        assert "EXECUTION" in layers

    def test_entitlement_blocks_other_client(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/attribution")
        assert response.status_code == 403

    def test_internal_admin_can_read_any_client(self, _admin_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/clients/client-B/attribution")
        assert response.status_code == 200

    def test_date_range_params_accepted(self, _client_a_auth: None) -> None:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/clients/client-A/attribution",
            params={"date_from": "2026-01-01", "date_to": "2026-05-01"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Pure helper functions — _nav_from_rows / compute_pnl_entries / _attribution_from_rows
# ---------------------------------------------------------------------------

_PNL_AS_OF = datetime(2026, 5, 23, 0, 0, 0, tzinfo=UTC)
_PNL_TS = datetime(2026, 5, 22, 0, 0, 0, tzinfo=UTC)


def _pnl_trade_row(*, venue: str, asset: str, side: str, qty: str, price: str) -> LedgerRow:
    """Build a TRADE LedgerRow for compute_pnl_entries tests (signed by side)."""
    signed = Decimal(qty) if side.upper() in ("BUY", "LONG", "SUPPLY") else -Decimal(qty)
    return LedgerRow(
        event_id=f"{venue}:{asset}:{side}",
        row_id=f"{venue}:{asset}:{side}",
        event_origin=EventOrigin.INSTRUCTION,
        event_type=EventType.TRADE,
        trade_id=f"{venue}:SPOT:{asset}|inst|{_PNL_TS.isoformat()}",
        timestamp_utc=_PNL_TS,
        asset_group="defi",
        venue=venue,
        account_id="acct",
        client_id="client-A",
        asset_symbol=asset,
        asset_canonical_id=asset,
        asset_class=LedgerAssetClass.SPOT_TOKEN,
        delta=signed,
        price=Decimal(price),
        quote_currency="USDC",
        fees_in_quote=Decimal("0"),
    )


_SAMPLE_ROWS: list[dict[str, object]] = [
    {
        "timestamp": "2026-05-01T12:00:00+00:00",
        "strategy_id": "carry_staked_basis",
        "instrument_id": "BTC-PERP",
        "factor": "CARRY",
        "layer": "STRATEGY",
        "amount": "300.00",
        "archetype_id": "carry_staked_basis",
        "fill_id": "fill-1",
        "venue": "hyperliquid",
        "benchmark_price": "62000.00",
    },
    {
        "timestamp": "2026-05-01T14:00:00+00:00",
        "strategy_id": "carry_staked_basis",
        "instrument_id": "ETH-PERP",
        "factor": "SLIPPAGE",
        "layer": "EXECUTION",
        "amount": "50.00",
        "archetype_id": "carry_staked_basis",
        "fill_id": "fill-2",
        "venue": "binance",
        "benchmark_price": "3000.00",
    },
    {
        "timestamp": "2026-05-02T12:00:00+00:00",
        "strategy_id": "carry_staked_basis",
        "instrument_id": "BTC-PERP",
        "factor": "CARRY",
        "layer": "STRATEGY",
        "amount": "150.00",
        "archetype_id": "carry_staked_basis",
        "fill_id": "fill-3",
        "venue": "hyperliquid",
        "benchmark_price": "62500.00",
    },
]


class TestNavFromRows:
    def test_empty_rows_returns_empty_snapshots(self) -> None:
        result = _attr_mod._nav_from_rows("client-A", [], None, None)
        assert result["client_id"] == "client-A"
        assert result["share_class"] == "USDT"
        assert result["snapshots"] == []

    def test_aggregates_amounts_by_date(self) -> None:
        result = _attr_mod._nav_from_rows("client-A", _SAMPLE_ROWS, None, None)
        snapshots = result["snapshots"]
        assert isinstance(snapshots, list)
        assert len(snapshots) == 2  # two distinct dates: 2026-05-01 and 2026-05-02

        by_date = {s["date"]: s for s in snapshots}
        assert "2026-05-01" in by_date
        assert "2026-05-02" in by_date

        may1 = by_date["2026-05-01"]
        assert "nav_usd" in may1
        assert "nav_in_share_class" in may1
        assert "nav_delta_usd" in may1

    def test_handles_missing_timestamp(self) -> None:
        rows = [{"timestamp": None, "amount": "100.00"}]
        result = _attr_mod._nav_from_rows("client-X", rows, None, None)
        snapshots = result["snapshots"]
        assert len(snapshots) == 1
        assert snapshots[0]["date"] == "unknown"

    def test_handles_bad_amount_gracefully(self) -> None:
        rows = [{"timestamp": "2026-05-01T00:00:00+00:00", "amount": "not-a-number"}]
        result = _attr_mod._nav_from_rows("client-X", rows, None, None)
        snapshots = result["snapshots"]
        assert len(snapshots) == 0  # bad amount is suppressed via contextlib.suppress


class TestLedgerDerivedPnl:
    """``/pnl`` is now ledger-derived (compute_pnl_entries), not attribution-parquet
    derived — so a fresh paper run with fills has a non-empty ``entries`` list."""

    def test_empty_ledger_is_honest_zero(self) -> None:
        result = compute_pnl_entries([], marks={}, as_of=_PNL_AS_OF, share_class_of={})
        assert result["entries"] == []
        assert result["realized_pnl_total"] == "0"
        assert result["unrealized_pnl_total"] == "0"
        assert result["total_pnl"] == "0"

    def test_one_open_leg_yields_one_entry_zero_realized(self) -> None:
        rows = [_pnl_trade_row(venue="LIDO", asset="ETH", side="SUPPLY", qty="33.33", price="1")]
        result = compute_pnl_entries(rows, marks={}, as_of=_PNL_AS_OF, share_class_of={})
        entries = result["entries"]
        assert len(entries) == 1
        # All-open run: realised is correctly zero (nothing closed).
        assert entries[0]["realized_pnl"] == "0"
        assert entries[0]["net_qty"] == "33.33"
        assert entries[0]["venue"] == "LIDO"

    def test_unrealized_from_mark(self) -> None:
        rows = [_pnl_trade_row(venue="UNISWAP_V3", asset="ETH", side="BUY", qty="2", price="3000")]
        result = compute_pnl_entries(rows, marks={"ETH": Decimal("3100")}, as_of=_PNL_AS_OF, share_class_of={})
        # 2 * (3100 - 3000) = 200 unrealised.
        assert result["unrealized_pnl_total"] == "200"
        assert result["entries"][0]["unrealized_pnl"] == "200"


class TestAttributionFromRows:
    def test_empty_rows_returns_empty(self) -> None:
        result = _attr_mod._attribution_from_rows("client-A", [])
        assert result["client_id"] == "client-A"
        assert result["rows"] == []

    def test_projects_all_fields(self) -> None:
        result = _attr_mod._attribution_from_rows("client-A", _SAMPLE_ROWS)
        rows = result["rows"]
        assert len(rows) == 3
        row = rows[0]
        assert row["strategy_id"] == "carry_staked_basis"
        assert row["instrument_id"] == "BTC-PERP"
        assert row["date"] == "2026-05-01"
        assert row["factor"] == "CARRY"
        assert row["layer"] == "STRATEGY"
        assert row["amount"] == "300.00"
        assert row["archetype_id"] == "carry_staked_basis"
        assert row["fill_id"] == "fill-1"
        assert row["venue"] == "hyperliquid"
        assert row["benchmark_price"] == "62000.00"

    def test_none_timestamp_yields_none_date(self) -> None:
        rows = [{"timestamp": None, "strategy_id": "s1"}]
        result = _attr_mod._attribution_from_rows("client-X", rows)
        assert result["rows"][0]["date"] is None


# ---------------------------------------------------------------------------
# Live path — handlers with is_mock_mode()=False (mocked read_attribution_rows)
# ---------------------------------------------------------------------------


class TestLivePaths:
    def test_nav_live_path_calls_reader(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        cfg = _attr_mod._cloud_cfg
        orig_mock = cfg.cloud_mock_mode
        cfg.cloud_mock_mode = False  # type: ignore[misc]
        cfg.data_mode = "live"  # type: ignore[misc]
        try:
            with patch(
                "client_reporting_api.api.routes.attribution.read_attribution_rows",
                return_value=_SAMPLE_ROWS,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/clients/client-A/nav")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["snapshots"]) > 0
        finally:
            cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
            cfg.data_mode = "mock"  # type: ignore[misc]

    def test_pnl_live_path_is_ledger_derived(self, _client_a_auth: None) -> None:
        # /pnl now derives entries from the canonical-run ledger (positions),
        # NOT from the attribution parquet — so a run with fills yields non-empty
        # entries even when the attribution parquet is empty.
        from unittest.mock import patch

        cfg = _attr_mod._cloud_cfg
        ledger_rows = [
            _pnl_trade_row(venue="UNISWAP_V3", asset="ETH", side="BUY", qty="2", price="3000"),
            _pnl_trade_row(venue="LIDO", asset="ETH", side="SUPPLY", qty="33", price="1"),
        ]
        # pydantic blocks plain setattr → flip mock off via the instance method.
        orig_is_mock = cfg.is_mock_mode
        object.__setattr__(cfg, "is_mock_mode", lambda: False)
        try:
            with (
                patch(
                    "client_reporting_api.api.routes.attribution.read_ledger_rows",
                    return_value=ledger_rows,
                ),
                patch(
                    "client_reporting_api.api.routes.attribution.resolve_canonical_run",
                    return_value="paper-20260620004135-bbbb",
                ),
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/clients/client-A/pnl")
            assert response.status_code == 200
            payload = response.json()
            assert payload["run_id"] == "paper-20260620004135-bbbb"
            assert len(payload["entries"]) == 2  # UNISWAP_V3:ETH + LIDO:ETH
            assert payload["realized_pnl_total"] == "0"  # all-open run
        finally:
            object.__setattr__(cfg, "is_mock_mode", orig_is_mock)

    def test_trades_live_path_is_ledger_derived(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        from unified_api_contracts.internal import FillModel, TradeFillRecord, make_trade_key

        ts = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
        ik = "DERIBIT:PERPETUAL:ETH-PERP"
        fill = TradeFillRecord(
            trade_key=make_trade_key(ik, "i1", ts),
            instrument_key=ik,
            strategy_instruction_id="i1",
            tick_timestamp=ts,
            venue="DERIBIT",
            side="LONG",
            qty=Decimal("30.8"),
            fill_price=Decimal("3000"),
            fees_in_quote=Decimal("0"),
            fill_model=FillModel.BENCHMARK,
        )
        with patch(
            "client_reporting_api.api.routes.attribution.read_canonical_run_fills",
            return_value=("paper-20260620004135-bbbb", [fill]),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/clients/client-A/trades")
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == "paper-20260620004135-bbbb"
        assert payload["total"] == 1
        trade = payload["trades"][0]
        assert trade["venue"] == "DERIBIT"
        assert trade["side"] == "buy"  # LONG → buy
        assert trade["quantity"] == "30.8"
        assert trade["fill_price"] == "3000"
        assert trade["notional_usd"] == "92400.0"

    def test_instructions_live_path_surfaces_qty(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        rows = [_pnl_trade_row(venue="UNISWAP_V3", asset="ETH", side="BUY", qty="100000", price="3000")]
        with (
            patch("client_reporting_api.api.routes.attribution.read_ledger_rows", return_value=rows),
            patch(
                "client_reporting_api.api.routes.attribution.resolve_canonical_run",
                return_value="paper-20260620004135-bbbb",
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/clients/client-A/instructions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == "paper-20260620004135-bbbb"
        ins = payload["instructions"][0]
        # qty surfaced under target_qty + size + quantity aliases (non-blank).
        assert ins["target_qty"] == "100000"
        assert ins["size"] == "100000"
        assert ins["quantity"] == "100000"

    def test_transfers_typed_honest_empty_when_no_transfer_rows(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        # Run exists with only TRADE rows → typed NO_TRANSFER_ROWS, not a bare 0.
        rows = [_pnl_trade_row(venue="LIDO", asset="ETH", side="SUPPLY", qty="33", price="1")]
        with (
            patch("client_reporting_api.api.routes.attribution.read_ledger_rows", return_value=rows),
            patch(
                "client_reporting_api.api.routes.attribution.resolve_canonical_run",
                return_value="paper-20260620004135-bbbb",
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/clients/client-A/transfers")
        assert response.status_code == 200
        payload = response.json()
        assert payload["transfers"] == []
        assert payload["status"] == "NO_TRANSFER_ROWS"
        assert payload["note"]  # explicit human-readable reason, never silent

    def test_attribution_live_path_calls_reader(self, _client_a_auth: None) -> None:
        from unittest.mock import patch

        cfg = _attr_mod._cloud_cfg
        orig_mock = cfg.cloud_mock_mode
        cfg.cloud_mock_mode = False  # type: ignore[misc]
        cfg.data_mode = "live"  # type: ignore[misc]
        try:
            with patch(
                "client_reporting_api.api.routes.attribution.read_attribution_rows",
                return_value=_SAMPLE_ROWS,
            ):
                client = TestClient(app, raise_server_exceptions=True)
                response = client.get("/api/v1/clients/client-A/attribution")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["rows"]) > 0
        finally:
            cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
            cfg.data_mode = "mock"  # type: ignore[misc]
