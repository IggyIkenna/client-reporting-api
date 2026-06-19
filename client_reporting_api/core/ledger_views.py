"""Ledger-derived operator-facing views — positions / balances / PnL totals.

Replaces the mock positions feed and the hardcoded ``realized_pnl="0.00"`` in
``api/routes/attribution.py`` with REAL, ledger-derived state. Given a client's
``LedgerRow`` tape (the as-if-filled fills + passive accruals) the pure
:func:`compute_ledger_views` folds it into:

- **positions** — the ``PositionLedgerRow`` list (``Σ delta`` with average-cost
  realised/unrealised PnL, via UTL :func:`materialize_position_ledger`).
- **balances** — GROUP-BY rollups of ``net_qty`` + ``unrealized_pnl`` over the
  position rows, sliced **by venue / by instrument / by share_class** (P5.1).
- **PnL totals** — ``Σ realized_pnl`` + ``Σ unrealized_pnl`` over the position
  rows — this is what replaces the hardcoded ``"0.00"``.

The ledger SOURCE is the pluggable seam (:func:`read_ledger_rows`) that reads
the engine-written parquet ledger from GCS (``ledger/ledger_type={instruction,
passive}/client_id={C}/…`` in the ``client-reports`` bucket). When no engine run
has written any shards yet the views are an HONEST zero/empty response (not mock
data).

SSOT: codex/09-strategy/operational/paper-batch-live-reconciliation.md §1+§4.3
Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P3.4 + P5.1).
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse

from unified_api_contracts import EventType, LedgerRow, PositionLedgerRow
from unified_trading_library import get_storage_client
from unified_trading_library.ledger import client_runs_prefix  # noqa: qg-deep-import
from unified_trading_library.ledger.materialize import (  # noqa: qg-deep-import
    materialize_position_ledger,  # not re-exported at UTL top level
)

logger = logging.getLogger(__name__)

#: The engine writes a run's InstructionLedger tape as newline-delimited JSON
#: (``ledger_type=instruction/{run_id}.jsonl``) under the canonical
#: client-addressable ``client_ledger_root`` — i.e. beneath
#: ``gs://{client-reports}/ledger/client_id={C}/run_id={R}/``. This reader lists
#: that per-client prefix (``client_runs_prefix``) and parses the SAME JSONL the
#: UTL ``write_run_ledger`` writer emitted, so the monitoring chain connects by
#: construction (writer + reader share the convention; no parquet/JSONL drift).
_INSTRUCTION_LEDGER_SUFFIX = ".jsonl"
_LEDGER_TYPE_INSTRUCTION = "ledger_type=instruction"

#: The own-field set of ``LedgerRow`` MINUS ``direction``. ``LedgerRow`` is
#: ``extra="forbid"``, so the reconciliation-only keys the writer stamps alongside
#: the row dump (``fill_model`` / ``strategy_instruction_id`` / ``correlation_id`` /
#: ``client_order_id`` / ``instrument_key``) must be dropped before
#: ``model_validate``. ``direction`` is the one COLLISION: it is a real ``LedgerRow``
#: field (a lower-case ``Direction`` enum), but the writer OVERWRITES it with the raw
#: UPPER-case fill side (e.g. ``"LONG"``) for the recon reader — so the JSONL value
#: is not a valid ``Direction``. The materialiser leaves ``LedgerRow.direction`` as
#: ``None`` (the signed ``delta`` is the authoritative side), so we drop the clobbered
#: ``direction`` key entirely and let it default to ``None`` — positions/PnL derive
#: the side from ``delta``, not ``direction``.
_LEDGER_ROW_FIELDS: frozenset[str] = frozenset(LedgerRow.model_fields) - {"direction"}


def _blob_run_date(blob_path: str) -> date | None:
    """Best-effort run date from a ``run_id=`` segment shaped ``…_YYYY-MM-DD…``.

    run_ids embed the run date (the paper-run launcher mints
    ``{client}_{strategy}_{YYYY-MM-DD}…``). When no parseable date is present the
    shard is treated as undated and always included (the ``as_of`` filter is a
    best-effort upper bound, never a silent drop of a real run).
    """
    for segment in blob_path.split("/"):
        if segment.startswith("run_id="):
            body = segment[len("run_id=") :]
            match = re.search(r"\d{4}-\d{2}-\d{2}", body)
            if match:
                try:
                    return date.fromisoformat(match.group(0))
                except ValueError:
                    return None
    return None


def _split_gs(uri: str) -> tuple[str, str]:
    """Split a ``gs://bucket/prefix`` URI into ``(bucket, prefix)`` (prefix kept as-is)."""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _is_instruction_blob(path: str, as_of_date: date | None) -> bool:
    """True iff ``path`` is an InstructionLedger JSONL within the ``as_of_date`` bound."""
    if not path.endswith(_INSTRUCTION_LEDGER_SUFFIX):
        return False
    if _LEDGER_TYPE_INSTRUCTION not in path:
        return False
    run_dt = _blob_run_date(path)
    return not (as_of_date is not None and run_dt is not None and run_dt > as_of_date)


def _parse_instruction_jsonl(raw: str) -> list[LedgerRow]:
    """Parse newline-delimited InstructionLedger JSON into ``LedgerRow``s.

    The writer stamps reconciliation-only keys (``direction`` / ``fill_model`` /
    ``instrument_key`` / ``strategy_instruction_id`` / ``correlation_id``)
    ALONGSIDE the ``LedgerRow`` dump; ``LedgerRow`` is ``extra="forbid"``, so only
    its own fields (minus the clobbered ``direction``) are kept before validating.
    """
    parsed: list[LedgerRow] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        ledger_fields = {k: v for k, v in record.items() if k in _LEDGER_ROW_FIELDS}
        parsed.append(LedgerRow.model_validate(ledger_fields))
    return parsed


def read_ledger_rows(
    client_id: str,
    as_of_date: date | None = None,
    cloud: str = "gcp",
) -> list[LedgerRow]:
    """Read a client's ``LedgerRow`` tape from the GCS run ledger.

    Lists the canonical per-client ledger prefix
    (``client_runs_prefix(client_id)`` →
    ``gs://{client-reports}/ledger/client_id={client_id}/``), then parses every
    ``ledger_type=instruction/{run_id}.jsonl`` object the engine wrote there into
    UAC :class:`LedgerRow`s. This is the SAME JSONL shape the UTL
    ``write_run_ledger`` writer emits and ``load_instruction_ledger_fills`` reads —
    so the engine→API monitoring chain connects by construction (no parquet/JSONL
    format drift, no path mismatch).

    Returns the ``INSTRUCTION`` (TRADE) rows across all of the client's runs.
    :func:`compute_ledger_views` folds these into positions / balances / PnL.
    (PassiveLedger accruals ride a separate ``ledger_type=passive`` prefix added by
    the carry engine wiring; only ``instruction`` exists today — when passive lands
    it is read here too and routed to realised PnL, never the position fold.)

    When the client has no run ledger yet this returns ``[]`` — the HONEST empty
    path: callers materialise zero positions / zero PnL, never mock data.

    Args:
        client_id: the client whose ledger to read (single-client scope — funds
            never cross clients; only ``client_id={client_id}`` is ever listed).
        as_of_date: optional inclusive upper-bound run date; runs dated after it
            are skipped (undated runs are always included).
        cloud: ``"gcp"`` or ``"aws"`` — passed to ``client_runs_prefix``.
    """
    runs_prefix = client_runs_prefix(client_id, cloud=cloud)
    bucket, prefix = _split_gs(runs_prefix)

    rows: list[LedgerRow] = []
    try:
        storage = get_storage_client()
        for blob_meta in storage.list_blobs(bucket, prefix=prefix):
            path = blob_meta.name
            if not _is_instruction_blob(path, as_of_date):
                continue
            raw = storage.download_bytes(bucket, path).decode("utf-8")
            rows.extend(_parse_instruction_jsonl(raw))
    except Exception as exc:
        logger.warning("read_ledger_rows: ledger scan failed for client=%s: %s", client_id, exc)

    return rows


def _decimal_str(value: Decimal | None) -> str | None:
    """Serialise a Decimal to a string (JSON-stable, no float rounding)."""

    return None if value is None else str(value)


def _rollup(
    rows: Sequence[PositionLedgerRow],
    key: str,
) -> list[dict[str, object]]:
    """GROUP-BY ``key`` rollup of ``net_qty`` + ``unrealized_pnl`` + ``realized_pnl``.

    ``key`` is the ``PositionLedgerRow`` attribute name to group on
    (``"venue"`` / ``"instrument_key"`` / ``"share_class"``). Sums are Decimal;
    the result preserves first-seen group order (deterministic).
    """

    buckets: OrderedDict[str, dict[str, Decimal]] = OrderedDict()
    for row in rows:
        bucket_key = str(getattr(row, key))
        agg = buckets.setdefault(
            bucket_key,
            {
                "net_qty": Decimal(0),
                "unrealized_pnl": Decimal(0),
                "realized_pnl": Decimal(0),
            },
        )
        agg["net_qty"] += row.net_qty
        agg["unrealized_pnl"] += row.unrealized_pnl or Decimal(0)
        agg["realized_pnl"] += row.realized_pnl or Decimal(0)

    return [
        {
            key: bucket_key,
            "net_qty": str(agg["net_qty"]),
            "unrealized_pnl": str(agg["unrealized_pnl"]),
            "realized_pnl": str(agg["realized_pnl"]),
        }
        for bucket_key, agg in buckets.items()
    ]


def realized_pnl_total(
    rows: Sequence[LedgerRow],
    *,
    marks: Mapping[str, Decimal],
    as_of: datetime,
    share_class_of: Mapping[str, str],
) -> Decimal:
    """Return the ledger-derived realised PnL total (typed Decimal).

    Σ over the ``PositionLedgerRow`` realised PnL (avg-cost trade closes, net of
    fees) PLUS the PASSIVE accrual cash flows. This is the value that replaces the
    former hardcoded ``realized_pnl="0.00"`` in the pnl route. ``Decimal(0)`` for
    an empty ledger (honest zero, not a placeholder).
    """

    trade_rows = [r for r in rows if r.event_type == EventType.TRADE]
    passive_rows = [r for r in rows if r.event_origin.value == "passive"]
    positions = materialize_position_ledger(
        trade_rows,
        marks=marks,
        as_of=as_of,
        share_class_of=share_class_of,
    )
    passive_pnl = sum((r.delta for r in passive_rows), Decimal(0))
    return sum((p.realized_pnl or Decimal(0) for p in positions), Decimal(0)) + passive_pnl


def _position_view(row: PositionLedgerRow) -> dict[str, object]:
    """Project a ``PositionLedgerRow`` to the operator-facing position dict."""

    return {
        "account_id": row.account_id,
        "client_id": row.client_id,
        "venue": row.venue,
        "instrument_key": row.instrument_key,
        "asset_canonical_id": row.asset_canonical_id,
        "asset_symbol": row.asset_symbol,
        "asset_class": row.asset_class.value,
        "share_class": row.share_class,
        "net_qty": str(row.net_qty),
        "avg_cost": _decimal_str(row.avg_cost),
        "mark_price": _decimal_str(row.mark_price),
        "quote_currency": row.quote_currency,
        "realized_pnl": _decimal_str(row.realized_pnl),
        "unrealized_pnl": _decimal_str(row.unrealized_pnl),
        "as_of": row.as_of.isoformat(),
    }


def compute_ledger_views(
    rows: Sequence[LedgerRow],
    *,
    marks: Mapping[str, Decimal],
    as_of: datetime,
    share_class_of: Mapping[str, str],
) -> dict[str, object]:
    """Fold a client's ledger tape into the operator-facing views.

    Pure function (no I/O). Materialises the ``PositionLedger`` via UTL
    :func:`materialize_position_ledger` then derives the balance rollups and the
    realised/unrealised PnL totals.

    Args:
        rows: the client's ``LedgerRow`` tape (TRADE fills + PASSIVE accruals).
        marks: ``asset_canonical_id -> mark price`` (joins on the per-group key).
        as_of: UTC instant the snapshot is valid at (must be tz-aware UTC).
        share_class_of: ``asset_canonical_id -> share_class`` (falls back to the
            canonical id when absent).

    Returns:
        A dict with ``positions`` (list of position dicts), ``balances`` (the
        ``by_venue`` / ``by_instrument`` / ``by_share_class`` rollups), and
        ``totals`` (``realized_pnl`` / ``unrealized_pnl`` / ``total_pnl`` strings).
        For an empty ``rows`` the positions + rollups are empty and totals are
        ``"0"`` — an honest zero response, never mock data.

    Only ``TRADE`` rows drive the ``PositionLedger`` (``Σ delta`` of the BASE
    asset). ``PASSIVE`` accrual rows (funding / staking / lending) carry a QUOTE
    cash flow in ``delta``, NOT a base-asset quantity, so feeding them to the
    position fold would corrupt ``net_qty``; they are summed into realised PnL
    separately (the carry IS the P&L for funding/basis strategies).
    """

    trade_rows = [r for r in rows if r.event_type == EventType.TRADE]
    passive_rows = [r for r in rows if r.event_origin.value == "passive"]

    positions = materialize_position_ledger(
        trade_rows,
        marks=marks,
        as_of=as_of,
        share_class_of=share_class_of,
    )

    passive_pnl = sum((r.delta for r in passive_rows), Decimal(0))
    realized_total = sum((p.realized_pnl or Decimal(0) for p in positions), Decimal(0)) + passive_pnl
    unrealized_total: Decimal = sum((p.unrealized_pnl or Decimal(0) for p in positions), Decimal(0))

    return {
        "positions": [_position_view(p) for p in positions],
        "balances": {
            "by_venue": _rollup(positions, "venue"),
            "by_instrument": _rollup(positions, "instrument_key"),
            "by_share_class": _rollup(positions, "share_class"),
        },
        "totals": {
            "realized_pnl": str(realized_total),
            "unrealized_pnl": str(unrealized_total),
            "total_pnl": str(realized_total + unrealized_total),
        },
    }


def _attr_amount(row: Mapping[str, object]) -> Decimal:
    """Parse a ``PnLAttributionRow`` ``amount`` (string-or-Decimal) to Decimal; 0 on garbage."""
    try:
        return Decimal(str(row.get("amount", "0")))
    except (ArithmeticError, ValueError):
        return Decimal(0)


def _attr_group(rows: Sequence[Mapping[str, object]], key: str) -> list[dict[str, str]]:
    """GROUP-BY ``key`` sum of attribution ``amount`` (deterministic first-seen order)."""
    buckets: OrderedDict[str, Decimal] = OrderedDict()
    for row in rows:
        bucket_key = str(row.get(key, "")) or "unknown"
        buckets[bucket_key] = buckets.get(bucket_key, Decimal(0)) + _attr_amount(row)
    return [{key: k, "amount": str(v)} for k, v in buckets.items()]


def attribution_breakdown(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Per-venue / per-instrument / per-factor / per-layer attribution rollups (P2.5.1).

    Folds the raw ``PnLAttributionRow`` records (from
    ``attribution_reader.read_attribution_rows`` — each carries ``venue`` /
    ``instrument_id`` / ``factor`` / ``layer`` / ``amount``) into four GROUP-BY
    sums, plus the grand total. This is the per-venue + per-instrument attribution
    VIEW off ``PnLAttributionRow`` — the operator's "where did the P&L come from"
    breakdown by venue, instrument, factor (CARRY / SLIPPAGE / …) and layer
    (STRATEGY / EXECUTION). Empty ``rows`` → all-empty rollups + ``"0"`` total
    (honest, never mock).
    """
    total = sum((_attr_amount(r) for r in rows), Decimal(0))
    return {
        "by_venue": _attr_group(rows, "venue"),
        "by_instrument": _attr_group(rows, "instrument_id"),
        "by_factor": _attr_group(rows, "factor"),
        "by_layer": _attr_group(rows, "layer"),
        "total_amount": str(total),
    }
