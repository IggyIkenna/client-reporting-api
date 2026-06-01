"""Unit tests for seed_demo_client.py — Phase 7.A + 7.B.

Covers:
  1. Happy-path DRAFT → LIVE traversal (8 state transitions, 8 assertions)
  2. Each individual transition validates required evidence (malformed rejection)
  3. ping_all returns all three sources reachable
  4. Malformed evidence rejected (missing deposit_txid)
  5. Missing subscriptions rejected
  6. Idempotent re-run (calling seed_demo_client twice does NOT raise)
  7. Treasury config dict contains expected 3 sources
  8. _run local mode completes without error
  9. _run requires --confirm for gcp mode
  10. ping_all with unreachable source raises AssertionError

Per ``wallet_treasury_client_flow_2026_05_10.md`` Phase 7.A + 7.B.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from unified_api_contracts.internal.domain.client_lifecycle import ClientOnboardingState
from unified_api_contracts.internal.domain.treasury import TreasurySource
from unified_trading_library.client_lifecycle.onboarding import (  # noqa: qg-deep-import
    ClientOnboardingStateMachine,
    InMemoryStateStore,
    InvalidStateTransitionError,
)

# Import under test — scripts/ is on sys.path via pytest.ini pythonpath=["."]
from scripts.seed_demo_client import (  # type: ignore[import-untyped]
    DEMO_CLIENT_ID,
    DEMO_TREASURY_SOURCES,
    _make_mock_ceffu_api,
    _make_mock_copper_sdk,
    _make_mock_web3,
    _run,
    seed_demo_client,
    wire_demo_treasury,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failing_sdk() -> MagicMock:
    """SDK that raises RuntimeError on any call."""
    sdk = MagicMock()
    sdk.get_portfolio_balance.side_effect = RuntimeError("network_error: simulated failure")
    sdk.get_account_balance.side_effect = RuntimeError("network_error: simulated failure")
    sdk.eth = MagicMock()
    sdk.eth.get_balance.side_effect = RuntimeError("network_error: simulated failure")
    return sdk


# ---------------------------------------------------------------------------
# Test 1: Happy-path DRAFT → LIVE (all 5 state transitions succeed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_draft_to_live() -> None:
    """Full DRAFT → KYC_SUBMITTED → KYC_APPROVED → DEPOSITED → SUBSCRIBED → LIVE."""
    store = InMemoryStateStore()
    captured = io.StringIO()
    with redirect_stdout(captured):
        await seed_demo_client(state_store=store)
    output = captured.getvalue()

    state_machine = ClientOnboardingStateMachine(state_store=store)
    final_state = state_machine.current_state(DEMO_CLIENT_ID)

    assert final_state == ClientOnboardingState.LIVE
    assert f"Demo client {DEMO_CLIENT_ID} now LIVE" in output


# ---------------------------------------------------------------------------
# Test 2: Audit log has exactly 5 transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_has_five_transitions() -> None:
    store = InMemoryStateStore()
    await seed_demo_client(state_store=store)
    sm = ClientOnboardingStateMachine(state_store=store)
    log = sm.audit_log(DEMO_CLIENT_ID)
    # DRAFT→KYC_SUBMITTED, KYC_SUBMITTED→KYC_APPROVED, KYC_APPROVED→DEPOSITED,
    # DEPOSITED→SUBSCRIBED, SUBSCRIBED→LIVE
    assert len(log) == 5
    states_from = [t.from_state for t in log]
    states_to = [t.to_state for t in log]
    assert states_from[0] == ClientOnboardingState.DRAFT
    assert states_to[-1] == ClientOnboardingState.LIVE


# ---------------------------------------------------------------------------
# Test 3: KYC_APPROVED requires approved_at + approval_notes (malformed rejection)
# ---------------------------------------------------------------------------


def test_kyc_approved_without_evidence_raises() -> None:
    store = InMemoryStateStore()
    sm = ClientOnboardingStateMachine(state_store=store)
    # Advance to KYC_SUBMITTED first
    sm.advance(DEMO_CLIENT_ID, ClientOnboardingState.KYC_SUBMITTED, evidence={})
    # Missing both required fields
    with pytest.raises(InvalidStateTransitionError, match="approved_at"):
        sm.advance(DEMO_CLIENT_ID, ClientOnboardingState.KYC_APPROVED, evidence={})


# ---------------------------------------------------------------------------
# Test 4: DEPOSITED requires deposit_txid (malformed rejection)
# ---------------------------------------------------------------------------


def test_deposited_without_txid_raises() -> None:
    store = InMemoryStateStore()
    sm = ClientOnboardingStateMachine(state_store=store)
    sm.advance(DEMO_CLIENT_ID, ClientOnboardingState.KYC_SUBMITTED, evidence={})
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    sm.advance(
        DEMO_CLIENT_ID,
        ClientOnboardingState.KYC_APPROVED,
        evidence={"approved_at": now, "approval_notes": "test"},
    )
    with pytest.raises(InvalidStateTransitionError, match="deposit_txid"):
        sm.advance(DEMO_CLIENT_ID, ClientOnboardingState.DEPOSITED, evidence={})


# ---------------------------------------------------------------------------
# Test 5: SUBSCRIBED requires non-empty subscriptions list
# ---------------------------------------------------------------------------


def test_subscribed_without_subscriptions_raises() -> None:
    store = InMemoryStateStore()
    sm = ClientOnboardingStateMachine(state_store=store)
    sm.advance(DEMO_CLIENT_ID, ClientOnboardingState.KYC_SUBMITTED, evidence={})
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    sm.advance(
        DEMO_CLIENT_ID,
        ClientOnboardingState.KYC_APPROVED,
        evidence={"approved_at": now, "approval_notes": "test"},
    )
    sm.advance(
        DEMO_CLIENT_ID,
        ClientOnboardingState.DEPOSITED,
        evidence={"deposit_txid": "0xabc"},
    )
    with pytest.raises(InvalidStateTransitionError, match="subscriptions"):
        sm.advance(DEMO_CLIENT_ID, ClientOnboardingState.SUBSCRIBED, evidence={"subscriptions": []})


# ---------------------------------------------------------------------------
# Test 6: ping_all returns all three sources reachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_all_returns_all_reachable() -> None:
    copper_sdk = _make_mock_copper_sdk(balance_usd=100_000.0)
    ceffu_api = _make_mock_ceffu_api(usdc_balance=50_000.0)
    defi_rpc = _make_mock_web3()

    captured = io.StringIO()
    with redirect_stdout(captured):
        results = await wire_demo_treasury(
            copper_sdk=copper_sdk,
            ceffu_api=ceffu_api,
            defi_rpc_client=defi_rpc,
        )

    assert len(results) == 3
    for source in [TreasurySource.COPPER, TreasurySource.CEFFU, TreasurySource.DEFI_HOT_WALLET]:
        assert source in results
        assert results[source].is_reachable is True
    assert "All treasury sources pingable" in captured.getvalue()


# ---------------------------------------------------------------------------
# Test 7: Idempotent re-run — calling seed_demo_client twice does NOT raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_rerun() -> None:
    store = InMemoryStateStore()
    await seed_demo_client(state_store=store)
    # Second call should be a no-op (state machine advance is idempotent)
    await seed_demo_client(state_store=store)

    sm = ClientOnboardingStateMachine(state_store=store)
    assert sm.current_state(DEMO_CLIENT_ID) == ClientOnboardingState.LIVE


# ---------------------------------------------------------------------------
# Test 8: Treasury sources config dict contains expected 3 keys
# ---------------------------------------------------------------------------


def test_demo_treasury_sources_has_three_keys() -> None:
    assert TreasurySource.COPPER in DEMO_TREASURY_SOURCES
    assert TreasurySource.CEFFU in DEMO_TREASURY_SOURCES
    assert TreasurySource.DEFI_HOT_WALLET in DEMO_TREASURY_SOURCES
    assert len(DEMO_TREASURY_SOURCES) == 3


# ---------------------------------------------------------------------------
# Test 9: _run local mode completes without error (end-to-end smoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_local_mode_completes() -> None:
    captured = io.StringIO()
    with redirect_stdout(captured):
        await _run(cloud="local", confirm=False)
    output = captured.getvalue()
    assert "now LIVE" in output
    assert "pingable" in output


# ---------------------------------------------------------------------------
# Test 10: _run gcp mode without --confirm exits with SystemExit(1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_gcp_without_confirm_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        await _run(cloud="gcp", confirm=False)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test 11: wire_demo_treasury raises AssertionError when source unreachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wire_treasury_unreachable_source_raises() -> None:
    failing_sdk = _make_failing_sdk()
    # Copper fails; CEFFU + DeFi succeed
    ceffu_api = _make_mock_ceffu_api()
    defi_rpc = _make_mock_web3()

    with pytest.raises(AssertionError, match="COPPER"):
        await wire_demo_treasury(
            copper_sdk=failing_sdk,
            ceffu_api=ceffu_api,
            defi_rpc_client=defi_rpc,
        )


# ---------------------------------------------------------------------------
# Test 12: Copper balance reflected in ping result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copper_balance_in_ping_result() -> None:
    copper_sdk = _make_mock_copper_sdk(balance_usd=123_456.78)
    ceffu_api = _make_mock_ceffu_api()
    defi_rpc = _make_mock_web3()

    results = await wire_demo_treasury(
        copper_sdk=copper_sdk,
        ceffu_api=ceffu_api,
        defi_rpc_client=defi_rpc,
    )

    copper_result = results[TreasurySource.COPPER]
    assert copper_result.is_reachable is True
    assert copper_result.balance_usd == Decimal("123456.78")
