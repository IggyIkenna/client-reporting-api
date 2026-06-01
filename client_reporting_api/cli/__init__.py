# pyright: reportPrivateUsage=false
"""client-reporting-manage CLI package.

The CLI is split across focused submodules:

  - ``shared``          : registry + credentials + exchange + pricing helpers
  - ``gcs_sync``        : MiFID II-compliant durable persistence helpers
  - ``pnl_fetch``       : venue-specific trading-PnL walkers
  - ``trades_and_orders``: trade/order history fetch + MiFID projection
  - ``equity_update``   : equity curve, transfer detection, balance snapshots
  - ``update_command``  : the ``update`` CLI sub-command
  - ``onboard_command`` : the ``onboard`` CLI sub-command
  - ``backfill_command``: the ``backfill`` CLI sub-command
  - ``status_command``  : the ``status`` CLI sub-command
  - ``main``            : argparse wiring + ``main()`` entry point

The public entry point wired in ``pyproject.toml`` is ``main()`` (below).
Helpers prefixed with ``_`` are re-exported here so existing test modules
that patch ``client_reporting_api.cli._foo`` keep working without churn.
"""

from __future__ import annotations

from client_reporting_api.cli.backfill_command import cmd_backfill
from client_reporting_api.cli.equity_update import (
    _BALANCE_SKIP_KEYS,
    _build_equity_point,
    _compute_update_gap,
    _detect_transfer,
    _fill_gap_days,
    _load_existing_equity_curve,
    _maybe_attach_transfer,
    _merge_today_point_into_curve,
    _open_exchange_with_balance,
    _parse_balances,
    _persist_balance_snapshot,
    _persist_positions,
    _update_summary,
)
from client_reporting_api.cli.gcs_sync import (
    _download_from_gcs,
    _get_gcs_bucket,
    _sync_to_gcs,
)
from client_reporting_api.cli.main import main
from client_reporting_api.cli.onboard_command import (
    _register_new_client,
    _store_onboard_secrets,
    _validate_onboard_credentials,
    cmd_onboard,
)
from client_reporting_api.cli.pnl_fetch import (
    _fetch_binance_pnl,
    _fetch_okx_pnl,
    _fetch_pnl_since,
    _okx_bill_pnl,
)
from client_reporting_api.cli.shared import (
    DATA_DIR,
    REGISTRY_PATH,
    WORKSPACE,
    DecimalEncoder,
    _create_exchange,
    _fetch_credentials,
    _GapInfo,
    _get_active_clients,
    _get_secret,
    _get_usd_price,
    _load_full_registry,
    _load_registry,
    _print_summary,
)
from client_reporting_api.cli.status_command import (
    _format_age,
    _read_status_equity,
    _read_status_summary,
    cmd_status,
)
from client_reporting_api.cli.trades_and_orders import (
    _extract_cancel_reason,
    _fetch_orders_for_symbol,
    _load_existing_orders,
    _load_existing_trades,
    _project_order_record,
    _resolve_trade_symbols,
    _slippage_bps,
    _update_order_history,
    _update_recent_trades,
)
from client_reporting_api.cli.update_command import _update_client, cmd_update

__all__ = [
    "DATA_DIR",
    "REGISTRY_PATH",
    "WORKSPACE",
    "_BALANCE_SKIP_KEYS",
    "DecimalEncoder",
    "_GapInfo",
    "_build_equity_point",
    "_compute_update_gap",
    "_create_exchange",
    "_detect_transfer",
    "_download_from_gcs",
    "_extract_cancel_reason",
    "_fetch_binance_pnl",
    "_fetch_credentials",
    "_fetch_okx_pnl",
    "_fetch_orders_for_symbol",
    "_fetch_pnl_since",
    "_fill_gap_days",
    "_format_age",
    "_get_active_clients",
    "_get_gcs_bucket",
    "_get_secret",
    "_get_usd_price",
    "_load_existing_equity_curve",
    "_load_existing_orders",
    "_load_existing_trades",
    "_load_full_registry",
    "_load_registry",
    "_maybe_attach_transfer",
    "_merge_today_point_into_curve",
    "_okx_bill_pnl",
    "_open_exchange_with_balance",
    "_parse_balances",
    "_persist_balance_snapshot",
    "_persist_positions",
    "_print_summary",
    "_project_order_record",
    "_read_status_equity",
    "_read_status_summary",
    "_register_new_client",
    "_resolve_trade_symbols",
    "_slippage_bps",
    "_store_onboard_secrets",
    "_sync_to_gcs",
    "_update_client",
    "_update_order_history",
    "_update_recent_trades",
    "_update_summary",
    "_validate_onboard_credentials",
    "cmd_backfill",
    "cmd_onboard",
    "cmd_status",
    "cmd_update",
    "main",
]
