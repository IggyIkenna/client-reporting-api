"""Demo client seed script — Phase 7 (wallet_treasury_client_flow_2026_05_10.md).

Walks demo client ``demo_client_001`` through the full DRAFT → LIVE onboarding
state machine and wires all three treasury sources (Copper / CEFFU / DeFi hot
wallet).  Also persists treasury endpoint config to GCS (or in-memory in mock
mode) and verifies all sources are reachable via ``CustodyPinger.ping_all()``.

execution:
  owner: slot-2 Tab / operator one-shot
  cadence: one-shot
  verifier: exit-code 0 + stdout "Demo client demo_client_001 now LIVE"
    + "All treasury sources pingable"
  last_executed: NEVER

CLI:
    python scripts/seed_demo_client.py --cloud gcp --confirm
    python -m scripts.seed_demo_client --cloud gcp --confirm

--confirm gate: required for any non-local cloud target.

Per ``wallet_treasury_client_flow_2026_05_10.md`` Phase 7.A + 7.B.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

from unified_api_contracts.internal.domain.client_lifecycle import (
    ClientKYCStub,
    ClientOnboardingState,
    ClientShareClassSubscription,
)
from unified_api_contracts.internal.domain.treasury import (
    CEFFUEndpoint,
    CopperEndpoint,
    CustodyPingResult,
    DefiWalletKeyMaterial,
    TreasurySource,
)
from unified_trading_library.client_lifecycle.onboarding import (
    ClientOnboardingStateMachine,
    GCSStateStore,
    InMemoryStateStore,
    StateStore,
)
from unified_trading_library.treasury.custody_pinger import CustodyPinger

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Demo constants
# ---------------------------------------------------------------------------

DEMO_CLIENT_ID = "demo_client_001"
DEMO_PROJECT_ID = "central-element-323112"

# ---------------------------------------------------------------------------
# Treasury endpoint definitions
# ---------------------------------------------------------------------------

_DEMO_COPPER = CopperEndpoint(
    portfolio_id="demo-copper-portfolio",
    api_key_id="copper-key-demo",
    is_live=False,
)

_DEMO_CEFFU = CEFFUEndpoint(
    ceffu_uid="demo-ceffu-uid",
    api_key_id="ceffu-key-demo",
    is_live=False,
)

_DEMO_DEFI = DefiWalletKeyMaterial(
    wallet_address="0xdemo0000000000000000000000000000000000001",
    chain="ETHEREUM",
    private_key_secret_id="demo-eth-pk",
    is_testnet=True,
)

_DemoEndpoint = CopperEndpoint | CEFFUEndpoint | DefiWalletKeyMaterial
DEMO_TREASURY_SOURCES: dict[TreasurySource, _DemoEndpoint] = {
    TreasurySource.COPPER: _DEMO_COPPER,
    TreasurySource.CEFFU: _DEMO_CEFFU,
    TreasurySource.DEFI_HOT_WALLET: _DEMO_DEFI,
}

# ---------------------------------------------------------------------------
# Mock SDK factories (used for testnet / demo mode)
# ---------------------------------------------------------------------------


def _make_mock_copper_sdk(balance_usd: float = 100_000.0) -> MagicMock:
    sdk = MagicMock()
    sdk.get_portfolio_balance.return_value = {
        "balance": balance_usd,
        "balance_usd": balance_usd,
    }
    return sdk


def _make_mock_ceffu_api(usdc_balance: float = 50_000.0) -> MagicMock:
    api = MagicMock()
    api.get_account_balance.return_value = {"usdc_balance": usdc_balance}
    return api


def _make_mock_web3(eth_balance_wei: int = 5_000_000_000_000_000_000) -> MagicMock:
    """Return mock Web3 instance with 5 ETH balance (5 x 10^18 wei)."""
    web3 = MagicMock()
    web3.eth.get_balance.return_value = eth_balance_wei
    return web3


# ---------------------------------------------------------------------------
# GCS treasury config persistence
# ---------------------------------------------------------------------------


def _persist_treasury_config(
    client_id: str,
    sources: dict[TreasurySource, _DemoEndpoint],
    bucket_name: str,
) -> None:
    """Persist treasury endpoint config JSON to GCS.

    Writes to ``gs://{bucket_name}/{client_id}/treasury/sources.json``.
    Uses google-cloud-storage directly (no extra deps required).
    """
    from google.cloud import storage  # type: ignore[import-untyped]

    payload: dict[str, object] = {
        "client_id": client_id,
        "persisted_at": datetime.now(tz=UTC).isoformat(),
        "sources": {
            src.value: {
                "type": type(cfg).__name__,
                "data": {k: str(v) for k, v in cfg.__dict__.items()},
            }
            for src, cfg in sources.items()
        },
    }
    gcs_client: storage.Client = storage.Client()
    bucket: storage.Bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(f"{client_id}/treasury/sources.json")
    blob.upload_from_string(json.dumps(payload, default=str), content_type="application/json")
    logger.info(
        "Treasury config persisted to gs://%s/%s/treasury/sources.json",
        bucket_name,
        client_id,
    )


# ---------------------------------------------------------------------------
# Phase 7.A — Onboarding
# ---------------------------------------------------------------------------


async def seed_demo_client(state_store: StateStore) -> None:
    """Walk demo_client_001 from DRAFT → LIVE through the state machine.

    Idempotent: if client is already LIVE, this is a complete no-op.
    Each advance() call is itself idempotent for same-state transitions.
    """
    state_machine = ClientOnboardingStateMachine(state_store=state_store)
    client_id = DEMO_CLIENT_ID

    # Early-exit idempotency: LIVE is the terminal success state for onboarding.
    # State machine advance() is idempotent per-step, but LIVE → KYC_SUBMITTED
    # is an invalid transition so we guard at the function level.
    if state_machine.current_state(client_id) == ClientOnboardingState.LIVE:
        logger.info("client_id=%s already LIVE — no-op", client_id)
        print(f"Demo client {client_id} now LIVE")
        return

    logger.info("Starting onboarding for client_id=%s", client_id)

    # Step 1 — DRAFT → KYC_SUBMITTED
    state_machine.advance(client_id, ClientOnboardingState.KYC_SUBMITTED, evidence={})
    logger.info("client_id=%s advanced to KYC_SUBMITTED", client_id)

    # Step 2 — KYC_SUBMITTED → KYC_APPROVED
    now = datetime.now(tz=UTC)
    kyc_stub = ClientKYCStub(
        client_name="Demo Capital LP",
        client_email="demo@example.com",
        jurisdiction="USA",
        accredited_investor_claimed=True,
        submitted_at=now,
        approved_at=now,
        approval_notes="Manual demo approval",
    )
    state_machine.advance(
        client_id,
        ClientOnboardingState.KYC_APPROVED,
        evidence={
            "approved_at": kyc_stub.approved_at,
            "approval_notes": kyc_stub.approval_notes,
        },
    )
    logger.info("client_id=%s advanced to KYC_APPROVED", client_id)

    # Step 3 — KYC_APPROVED → DEPOSITED
    state_machine.advance(
        client_id,
        ClientOnboardingState.DEPOSITED,
        evidence={"deposit_txid": "0xdeadbeef0000000000000000000000000000000000000001"},
    )
    logger.info("client_id=%s advanced to DEPOSITED", client_id)

    # Step 4 — DEPOSITED → SUBSCRIBED (≥1 subscription to both cutover archetypes)
    subscriptions = [
        ClientShareClassSubscription(
            client_id=client_id,
            share_class_id="USDC_CUTOVER_V1",
            archetype_id="carry_staked_basis",
            allocation_pct=Decimal("50"),
        ),
        ClientShareClassSubscription(
            client_id=client_id,
            share_class_id="USDC_CUTOVER_V1",
            archetype_id="arbitrage_price_dispersion",
            allocation_pct=Decimal("50"),
        ),
    ]
    state_machine.advance(
        client_id,
        ClientOnboardingState.SUBSCRIBED,
        evidence={"subscriptions": subscriptions},
    )
    logger.info("client_id=%s advanced to SUBSCRIBED (2 archetypes)", client_id)

    # Step 5 — SUBSCRIBED → LIVE
    state_machine.advance(client_id, ClientOnboardingState.LIVE, evidence={})
    logger.info("client_id=%s advanced to LIVE", client_id)

    # Invariant assertion
    final_state: ClientOnboardingState = state_machine.current_state(client_id)
    if final_state != ClientOnboardingState.LIVE:
        raise AssertionError(f"Expected LIVE, got {final_state.value} for client_id={client_id}")
    print(f"Demo client {client_id} now LIVE")


# ---------------------------------------------------------------------------
# Phase 7.B — Treasury wiring + ping
# ---------------------------------------------------------------------------


async def wire_demo_treasury(
    copper_sdk: object,
    ceffu_api: object,
    defi_rpc_client: object,
) -> dict[TreasurySource, CustodyPingResult]:
    """Wire treasury endpoints + verify all sources are reachable.

    Args:
        copper_sdk: Mock or real Copper SDK object.
        ceffu_api: Mock or real CEFFU API client object.
        defi_rpc_client: Mock or real Web3 instance.

    Returns:
        Dict mapping TreasurySource to CustodyPingResult.

    Raises:
        AssertionError: If any source is not reachable.
    """
    pinger = CustodyPinger(
        copper_sdk=copper_sdk,
        ceffu_api=ceffu_api,
        defi_rpc_client=defi_rpc_client,
    )

    raw_results = await pinger.ping_all(DEMO_TREASURY_SOURCES)
    results: dict[TreasurySource, CustodyPingResult] = cast(
        "dict[TreasurySource, CustodyPingResult]", raw_results
    )

    source: TreasurySource
    result: CustodyPingResult
    for source, result in results.items():
        if not result.is_reachable:
            raise AssertionError(f"{source.value} not reachable: {result.error_message}")
        logger.info(
            "source=%s reachable latency_ms=%d balance=%s",
            source.value,
            result.latency_ms,
            result.balance_native,
        )

    print("All treasury sources pingable")
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def _run(
    cloud: str,
    confirm: bool,
    project_id: str = DEMO_PROJECT_ID,
) -> None:
    """Orchestrate Phase 7.A (onboarding) + 7.B (treasury wire + ping)."""
    if cloud != "local" and not confirm:
        print(
            "ERROR: --confirm required for non-local cloud targets. "
            "Re-run with --confirm to proceed.",
            file=sys.stderr,
        )
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # --- Phase 7.A: onboarding state machine ---
    if cloud == "gcp":
        bucket_name = f"{project_id}-client-state"
        state_store: StateStore = GCSStateStore(bucket_name=bucket_name)
        logger.info("Using GCSStateStore bucket=%s", bucket_name)
    else:
        state_store = InMemoryStateStore()
        logger.info("Using InMemoryStateStore (local/dry-run mode)")

    await seed_demo_client(state_store=state_store)

    # --- Phase 7.B: treasury wire + ping ---
    copper_sdk = _make_mock_copper_sdk()
    ceffu_api = _make_mock_ceffu_api()
    defi_rpc = _make_mock_web3()

    wire_results: dict[TreasurySource, CustodyPingResult] = await wire_demo_treasury(
        copper_sdk=copper_sdk,
        ceffu_api=ceffu_api,
        defi_rpc_client=defi_rpc,
    )

    # Persist treasury config to GCS (only in real cloud mode)
    if cloud == "gcp":
        treasury_bucket = f"{project_id}-treasury-config"
        _persist_treasury_config(
            client_id=DEMO_CLIENT_ID,
            sources=DEMO_TREASURY_SOURCES,
            bucket_name=treasury_bucket,
        )

    logger.info(
        "Phase 7 complete: client=%s sources_verified=%d",
        DEMO_CLIENT_ID,
        len(wire_results),
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Seed demo_client_001 through DRAFT to LIVE + wire treasury",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--cloud",
        choices=["gcp", "local"],
        default="local",
        help="Cloud target: 'gcp' writes to real GCS; 'local' uses in-memory (dry-run).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="Required safety gate for non-local cloud targets.",
    )
    parser.add_argument(
        "--project-id",
        default=DEMO_PROJECT_ID,
        help=f"GCP project ID (default: {DEMO_PROJECT_ID})",
    )
    args = parser.parse_args()

    asyncio.run(
        _run(
            cloud=str(args.cloud),
            confirm=bool(args.confirm),
            project_id=str(args.project_id),
        )
    )


if __name__ == "__main__":
    main()
