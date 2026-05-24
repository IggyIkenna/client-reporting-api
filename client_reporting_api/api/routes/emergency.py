"""Emergency close-all endpoint — optional trading capability for provisioned clients.

Live-mode only. Requires trading-capable API keys (not read-only).
Every attempt is logged with rationale for audit trail.
Safe default: has_trading_capability() returns False unless explicitly provisioned.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

import ccxt
from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from unified_trading_library import (  # pyright: ignore[reportPrivateImportUsage]
    AuthContext,
    UnifiedCloudConfig,
    create_api_auth,
    get_secret,
)

from client_reporting_api.core.entitlement import require_internal
from client_reporting_api.core.exchange_data_collector import _create_exchange  # pyright: ignore[reportPrivateUsage]
from client_reporting_api.core.tranche_router import get_client_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/emergency", tags=["emergency"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class CloseAllRequest(BaseModel):  # CORRECT-LOCAL: FastAPI request schema
    """Request body for emergency close-all."""

    rationale: str = Field(..., min_length=10, description="Reason for emergency close - audit trail")
    dry_run: bool = Field(default=True, description="If true, preview only - no orders placed")


class CloseAllPositionResult(BaseModel):  # CORRECT-LOCAL: FastAPI response schema
    """Result for a single position in the close-all response."""

    instrument: str
    side: str
    quantity: Decimal
    order_id: str | None = None
    status: str  # "would_close" | "submitted" | "failed"
    error: str | None = None


class CloseAllResponse(BaseModel):  # CORRECT-LOCAL: FastAPI response schema
    """Response body for emergency close-all."""

    client_id: str
    dry_run: bool
    positions_affected: int
    results: list[CloseAllPositionResult]
    rationale: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Trading capability check
# ---------------------------------------------------------------------------


def has_trading_capability(client_id: str) -> bool:
    """Check if a client has trading-capable API keys provisioned.

    Reads the ``has_trading_keys`` flag from the credentials registry.
    Safe default: returns False unless the flag is explicitly set to True.
    """
    config = get_client_config(client_id)
    if config is None:
        return False
    # ClientConfig is total=False TypedDict — use .get with safe default
    return bool(config.get("has_trading_keys", False))


# ---------------------------------------------------------------------------
# Helper: create a *trading* exchange instance for the client
# ---------------------------------------------------------------------------


def _get_trading_exchange(client_id: str) -> ccxt.Exchange | None:
    """Create a CCXT exchange wired with the client's *trading* credentials.

    Returns None if the client has no trading secret configured.
    Trading secrets follow the naming pattern:
        trade-{client_lower}-{venue}-api-key / api-secret / passphrase
    """
    config = get_client_config(client_id)
    if config is None:
        return None

    venue = config.get("venue", "")
    if not venue:
        return None

    client_lower = client_id.lower().replace("_", "-")
    secret_base = f"trade-{client_lower}-{venue}"

    try:
        api_key: str = get_secret(f"{secret_base}-api-key")
        api_secret: str = get_secret(f"{secret_base}-api-secret")
    except RuntimeError:
        logger.warning("Trading credentials not found for %s", client_id)
        return None

    passphrase: str = ""
    if venue == "okx":
        try:
            passphrase = get_secret(f"{secret_base}-passphrase")
        except RuntimeError:
            logger.warning("Trading passphrase not found for %s (OKX)", client_id)

    return _create_exchange(
        venue=venue,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
    )


# ---------------------------------------------------------------------------
# Fetch open positions from exchange
# ---------------------------------------------------------------------------


def _fetch_open_positions(
    exchange: ccxt.Exchange,
) -> list[dict[str, str | float | None]]:
    """Return raw CCXT position dicts with non-zero contract size."""
    try:
        raw_positions: list[dict[str, str | float | None]] = exchange.fetch_positions()
        return [p for p in raw_positions if float(p.get("contracts", 0) or 0) != 0]
    except ccxt.BaseError as exc:
        logger.warning("Failed to fetch positions: %s", str(exc))
        return []


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/close-all/{client_id}", response_model=CloseAllResponse)
def close_all_positions(
    auth: AuthDep,
    body: Annotated[CloseAllRequest, Body()],
    client_id: str = Path(..., description="Client identifier"),
) -> CloseAllResponse:
    """Emergency close-all positions for a client.

    Internal-only: trading actions are never reachable by external client
    API keys regardless of ``client_id``. Ops / support initiates via
    admin credentials.

    - Guard: returns 403 if the client does not have trading keys.
    - dry_run=true (default): lists positions that WOULD be closed.
    - dry_run=false: submits market close orders via CCXT.
    """
    require_internal(auth)
    now = datetime.now(tz=UTC)

    # --- Audit log: every attempt ---
    logger.info(
        "EMERGENCY_CLOSE_ALL attempt | client=%s dry_run=%s rationale=%r",
        client_id,
        body.dry_run,
        body.rationale,
    )

    # --- Guard: trading keys required ---
    if not has_trading_capability(client_id):
        logger.warning(
            "EMERGENCY_CLOSE_ALL denied | client=%s reason=no_trading_keys",
            client_id,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "Trading API keys not configured for this client. Contact client to provision trading-capable keys."
            ),
        )

    # --- Mock mode: return empty preview ---
    if _cloud_cfg.is_mock_mode():
        logger.info("EMERGENCY_CLOSE_ALL mock mode — returning empty preview")
        return CloseAllResponse(
            client_id=client_id,
            dry_run=body.dry_run,
            positions_affected=0,
            results=[],
            rationale=body.rationale,
            timestamp=now,
        )

    # --- Live mode: fetch positions ---
    exchange = _get_trading_exchange(client_id)
    if exchange is None:
        raise HTTPException(
            status_code=502,
            detail="Unable to connect to exchange with trading credentials.",
        )

    open_positions = _fetch_open_positions(exchange)
    results: list[CloseAllPositionResult] = []

    for pos in open_positions:
        symbol = str(pos.get("symbol", ""))
        side = str(pos.get("side", "long"))
        contracts = Decimal(str(pos.get("contracts", 0) or 0))

        if body.dry_run:
            results.append(
                CloseAllPositionResult(
                    instrument=symbol,
                    side=side,
                    quantity=contracts,
                    status="would_close",
                )
            )
            continue

        # --- Execute market close ---
        close_side = "sell" if side == "long" else "buy"
        try:
            order: dict[str, str | float | None] = exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=float(contracts),
                params={"reduceOnly": True},
            )
            order_id = str(order.get("id", ""))
            logger.info(
                "EMERGENCY_CLOSE_ALL order | client=%s sym=%s side=%s qty=%s id=%s",
                client_id,
                symbol,
                close_side,
                contracts,
                order_id,
            )
            results.append(
                CloseAllPositionResult(
                    instrument=symbol,
                    side=side,
                    quantity=contracts,
                    order_id=order_id,
                    status="submitted",
                )
            )
        except ccxt.BaseError as exc:
            error_msg = str(exc)
            logger.warning(
                "EMERGENCY_CLOSE_ALL order failed | client=%s symbol=%s error=%s",
                client_id,
                symbol,
                error_msg,
            )
            results.append(
                CloseAllPositionResult(
                    instrument=symbol,
                    side=side,
                    quantity=contracts,
                    status="failed",
                    error=error_msg,
                )
            )

    logger.info(
        "EMERGENCY_CLOSE_ALL complete | client=%s dry_run=%s positions=%d",
        client_id,
        body.dry_run,
        len(results),
    )

    return CloseAllResponse(
        client_id=client_id,
        dry_run=body.dry_run,
        positions_affected=len(results),
        results=results,
        rationale=body.rationale,
        timestamp=now,
    )
