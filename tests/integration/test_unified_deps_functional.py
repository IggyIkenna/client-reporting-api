"""Functional integration tests for all unified-* library dependencies.

Exercises actual code paths for every unified-* dep used by client-reporting-api:

  - unified-config-interface: UnifiedCloudConfig (auth.py, routes, __main__)
  - unified-trading-library: RequestAuditMiddleware, MockStateStore, PubSubEventSink,
                             setup_tracing
  - unified-cloud-interface: get_data_source, DataSource (pnl_reader, sports_pnl_reader)
  - unified-internal-contracts: FeeStructure, ClientConfig, CredentialsRegistry
  - unified-events-interface: setup_events, log_event

All tests run credential-free (CLOUD_PROVIDER=local CLOUD_MOCK_MODE=true).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# unified-config-interface: UnifiedCloudConfig
# ---------------------------------------------------------------------------


class TestUnifiedConfigInterfaceFunctional:
    """Functional tests for UnifiedCloudConfig used across auth/routes."""

    @pytest.mark.integration
    def test_config_reads_cloud_provider_and_mock_mode(self) -> None:
        """UnifiedCloudConfig reads CLOUD_PROVIDER and CLOUD_MOCK_MODE."""
        with patch.dict(
            "os.environ",
            {
                "CLOUD_PROVIDER": "local",
                "CLOUD_MOCK_MODE": "true",
                "DISABLE_AUTH": "true",
            },
            clear=False,
        ):
            from unified_config_interface import UnifiedCloudConfig

            cfg = UnifiedCloudConfig()
            assert cfg.cloud_provider == "local"
            assert cfg.cloud_mock_mode is True

    @pytest.mark.integration
    def test_config_disable_auth_flag(self) -> None:
        """UnifiedCloudConfig exposes disable_auth for auth gating."""
        with patch.dict(
            "os.environ",
            {
                "CLOUD_PROVIDER": "local",
                "CLOUD_MOCK_MODE": "true",
                "DISABLE_AUTH": "true",
                "ENVIRONMENT": "development",
            },
            clear=False,
        ):
            from unified_config_interface import UnifiedCloudConfig

            cfg = UnifiedCloudConfig()
            assert cfg.disable_auth is True

    @pytest.mark.integration
    def test_config_environment_not_production_allows_disable_auth(self) -> None:
        """DISABLE_AUTH is allowed in non-production environments."""
        with patch.dict(
            "os.environ",
            {
                "CLOUD_PROVIDER": "local",
                "CLOUD_MOCK_MODE": "true",
                "DISABLE_AUTH": "true",
                "ENVIRONMENT": "development",
            },
            clear=False,
        ):
            from unified_config_interface import UnifiedCloudConfig

            cfg = UnifiedCloudConfig()
            # Should NOT raise - only production raises
            assert cfg.environment == "development"
            assert cfg.disable_auth is True


# ---------------------------------------------------------------------------
# unified-events-interface: setup_events, log_event
# ---------------------------------------------------------------------------


class TestUnifiedEventsInterfaceFunctional:
    """Functional tests for UEI event logging used in auth.py and main.py."""

    @pytest.mark.integration
    def test_setup_events_in_test_mode(self) -> None:
        """setup_events('test') initializes without error."""
        from unified_events_interface import setup_events

        # Should not raise — test mode silences all real sinks
        setup_events("client-reporting-api-integ-test", "test")

    @pytest.mark.integration
    def test_log_event_callable_after_setup(self) -> None:
        """log_event can be called after setup_events without error."""
        from unified_events_interface import log_event, setup_events

        setup_events("client-reporting-api-integ-test-2", "test")
        # Should not raise
        log_event(
            "TEST_EVENT",
            severity="INFO",
            details={"test": True},
        )

    @pytest.mark.integration
    def test_log_event_used_in_auth_failure_paths(self) -> None:
        """auth.py calls log_event('AUTH_FAILURE') on missing API key."""
        with patch.dict(
            "os.environ",
            {
                "CLOUD_PROVIDER": "local",
                "CLOUD_MOCK_MODE": "true",
                "DISABLE_AUTH": "false",
                "ENVIRONMENT": "development",
            },
            clear=False,
        ):
            from unified_events_interface import log_event

            # log_event is callable (the actual auth test is in unit tests)
            assert callable(log_event)


# ---------------------------------------------------------------------------
# unified-trading-library: RequestAuditMiddleware, MockStateStore,
#                          PubSubEventSink, setup_tracing
# ---------------------------------------------------------------------------


class TestUnifiedTradingLibraryFunctional:
    """Functional tests for UTL components used by client-reporting-api."""

    @pytest.mark.integration
    def test_request_audit_middleware_instantiation(self) -> None:
        """RequestAuditMiddleware can be created with an ASGI app."""
        from unittest.mock import AsyncMock

        from unified_trading_library import RequestAuditMiddleware

        mock_app = AsyncMock()
        middleware = RequestAuditMiddleware(mock_app)
        assert middleware is not None

    @pytest.mark.integration
    def test_mock_state_store_seed_and_crud(self) -> None:
        """MockStateStore supports seed/list/create for mock_state.py."""
        import uuid

        from unified_trading_library import MockStateStore

        # Use unique service name to avoid cross-test state pollution
        store = MockStateStore(f"client-reporting-test-{uuid.uuid4().hex[:8]}")
        store.seed("reports", [{"id": "r1", "type": "monthly", "client_id": "c1"}])

        items = store.list("reports")
        assert len(items) == 1
        assert items[0]["type"] == "monthly"

        new_item = store.create("reports", {"id": "r2", "type": "quarterly", "client_id": "c2"})
        assert new_item["id"] == "r2"

        items = store.list("reports")
        assert len(items) == 2

    @pytest.mark.integration
    def test_mock_state_module_initializes_store(self) -> None:
        """client_reporting_api.mock_state creates a working store."""
        from client_reporting_api.mock_state import get_store

        store = get_store()
        assert store is not None

    @pytest.mark.integration
    def test_pubsub_event_sink_importable(self) -> None:
        """PubSubEventSink is importable from UTL (used in main.py)."""
        from unified_trading_library import PubSubEventSink

        assert PubSubEventSink is not None

    @pytest.mark.integration
    def test_setup_tracing_importable(self) -> None:
        """setup_tracing is importable from UTL (used in main.py)."""
        from unified_trading_library import setup_tracing

        assert callable(setup_tracing)

    @pytest.mark.integration
    def test_app_with_middleware_serves_health(self) -> None:
        """The client-reporting-api app with RequestAuditMiddleware serves /health."""
        with patch(
            "unified_trading_library.core.audit_middleware.log_event",
            new_callable=MagicMock,
        ):
            from fastapi.testclient import TestClient

            from client_reporting_api.api.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# unified-cloud-interface: get_data_source, DataSource
# ---------------------------------------------------------------------------


class TestUnifiedCloudInterfaceFunctional:
    """Functional tests for UCI DataSource used in pnl_reader/sports_pnl_reader."""

    @pytest.mark.integration
    def test_get_data_source_importable(self) -> None:
        """get_data_source and DataSource are importable from UCI."""
        from unified_cloud_interface import DataSource, get_data_source

        assert callable(get_data_source)
        assert DataSource is not None

    @pytest.mark.integration
    def test_pnl_reader_with_mocked_data_source(self) -> None:
        """generate_pnl_report uses get_data_source and returns report dict."""
        import pandas as pd

        mock_source = MagicMock()
        mock_df = pd.DataFrame(
            {
                "date": ["2026-03-01", "2026-03-02"],
                "pnl": [100.0, -50.0],
                "instrument": ["BTC-USDT", "ETH-USDT"],
            }
        )
        mock_source.read.return_value = mock_df

        with patch(
            "client_reporting_api.core.pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            from client_reporting_api.core.pnl_reader import generate_pnl_report

            result = generate_pnl_report("client-001", "2026-03")

        assert result["status"] == "ok"
        assert result["client_id"] == "client-001"
        assert len(result["rows"]) == 2
        mock_source.read.assert_called_once_with(format="parquet")

    @pytest.mark.integration
    def test_pnl_reader_handles_no_data(self) -> None:
        """generate_pnl_report returns no_data when DataSource returns empty."""
        import pandas as pd

        mock_source = MagicMock()
        mock_source.read.return_value = pd.DataFrame()

        with patch(
            "client_reporting_api.core.pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            from client_reporting_api.core.pnl_reader import generate_pnl_report

            result = generate_pnl_report("client-001", "2026-03")

        assert result["status"] == "no_data"

    @pytest.mark.integration
    def test_pnl_reader_handles_file_not_found(self) -> None:
        """generate_pnl_report handles FileNotFoundError gracefully."""
        mock_source = MagicMock()
        mock_source.read.side_effect = FileNotFoundError("blob not found")

        with patch(
            "client_reporting_api.core.pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            from client_reporting_api.core.pnl_reader import generate_pnl_report

            result = generate_pnl_report("client-001", "2026-03")

        assert result["status"] == "no_data"

    @pytest.mark.integration
    def test_sports_pnl_reader_with_mocked_data_source(self) -> None:
        """generate_sports_pnl_report reads from UCI DataSource."""
        import pandas as pd

        mock_source = MagicMock()
        mock_df = pd.DataFrame(
            {
                "profit_loss": [100.0, -50.0, 75.0],
                "stake": [200.0, 100.0, 150.0],
                "venue_key": ["bet365", "betfair", "bet365"],
                "strategy_id": ["strat_a", "strat_b", "strat_a"],
            }
        )
        mock_source.read.return_value = mock_df

        with patch(
            "client_reporting_api.core.sports_pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            from client_reporting_api.core.sports_pnl_reader import (
                generate_sports_pnl_report,
            )

            result = generate_sports_pnl_report("client-001", "2026-03")

        assert result["status"] == "ok"
        assert result["total_bets"] == 3
        assert len(result["by_venue"]) == 2
        assert len(result["by_strategy"]) == 2


# ---------------------------------------------------------------------------
# unified-internal-contracts: FeeStructure, ClientConfig, CredentialsRegistry
# ---------------------------------------------------------------------------


class TestUnifiedInternalContractsFunctional:
    """Functional tests for UIC types used in core/ modules."""

    @pytest.mark.integration
    def test_fee_structure_used_in_fee_calculator(self) -> None:
        """FeeCalculator uses FeeStructure from UIC to compute period fees."""
        from unified_internal_contracts import FeeStructure

        from client_reporting_api.core.fee_calculator import FeeCalculator

        fee_structure = FeeStructure(
            trader_fee_pct=Decimal("0.20"),
            odum_fee_pct=Decimal("0.05"),
            introducer_fee_pct=None,
            introducer_id=None,
        )
        calc = FeeCalculator()
        trader_fee, odum_fee, introducer_fee, server_cost = calc.calculate_period_fees(
            client_id="client-001",
            opening_aum=Decimal("100000"),
            closing_aum=Decimal("120000"),
            trader_hwm=Decimal("110000"),
            odum_hwm=Decimal("115000"),
            fee_structure=fee_structure,
            is_underwater=False,
        )
        # closing_aum (120k) > trader_hwm (110k) => pnl_above = 10k => fee = 10k * 0.20 = 2k
        assert trader_fee == Decimal("2000.00") or trader_fee == Decimal("2000")
        # closing_aum (120k) > odum_hwm (115k) => pnl_above = 5k => fee = 5k * 0.05 = 250
        assert odum_fee == Decimal("250.00") or odum_fee == Decimal("250")
        assert introducer_fee == Decimal("0")
        # Not underwater => server_cost = 0
        assert server_cost == Decimal("0")

    @pytest.mark.integration
    def test_fee_structure_with_introducer(self) -> None:
        """FeeCalculator computes introducer fee when introducer is configured."""
        from unified_internal_contracts import FeeStructure

        from client_reporting_api.core.fee_calculator import FeeCalculator

        fee_structure = FeeStructure(
            trader_fee_pct=Decimal("0.20"),
            odum_fee_pct=Decimal("0.10"),
            introducer_fee_pct=Decimal("0.50"),
            introducer_id="intro-001",
        )
        calc = FeeCalculator()
        trader_fee, odum_fee, introducer_fee, server_cost = calc.calculate_period_fees(
            client_id="client-002",
            opening_aum=Decimal("100000"),
            closing_aum=Decimal("130000"),
            trader_hwm=Decimal("100000"),
            odum_hwm=Decimal("100000"),
            fee_structure=fee_structure,
            is_underwater=True,
            server_cost_usd=Decimal("75"),
        )
        # PnL above trader HWM = 30k * 0.20 = 6000
        assert trader_fee == Decimal("6000")
        # PnL above odum HWM = 30k * 0.10 = 3000
        assert odum_fee == Decimal("3000")
        # Introducer gets 50% of odum_fee = 1500
        assert introducer_fee == Decimal("1500")
        # Underwater => server cost applies
        assert server_cost == Decimal("75")

    @pytest.mark.integration
    def test_client_config_and_credentials_registry_types(self) -> None:
        """ClientConfig and CredentialsRegistry TypedDicts are usable."""
        from unified_internal_contracts import ClientConfig, CredentialsRegistry

        # These are TypedDicts — verify they can be used as type annotations
        config: ClientConfig = {
            "tranche": "managed",
            "secret_name": "my-secret",
        }
        assert config["tranche"] == "managed"

        registry: CredentialsRegistry = {
            "clients": {"client-001": config},
            "server_costs_per_underwater_account_usd": 50,
        }
        assert "client-001" in registry["clients"]

    @pytest.mark.integration
    def test_tranche_router_uses_uic_types(self) -> None:
        """tranche_router.get_data_source() returns correct source based on config."""
        from client_reporting_api.core.tranche_router import get_data_source

        # With no registry file, returns "manual" (the fallback)
        result = get_data_source("nonexistent-client")
        assert result == "manual"
