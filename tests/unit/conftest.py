"""Shared fixtures for client-reporting-api unit tests.

Key fixtures:
- ``registry_path`` (session, autouse): patches ``tranche_router._REGISTRY_PATH``
  to the repo-local ``configs/credentials-registry.yaml`` so all unit tests can
  resolve client configs without a sibling ``execution-service`` checkout.

- ``seeded_backfill_dir`` (module, autouse on test_core_coverage): creates a
  minimal ``data/backfill/PR/`` tree in a tmp dir and patches ``_DATA_DIR`` in
  both ``invoice_state`` and ``trade_analytics``.  This lets the data-dependent
  tests in ``TestTradeAnalyticsRealData``, ``TestMonthlyReportGenerator``,
  ``TestPnLChartGenerator``, ``TestDashboardGenerator``, and
  ``TestInvoiceState`` actually run rather than skip, while keeping tests
  hermetic (no real GCS / local-file dependency in CI).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Canonical path to the repo-local credentials-registry.yaml
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LOCAL_REGISTRY = _REPO_ROOT / "configs" / "credentials-registry.yaml"

# Minimal equity-curve: two data-points so date-range / latest-equity logic runs.
# Amounts are deliberately small but non-zero so fee computation produces results.
_EQUITY_CURVE_PR: list[dict[str, object]] = [
    {
        "date": "2026-02-17",
        "equity_usd": 335000.0,
        "usdt_balance": 335000.0,
        "btc_balance": 0.0,
        "btc_price_usd": 95000.0,
    },
    {
        "date": "2026-04-09",
        "equity_usd": 344000.0,
        "usdt_balance": 344000.0,
        "btc_balance": 0.0,
        "btc_price_usd": 83000.0,
        "transfer_usd": None,
    },
]

# Minimal bills_ledger: one realized PnL entry so compute_coin_breakdown returns coins.
_BILLS_LEDGER_PR: list[dict[str, object]] = [
    {
        "inst_id": "BTC-USDT",
        "change": 8690.0,
        "sub_type": "5",
        "timestamp": 1744185600000,  # 2026-04-09
    },
    {
        "inst_id": "BTC-USDT",
        "change": -50.0,
        "sub_type": "3",
        "timestamp": 1744185600000,
    },
]

# Minimal trades: one round-trip BTC trade so holding-time / volume paths execute.
_TRADES_PR: list[dict[str, object]] = [
    {
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.1,
        "cost": 9500.0,
        "datetime": "2026-04-01T00:00:00Z",
        "timestamp": 1743465600000,
    },
    {
        "symbol": "BTC/USDT",
        "side": "sell",
        "amount": 0.1,
        "cost": 9800.0,
        "datetime": "2026-04-09T00:00:00Z",
        "timestamp": 1744185600000,
    },
]


@pytest.fixture(scope="session", autouse=True)
def _patch_registry_path(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Redirect tranche_router._REGISTRY_PATH to the repo's own credentials-registry.yaml.

    This fixture runs for every test session so that ``get_client_config()``
    resolves clients declared in ``configs/credentials-registry.yaml`` without
    requiring a sibling ``execution-service`` checkout (which is not cloned in CI
    unless listed in ``dep_repos``).

    We use ``monkeypatch`` at session scope via the lower-level attribute-set
    pattern so the patch survives for the entire session (``monkeypatch`` is
    function-scoped by default; the session-scoped workaround is to set the
    attribute directly and restore it in a finaliser).
    """
    import client_reporting_api.core.tranche_router as _tr

    original = _tr._REGISTRY_PATH
    _tr._REGISTRY_PATH = _LOCAL_REGISTRY
    yield  # type: ignore[misc]
    _tr._REGISTRY_PATH = original


@pytest.fixture(scope="module")
def seeded_backfill_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write minimal client backfill fixtures to a tmp directory.

    Creates ``<tmp>/backfill/PR/`` with:
    - ``equity_curve.json``  — two data points; last equity ≈ $344 k
    - ``bills_ledger.json``  — one realized PnL + one fee entry
    - ``trades.json``        — one round-trip BTC trade

    The directory is returned so callers can point ``_DATA_DIR`` at it.
    """
    base = tmp_path_factory.mktemp("backfill")
    pr_dir = base / "PR"
    pr_dir.mkdir(parents=True, exist_ok=True)

    (pr_dir / "equity_curve.json").write_text(json.dumps(_EQUITY_CURVE_PR), encoding="utf-8")
    (pr_dir / "bills_ledger.json").write_text(json.dumps(_BILLS_LEDGER_PR), encoding="utf-8")
    (pr_dir / "trades.json").write_text(json.dumps(_TRADES_PR), encoding="utf-8")

    return base
