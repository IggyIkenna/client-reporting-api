"""Unit tests for client_reporting_api.core.tranche_router."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from client_reporting_api.core.tranche_router import (
    get_client_config,
    get_data_source,
    get_server_cost_usd,
    load_registry,
)

_SAMPLE_REGISTRY_YAML = """
clients:
  managed-client:
    full_name: Managed Client
    tranche: managed
    currency: USD
    secret_names:
      api_key: managed-client-secret-api-key
      api_secret: managed-client-secret-api-secret
    odum_fee_pct: 0.05
    trader_fee_pct: 0.20
    is_active: true
    is_underwater: false
  managed-no-secret:
    full_name: Managed No Secret
    tranche: managed
    currency: USD
    is_active: true
  fund-client:
    full_name: Fund Client
    tranche: fund_of_fund
    currency: USD
    is_active: true
  manual-client:
    full_name: Manual Client
    tranche: manual
    currency: USD
    is_active: true
server_costs_per_underwater_account_usd: 75
"""


@pytest.fixture()
def patched_registry(tmp_path: Path) -> Path:
    """Write a sample registry YAML to a temp file and patch _REGISTRY_PATH."""
    registry_file = tmp_path / "credentials-registry.yaml"
    registry_file.write_text(_SAMPLE_REGISTRY_YAML)
    return registry_file


def test_load_registry_returns_empty_when_file_missing() -> None:
    """load_registry() returns empty structure when YAML file does not exist."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", Path("/nonexistent/path.yaml")):
        registry = load_registry()
    assert registry["clients"] == {}
    assert registry["server_costs_per_underwater_account_usd"] == 50


def test_load_registry_parses_yaml(patched_registry: Path) -> None:
    """load_registry() correctly parses a valid YAML file."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        registry = load_registry()
    assert "managed-client" in registry["clients"]
    assert registry["server_costs_per_underwater_account_usd"] == 75


def test_load_registry_handles_invalid_structure() -> None:
    """load_registry() returns empty structure when YAML content is not a dict."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH") as mock_path:
        mock_path.exists.return_value = True
        with patch("builtins.open", mock_open(read_data="- just a list")):
            registry = load_registry()
    assert registry["clients"] == {}


def test_get_client_config_returns_config(patched_registry: Path) -> None:
    """get_client_config() returns the config dict for a known client."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        config = get_client_config("managed-client")
    assert config is not None
    assert config.get("tranche") == "managed"


def test_get_client_config_returns_none_for_unknown(patched_registry: Path) -> None:
    """get_client_config() returns None for an unknown client id."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        config = get_client_config("nonexistent-client")
    assert config is None


def test_get_data_source_managed_with_secret_is_api_live(patched_registry: Path) -> None:
    """Managed client with secret_names returns 'api_live'."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        source = get_data_source("managed-client")
    assert source == "api_live"


def test_get_data_source_managed_without_secret_is_api_static(patched_registry: Path) -> None:
    """Managed client without secret_names returns 'api_static'."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        source = get_data_source("managed-no-secret")
    assert source == "api_static"


def test_get_data_source_fund_of_fund_is_manual(patched_registry: Path) -> None:
    """Fund-of-fund tranche returns 'manual'."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        source = get_data_source("fund-client")
    assert source == "manual"


def test_get_data_source_manual_tranche_is_manual(patched_registry: Path) -> None:
    """Manual tranche returns 'manual'."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        source = get_data_source("manual-client")
    assert source == "manual"


def test_get_data_source_unknown_client_is_manual(patched_registry: Path) -> None:
    """Unknown client returns 'manual' as fallback."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        source = get_data_source("nonexistent-client")
    assert source == "manual"


def test_get_server_cost_usd_returns_value_from_registry(patched_registry: Path) -> None:
    """get_server_cost_usd() returns value from registry."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", patched_registry):
        cost = get_server_cost_usd()
    assert cost == 75


def test_get_server_cost_usd_defaults_to_50_when_missing() -> None:
    """get_server_cost_usd() defaults to 50 when registry file is missing."""
    with patch("client_reporting_api.core.tranche_router._REGISTRY_PATH", Path("/nonexistent/path.yaml")):
        cost = get_server_cost_usd()
    assert cost == 50
