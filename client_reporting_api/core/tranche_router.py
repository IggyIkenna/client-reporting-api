import logging
from pathlib import Path
from typing import TypedDict, cast

import yaml

logger = logging.getLogger(__name__)

_REGISTRY_PATH = (
    Path(__file__).parent.parent.parent.parent / "execution-services" / "configs" / "credentials-registry.yaml"
)


class ClientConfig(TypedDict, total=False):
    """Configuration for a single client."""

    full_name: str
    tranche: str
    currency: str
    venue: str
    secret_name: str
    odum_fee_pct: float
    trader_fee_pct: float
    introducer_id: str
    introducer_fee_pct: float
    is_underwater: bool
    is_active: bool
    data_source: str
    is_pooled: bool
    pool_investors: dict[str, float]


class CredentialsRegistry(TypedDict):
    """The complete credentials registry structure."""

    clients: dict[str, ClientConfig]
    server_costs_per_underwater_account_usd: int


def load_registry() -> CredentialsRegistry:
    """Load the credentials registry from YAML file."""
    if not _REGISTRY_PATH.exists():
        logger.warning("credentials-registry.yaml not found at %s", _REGISTRY_PATH)
        return {"clients": {}, "server_costs_per_underwater_account_usd": 50}

    with open(_REGISTRY_PATH) as f:
        raw_data: object = cast(object, yaml.safe_load(f))

    # Ensure the structure matches our TypedDict
    if not isinstance(raw_data, dict):
        logger.warning("Invalid registry structure")
        return {"clients": {}, "server_costs_per_underwater_account_usd": 50}

    # Cast to our typed structure after validation
    data: CredentialsRegistry = cast(CredentialsRegistry, raw_data)
    return data


def get_client_config(client_id: str) -> ClientConfig | None:
    """Get configuration for a specific client."""
    registry = load_registry()
    clients = registry["clients"]
    return clients.get(client_id)


def get_data_source(client_id: str) -> str:
    """Determine the data source for a client based on their tranche and configuration."""
    config = get_client_config(client_id)
    if config is None:
        logger.warning("Client %s not found in registry", client_id)
        return "manual"

    tranche = config.get("tranche", "manual")

    if tranche == "fund_of_fund":
        return "manual"

    if tranche == "managed":
        secret_name = config.get("secret_name")
        if secret_name:
            return "api_live"
        return "api_static"

    return "manual"


def get_server_cost_usd() -> int:
    """Get the monthly server cost for underwater accounts."""
    registry = load_registry()
    return registry.get("server_costs_per_underwater_account_usd", 50)
