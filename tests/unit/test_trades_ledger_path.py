"""Tests for the legacy ``/api/v1/trades`` route's canonical-run ledger source.

The UI's paper-trading trades panel calls ``/api/v1/trades?client_id=`` (rewritten
from ``/api/client-reporting/trades``). The route now derives its tape from the
canonical paper-run InstructionLedger (the SAME run the positions/pnl/recon views
read) — so a paper-trading client shows its real fills, NOT an empty CeFi feed.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from unified_api_contracts.internal import FillModel, TradeFillRecord, make_trade_key

from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _mock_auth() -> Generator[None]:
    original = {k: os.environ.get(k) for k in ("DISABLE_AUTH", "DATA_MODE", "CLOUD_MOCK_MODE")}
    os.environ["DISABLE_AUTH"] = "true"
    os.environ["DATA_MODE"] = "live"  # exercise the live (non-mock) ledger path
    os.environ["CLOUD_MOCK_MODE"] = "false"

    import importlib

    utl = importlib.import_module("unified_trading_library.cloud_interface.api_auth")
    utl._get_auth_config.cache_clear()
    # The route module caches a UnifiedCloudConfig at import; force live mode on it.
    import client_reporting_api.api.routes.trades as trades_mod

    orig_mock = trades_mod._cloud_cfg.cloud_mock_mode
    orig_dm = trades_mod._cloud_cfg.data_mode
    trades_mod._cloud_cfg.cloud_mock_mode = False  # type: ignore[misc]
    trades_mod._cloud_cfg.data_mode = "live"  # type: ignore[misc]
    try:
        yield
    finally:
        trades_mod._cloud_cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
        trades_mod._cloud_cfg.data_mode = orig_dm  # type: ignore[misc]
        for key, val in original.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        utl._get_auth_config.cache_clear()


def _fill(*, ik: str, side: str, qty: str, price: str) -> TradeFillRecord:
    ts = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    return TradeFillRecord(
        trade_key=make_trade_key(ik, "i1", ts),
        instrument_key=ik,
        strategy_instruction_id="i1",
        tick_timestamp=ts,
        venue=ik.split(":")[0],
        side=side,
        qty=Decimal(qty),
        fill_price=Decimal(price),
        fees_in_quote=Decimal("0"),
        fill_model=FillModel.BENCHMARK,
    )


def test_trades_route_derives_from_canonical_run_ledger() -> None:
    fills = [
        _fill(ik="UNISWAP_V3:DEX_POOL:ETH", side="BUY", qty="2", price="3000"),
        _fill(ik="LIDO:STAKING:ETH", side="SUPPLY", qty="33", price="1"),
        _fill(ik="DERIBIT:PERPETUAL:ETH-PERP", side="LONG", qty="30.8", price="3000"),
    ]
    with patch(
        "client_reporting_api.api.routes.trades.read_canonical_run_fills",
        return_value=("paper-20260620004135-bbbb", fills),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/trades?client_id=firm-paper-determinism&limit=50")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["source"] == "ledger"
    assert payload["run_id"] == "paper-20260620004135-bbbb"
    assert payload["total"] == 3
    venues = {t["venue"] for t in payload["trades"]}
    assert venues == {"UNISWAP_V3", "LIDO", "DERIBIT"}
    # Sides project to the UI's buy/sell enum.
    assert all(t["side"] in ("buy", "sell") for t in payload["trades"])
    # Aggregates summed over the ledger fills.
    assert payload["aggregates"]["total_trades"] == 3


def test_trades_route_falls_back_to_collector_when_no_run() -> None:
    # No ledger run → legacy collector path (returns its own empty list here).
    class _EmptyCollector:
        def get_client_trades(self, client_id: str, limit: int = 50) -> list[object]:
            return []

    with (
        patch(
            "client_reporting_api.api.routes.trades.read_canonical_run_fills",
            return_value=(None, []),
        ),
        patch("client_reporting_api.api.routes.trades.get_collector", return_value=_EmptyCollector()),
        patch("client_reporting_api.api.routes.trades._backfill_trades", return_value=[]),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/trades?client_id=some-cefi-client&limit=50")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["source"] == "live"
    assert payload["run_id"] is None
    assert payload["trades"] == []
