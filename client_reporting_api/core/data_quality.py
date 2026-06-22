"""Run-level data-quality view — what the paper book DECLARED but could NOT run.

The paper-trading dashboard needs to show the operator, for a client's canonical
paper run, WHAT DATA IS MISSING / INCOMPLETE: the honest-absence ``skipped_specs``
sidecar the engine pinned beside the four run ledgers, plus a simple coverage
summary derived from the ``run_manifest`` strategy book.

Sources (all REAL, canonical — no fabrication):

- **skipped_specs** — ``{ledger_root}/skipped_specs/{run_id}.json`` (the UTL
  ``write_run_skipped_specs`` sidecar). Shape:
  ``{"run_id": R, "skipped": [{"archetype","slot_label","reason"}, ...]}`` — the
  list of declared specs that had NO real GCS data in the window, or an engine
  that cannot drive them. NEVER a synthetic fill. Read via the SAME
  :func:`resolve_canonical_run` + ``client_ledger_root`` the other ledger views
  use, so the data-quality panel keys off the SAME run as positions / trades /
  pnl (no per-endpoint run drift).
- **run_manifest** — ``{ledger_root}/run_manifest.json`` (UTL
  ``read_run_manifest``). Its ``strategy_ids`` is the DRIVABLE book — the specs
  the run actually ran. The skipped slot_labels are a DISJOINT broader universe
  (specs declared that did not run), so honest coverage is:
  ``total_specs = drivable + skipped``, ``drivable = len(strategy_ids)``.

When the run has no ``skipped_specs`` sidecar (a run that ran everything it
declared) the view returns an empty ``skipped`` list + a note — never a crash.

SSOT: codex/09-strategy/operational/paper-batch-live-reconciliation.md (run
ledgers + honest-absence sidecar).
Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P11.19).
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import date
from typing import cast
from urllib.parse import urlparse

from unified_trading_library import get_storage_client
from unified_trading_library.ledger import (  # noqa: qg-deep-import
    client_ledger_root,
    read_run_manifest,
)

from client_reporting_api.core.ledger_views import resolve_canonical_run

logger = logging.getLogger(__name__)

#: The honest-absence sidecar partition, written by UTL ``write_run_skipped_specs``
#: at ``{ledger_root}/skipped_specs/{run_id}.json``.
_SKIPPED_SPECS_KEY = "skipped_specs"


def _split_gs(uri: str) -> tuple[str, str]:
    """Split a ``gs://bucket/prefix`` URI into ``(bucket, prefix)`` (prefix kept as-is)."""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _archetype_of(slot_label: str, fallback: str) -> str:
    """Resolve a spec's archetype from its ``ARCHETYPE@slot`` label, else ``fallback``.

    A skipped entry carries its own ``archetype`` field (the fallback). A manifest
    ``strategy_id`` is shaped ``ARCHETYPE@venue-coin-...`` so the archetype is the
    segment before the first ``@``.
    """
    head, sep, _ = slot_label.partition("@")
    return head if sep and head else fallback


def _venue_coin_from_slot(slot_label: str) -> tuple[str, str]:
    """Best-effort ``(venue, coin)`` from an ``ARCHETYPE@venue-coin-...`` slot label.

    Slot labels embed the venue + coin in the post-``@`` body (e.g.
    ``CARRY_BASIS_PERP@binance-btc-1h-usdt-v3-prod`` → venue ``binance`` coin
    ``btc``). This is a display convenience only — the authoritative spec string is
    always carried verbatim as ``spec`` (the full ``slot_label``); a label that does
    not match the convention yields ``("", "")`` rather than a wrong guess.
    """
    _, sep, body = slot_label.partition("@")
    if not sep or not body:
        return "", ""
    parts = body.split("-")
    venue = parts[0] if parts else ""
    coin = parts[1] if len(parts) > 1 else ""
    return venue, coin


def read_skipped_specs(
    client_id: str,
    run_id: str,
    cloud: str = "gcp",
) -> list[dict[str, str]]:
    """Read the run's honest-absence ``skipped_specs`` sidecar from GCS.

    Lists ``{client_ledger_root}/skipped_specs/{run_id}.json`` and returns its
    ``skipped`` array verbatim (each entry: ``archetype`` / ``slot_label`` /
    ``reason``). Returns ``[]`` when the sidecar is absent (a run that ran
    everything it declared) — the HONEST empty path, never a crash.
    """
    skipped: list[dict[str, str]] = []
    try:
        ledger_root = client_ledger_root(client_id, run_id, cloud=cloud)
        bucket, root_prefix = _split_gs(ledger_root)
        obj_key = f"{root_prefix}{_SKIPPED_SPECS_KEY}/{run_id}.json"
        storage = get_storage_client()
        raw = storage.download_bytes(bucket, obj_key).decode("utf-8")
        payload = cast(object, json.loads(raw))
        entries: list[object] = []
        if isinstance(payload, dict):
            typed_payload = cast(dict[str, object], payload)
            entries = cast(list[object], typed_payload.get("skipped", []))
        for entry in entries:
            if isinstance(entry, dict):
                typed_entry = cast(dict[object, object], entry)
                skipped.append({str(k): str(v) for k, v in typed_entry.items()})
    except Exception as exc:
        # Absent sidecar (the common, honest case) OR a transient read failure:
        # both degrade to an empty skipped list — the endpoint stays 200 and the
        # coverage view shows the manifest book without fabricated gaps.
        logger.info(
            "read_skipped_specs: no skipped sidecar for client=%s run=%s (%s)",
            client_id,
            run_id,
            exc,
        )
    return skipped


def _grouped_skipped(skipped: list[dict[str, str]]) -> list[dict[str, str]]:
    """Project raw skipped entries to the API shape, sorted by (reason, archetype).

    Each output row carries the verbatim ``spec`` (the full ``slot_label``), the
    ``archetype``, the ``reason``, and best-effort ``venue`` / ``coin`` parsed from
    the slot label. Sorting by ``reason`` then ``archetype`` makes the panel
    naturally group "all the no-data specs" / "all the unwired-engine specs".
    """
    rows: list[dict[str, str]] = []
    for entry in skipped:
        slot_label = entry.get("slot_label", "")
        archetype = _archetype_of(slot_label, entry.get("archetype", ""))
        venue, coin = _venue_coin_from_slot(slot_label)
        rows.append(
            {
                "spec": slot_label,
                "archetype": archetype,
                "venue": venue,
                "coin": coin,
                "reason": entry.get("reason", ""),
            }
        )
    rows.sort(key=lambda r: (r["reason"], r["archetype"], r["spec"]))
    return rows


def _coverage(
    strategy_ids: tuple[str, ...],
    skipped_rows: list[dict[str, str]],
) -> dict[str, object]:
    """Compute the honest coverage summary from the drivable book + skipped specs.

    The manifest ``strategy_ids`` are the DRIVABLE specs (the run ran them); the
    skipped rows are a DISJOINT broader declared universe (specs that did not run).
    So ``total_specs = drivable + skipped`` and the per-archetype split counts both
    sides — never a re-derivation, just a fold of the two real lists.

    ``by_archetype`` is emitted as a SORTED LIST of rows
    (``{archetype, drivable, skipped, total}``) — the canonical list shape the
    paper-trading UI's ``DataQualityCoverageRow[]`` contract reads with ``.map`` /
    ``.reduce`` (a dict would crash the panel's ``.reduce`` — UI is array-shaped).
    """
    counts: OrderedDict[str, dict[str, int]] = OrderedDict()

    def _bucket(arch: str) -> dict[str, int]:
        return counts.setdefault(arch, {"drivable": 0, "skipped": 0})

    for sid in strategy_ids:
        _bucket(_archetype_of(sid, sid))["drivable"] += 1
    for row in skipped_rows:
        _bucket(row["archetype"] or "UNKNOWN")["skipped"] += 1

    drivable = len(strategy_ids)
    skipped_n = len(skipped_rows)
    by_archetype: list[dict[str, object]] = [
        {
            "archetype": arch,
            "drivable": v["drivable"],
            "skipped": v["skipped"],
            "total": v["drivable"] + v["skipped"],
        }
        for arch, v in sorted(counts.items())
    ]
    return {
        "total_specs": drivable + skipped_n,
        "drivable": drivable,
        "skipped": skipped_n,
        "by_archetype": by_archetype,
    }


def compute_data_quality(
    client_id: str,
    as_of_date: date | None = None,
    cloud: str = "gcp",
) -> dict[str, object]:
    """Build the run-level data-quality payload for a client's canonical paper run.

    Resolves the SAME canonical run every other ledger view keys off, reads its
    ``run_manifest`` (drivable book) + ``skipped_specs`` sidecar (honest-absence),
    and folds them into ``{run_id, coverage, skipped, note}``. Returns an honest
    empty payload (``run_id=None``, zeroed coverage) when the client has no paper
    run yet. Never raises — a missing manifest / sidecar degrades to a note.
    """
    run_id = resolve_canonical_run(client_id, as_of_date=as_of_date, cloud=cloud)
    if run_id is None:
        return {
            "run_id": None,
            "coverage": {"total_specs": 0, "drivable": 0, "skipped": 0, "by_archetype": []},
            "skipped": [],
            "note": "no paper run yet for this client",
        }

    strategy_ids: tuple[str, ...] = ()
    note = ""
    try:
        manifest = read_run_manifest(client_ledger_root(client_id, run_id, cloud=cloud))
        strategy_ids = manifest.strategy_ids
    except Exception as exc:
        note = "run_manifest unavailable — coverage shows skipped specs only"
        logger.info("compute_data_quality: manifest read failed for run=%s (%s)", run_id, exc)

    skipped_rows = _grouped_skipped(read_skipped_specs(client_id, run_id, cloud=cloud))
    if not skipped_rows and not note:
        note = "run ran every declared spec (no skipped_specs sidecar)"

    return {
        "run_id": run_id,
        "coverage": _coverage(strategy_ids, skipped_rows),
        "skipped": skipped_rows,
        "note": note,
    }
