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
- **spec_coverage** (P11.22) — ``{ledger_root}/spec_coverage/{run_id}.json`` (the UTL
  ``write_run_spec_coverage`` sidecar). Shape:
  ``{"run_id": R, "specs": [{"archetype","slot_label","present_bars","expected_bars",
  "coverage_pct","threshold_pct","state"}, ...]}`` — the per-spec WINDOW coverage for
  every spec the run DROVE, ``state`` ∈ {``drivable``, ``drivable_thin``}. The thin
  subset (ran on a SPARSE window, below the threshold) is a SUBSET of the drivable book
  — surfaced so a sparse-window backtest is FLAGGED, not silently trusted like a full
  one. Absent sidecar (a pre-P11.22 run) → no thin flags (binary drivable/skipped view).

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

#: The per-spec window-coverage sidecar (P11.22), written by UTL
#: ``write_run_spec_coverage`` at ``{ledger_root}/spec_coverage/{run_id}.json`` — the
#: third "drivable-but-thin" state for every spec the run actually DROVE.
_SPEC_COVERAGE_KEY = "spec_coverage"

#: A spec whose window coverage is below the threshold (ran on a SPARSE window).
_THIN_STATE = "drivable_thin"


def _split_gs(uri: str) -> tuple[str, str]:
    """Split a ``gs://bucket/prefix`` URI into ``(bucket, prefix)`` (prefix kept as-is)."""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _as_float(value: object) -> float:
    """Coerce a sidecar numeric field to float; non-numeric → 0.0 (never crash a row)."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    """Coerce a sidecar numeric field to int; non-numeric → 0 (never crash a row)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


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


def read_spec_coverage(
    client_id: str,
    run_id: str,
    cloud: str = "gcp",
) -> list[dict[str, object]]:
    """Read the run's per-spec window-coverage sidecar from GCS (P11.22).

    Lists ``{client_ledger_root}/spec_coverage/{run_id}.json`` and returns its
    ``specs`` array verbatim (each entry: ``archetype`` / ``slot_label`` /
    ``present_bars`` / ``expected_bars`` / ``coverage_pct`` / ``threshold_pct`` /
    ``state``). Returns ``[]`` when the sidecar is absent (a run written before
    P11.22, or a transient read failure) — the HONEST empty path, never a crash, so
    the panel degrades to the binary drivable/skipped view with no thin flags.
    """
    specs: list[dict[str, object]] = []
    try:
        ledger_root = client_ledger_root(client_id, run_id, cloud=cloud)
        bucket, root_prefix = _split_gs(ledger_root)
        obj_key = f"{root_prefix}{_SPEC_COVERAGE_KEY}/{run_id}.json"
        storage = get_storage_client()
        raw = storage.download_bytes(bucket, obj_key).decode("utf-8")
        payload = cast(object, json.loads(raw))
        entries: list[object] = []
        if isinstance(payload, dict):
            typed_payload = cast(dict[str, object], payload)
            entries = cast(list[object], typed_payload.get("specs", []))
        for entry in entries:
            if isinstance(entry, dict):
                specs.append(cast(dict[str, object], entry))
    except Exception as exc:
        logger.info(
            "read_spec_coverage: no spec-coverage sidecar for client=%s run=%s (%s)",
            client_id,
            run_id,
            exc,
        )
    return specs


def _thin_rows(spec_coverage: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project the DRIVABLE-BUT-THIN coverage entries to the panel shape (P11.22).

    Surfaces ONLY the specs flagged ``drivable_thin`` (ran on a sparse window) — the
    full-coverage specs are not listed (the panel flags the thin runs, not the healthy
    ones). Each row carries the verbatim ``spec`` (full ``slot_label``), ``archetype``,
    best-effort ``venue`` / ``coin`` parsed from the label, and the ``coverage_pct`` /
    ``threshold_pct`` / ``present_bars`` / ``expected_bars``. Sorted by coverage ascending
    (the thinnest, most-suspect run first).
    """
    rows: list[dict[str, object]] = []
    for entry in spec_coverage:
        if str(entry.get("state", "")) != _THIN_STATE:
            continue
        slot_label = str(entry.get("slot_label", ""))
        archetype = _archetype_of(slot_label, str(entry.get("archetype", "")))
        venue, coin = _venue_coin_from_slot(slot_label)
        rows.append(
            {
                "spec": slot_label,
                "archetype": archetype,
                "venue": venue,
                "coin": coin,
                "coverage_pct": _as_float(entry.get("coverage_pct")),
                "threshold_pct": _as_float(entry.get("threshold_pct")),
                "present_bars": _as_int(entry.get("present_bars")),
                "expected_bars": _as_int(entry.get("expected_bars")),
            }
        )
    rows.sort(key=lambda r: (r["coverage_pct"], r["archetype"], r["spec"]))
    return rows


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
    thin_rows: list[dict[str, object]],
) -> dict[str, object]:
    """Compute the honest coverage summary from the drivable book + skipped + thin specs.

    The manifest ``strategy_ids`` are the DRIVABLE specs (the run ran them); the
    skipped rows are a DISJOINT broader declared universe (specs that did not run).
    So ``total_specs = drivable + skipped`` and the per-archetype split counts both
    sides — never a re-derivation, just a fold of the real lists.

    ``drivable_thin`` (P11.22) is a SUBSET of ``drivable`` — the specs that ran on a
    sparse window (below the coverage threshold), so it is NOT added to ``total`` (a
    thin spec is still a drivable spec); it is a flag WITHIN drivable. The per-archetype
    ``drivable_thin`` count lets the panel show "N of the drivable ran thin".

    ``by_archetype`` is emitted as a SORTED LIST of rows
    (``{archetype, drivable, drivable_thin, skipped, total}``) — the canonical list
    shape the paper-trading UI's ``DataQualityCoverageRow[]`` contract reads with
    ``.map`` / ``.reduce`` (a dict would crash the panel's ``.reduce`` — UI is
    array-shaped).
    """
    counts: OrderedDict[str, dict[str, int]] = OrderedDict()

    def _bucket(arch: str) -> dict[str, int]:
        return counts.setdefault(arch, {"drivable": 0, "drivable_thin": 0, "skipped": 0})

    for sid in strategy_ids:
        _bucket(_archetype_of(sid, sid))["drivable"] += 1
    for row in skipped_rows:
        _bucket(row["archetype"] or "UNKNOWN")["skipped"] += 1
    for row in thin_rows:
        _bucket(str(row.get("archetype") or "UNKNOWN"))["drivable_thin"] += 1

    drivable = len(strategy_ids)
    skipped_n = len(skipped_rows)
    thin_n = len(thin_rows)
    by_archetype: list[dict[str, object]] = [
        {
            "archetype": arch,
            "drivable": v["drivable"],
            "drivable_thin": v["drivable_thin"],
            "skipped": v["skipped"],
            "total": v["drivable"] + v["skipped"],
        }
        for arch, v in sorted(counts.items())
    ]
    return {
        "total_specs": drivable + skipped_n,
        "drivable": drivable,
        "drivable_thin": thin_n,
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
            "coverage": {"total_specs": 0, "drivable": 0, "drivable_thin": 0, "skipped": 0, "by_archetype": []},
            "skipped": [],
            "thin_specs": [],
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
    # P11.22 — the per-spec window-coverage sidecar subdivides the drivable book into
    # full vs drivable-but-thin (ran on a sparse window). Absent sidecar → no thin rows
    # (a pre-P11.22 run) → the panel degrades to the binary drivable/skipped view.
    thin_rows = _thin_rows(read_spec_coverage(client_id, run_id, cloud=cloud))
    if not skipped_rows and not thin_rows and not note:
        note = "run ran every declared spec at full coverage (no skipped / thin specs)"

    return {
        "run_id": run_id,
        "coverage": _coverage(strategy_ids, skipped_rows, thin_rows),
        "skipped": skipped_rows,
        "thin_specs": thin_rows,
        "note": note,
    }
