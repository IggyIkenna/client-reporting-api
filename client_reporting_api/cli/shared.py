# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportReturnType=false, reportUnusedClass=false, reportArgumentType=false, reportUnknownVariableType=false
"""Shared helpers for the client-reporting-manage CLI.

Holds:
  - JSON encoder that handles Decimal/datetime
  - Registry path resolution (workspace vs /app vs package-relative)
  - Data directory resolution
  - Secret Manager access (via unified_trading_library.get_secret)
  - Exchange factory + credential fetch + USD pricing
  - Active-client enumeration + summary printing
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import ccxt
import yaml
from unified_trading_library import (  # pyright: ignore[reportPrivateImportUsage]
    get_secret as _utl_get_secret,
)
from unified_trading_library import (
    log_event,
)

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
_APP_DIR = Path("/app")

# Data dir: prefer local dev, fall back to /app (Docker)
_LOCAL_DATA = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"
DATA_DIR = _LOCAL_DATA if _LOCAL_DATA.exists() else _APP_DIR / "data" / "backfill"

# Registry path: workspace (local dev) → /app/configs (Docker) → package-relative
_WORKSPACE_REGISTRY = WORKSPACE / "execution-service" / "configs" / "credentials-registry.yaml"
_DOCKER_REGISTRY = _APP_DIR / "configs" / "credentials-registry.yaml"
_PKG_REGISTRY = Path(__file__).resolve().parent.parent.parent / "configs" / "credentials-registry.yaml"
REGISTRY_PATH = (
    _WORKSPACE_REGISTRY
    if _WORKSPACE_REGISTRY.exists()
    else _DOCKER_REGISTRY
    if _DOCKER_REGISTRY.exists()
    else _PKG_REGISTRY
)


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that serialises Decimal as float and datetime as ISO-8601."""

    def default(self, o: object) -> float | str:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def _load_registry() -> dict[str, dict[str, str | float | bool | dict[str, float]]]:
    """Load the 'clients' section of the credentials registry."""
    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("clients", {})


def _load_full_registry() -> dict[str, dict[str, str | float | bool | dict[str, float]] | dict[str, dict[str, str]]]:
    """Load the full credentials registry (clients + organisations + strategies)."""
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f)


def _get_secret(name: str) -> str | None:
    """Fetch a secret from the configured Secret Manager via UTL (not google.cloud).

    Returns None when the secret is missing (UTL's `get_secret` contract).
    """
    return _utl_get_secret(name)


@dataclass
class _GapInfo:
    """Date arithmetic for the local-vs-now gap during incremental updates."""

    today: str
    last_date: str
    last_dt: datetime
    today_dt: datetime
    gap_days: int


def _create_exchange(venue: str, api_key: str, api_secret: str, passphrase: str) -> ccxt.Exchange:
    """Build a CCXT client for a given venue + credential triple."""
    config: dict[str, str | bool | int | dict[str, str]] = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": False,
        "timeout": 15000,
    }
    if venue == "okx" and passphrase:
        config["password"] = passphrase
    if venue == "binance":
        config["options"] = {"defaultType": "swap"}

    cls_map: dict[str, type[ccxt.Exchange]] = {"binance": ccxt.binance, "okx": ccxt.okx}
    cls = cls_map.get(venue)
    if cls is None:
        raise ValueError(f"Unsupported venue: {venue}")
    return cls(config)


def _fetch_credentials(client_id: str, venue: str) -> dict[str, str] | None:
    """Pull API key/secret/passphrase for a given (client, venue) from Secret Manager."""
    base = f"exec-{client_id.lower().replace('_', '-')}-{venue}"
    try:
        api_key = _get_secret(f"{base}-api-key")
        api_secret = _get_secret(f"{base}-api-secret")
    except RuntimeError:
        logger.warning("No credentials for %s", base)
        return None
    # UTL's get_secret returns None (not RuntimeError) on a missing secret; without
    # this guard a missing key builds a None-filled creds dict and CCXT gets apiKey=None.
    if api_key is None or api_secret is None:
        logger.warning("Missing api-key/api-secret for %s", base)
        return None
    passphrase = ""
    if venue == "okx":
        with contextlib.suppress(RuntimeError):
            passphrase = _get_secret(f"{base}-passphrase") or ""
    return {"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase}


def _get_usd_price(exchange: ccxt.Exchange, currency: str) -> float:
    """Return the latest USD price for a currency; 1.0 for stables, 0.0 if unknown."""
    stables = {"USDT", "USDC", "BUSD", "DAI", "USD", "FDUSD"}
    if currency in stables:
        return 1.0
    for quote in ["USDT", "USD", "USDC"]:
        try:
            ticker = exchange.fetch_ticker(f"{currency}/{quote}")
            price = float(ticker.get("last", 0) or ticker.get("close", 0) or 0)
            if price > 0:
                return price
        except ccxt.BaseError:
            continue
    return 0.0


def _get_active_clients(
    registry: dict[str, dict[str, str | float | bool | dict[str, float]]],
    client_filter: str | None = None,
) -> list[tuple[str, dict[str, str | float | bool | dict[str, float]]]]:
    """Filter the registry to active, managed clients (optionally a single client)."""
    clients = []
    for cid, cfg in registry.items():
        if not cfg.get("is_active", False) or cfg.get("tranche") != "managed" or not cfg.get("secret_name"):
            continue
        if client_filter and cid != client_filter:
            continue
        clients.append((cid, cfg))
    return clients


def _print_summary(label: str, results: dict[str, bool]) -> None:
    """Log an OK/FAILED summary for each client in ``results``."""
    ok = sum(1 for v in results.values() if v)
    fail = sum(1 for v in results.values() if not v)
    logger.info("\n=== %s SUMMARY ===", label)
    for cid, success in results.items():
        logger.info("  %s: %s", cid, "OK" if success else "FAILED")
    logger.info("  %d/%d succeeded", ok, ok + fail)


def _emit_structured(event: str, details: dict[str, object]) -> None:
    """Emit a structured event through UEI/UTL (no-op in local mode)."""
    log_event(event, details=details)


def _utc_now() -> datetime:
    """UTC-aware now() — isolated so tests can monkey-patch if needed."""
    return datetime.now(tz=UTC)
