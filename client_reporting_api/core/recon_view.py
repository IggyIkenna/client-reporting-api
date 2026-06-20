"""Latest paper⟷batch determinism verdict for the paper-trading dashboard (P2.5.2).

Computes the headline "is paper ≡ batch (ε=0)?" verdict the dashboard's
``useLedgerRecon`` panel renders — by reading the client's REAL paper
InstructionLedger + its batch-rerun ledger from GCS and keying the fills by
``trade_id`` (the deterministic UAC trade_key).

Why computed here (not imported from batch-live-reconciliation-service): the
service-dependency HARD RULE forbids client-reporting-api importing a peer
service. The keyed comparison is the SAME one
``batch_live_reconciliation_service.engine.trade_recon.reconcile_day`` performs
(match on ``trade_key``; DETERMINISTIC iff no unmatched + every matched pair has
identical signed delta + fill price) — re-expressed over the UAC ``LedgerRow``
the API already reads. The BLRS daily stage remains the authoritative cron writer
of the per-day report; this is the dashboard's read-time projection of the same
ledgers, so the badge reflects the real run with no peer-service coupling.

Layout it reads (written by the determinism spine):
  paper:  gs://{client-reports}/ledger/client_id={C}/run_id={R}/ledger_type=instruction/{R}.jsonl
  batch:  gs://{client-reports}/ledger/client_id={C}/run_id={R}/__batch__/{B}/ledger/ledger_type=instruction/{B}.jsonl

SSOT: codex/09-strategy/operational/paper-batch-live-reconciliation.md §4.4
Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P2.5.2)
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from urllib.parse import urlparse

from unified_api_contracts.internal import TradeFillRecord
from unified_trading_library import get_storage_client
from unified_trading_library.ledger import (  # noqa: qg-deep-import
    client_ledger_root,
    client_runs_prefix,
    load_instruction_ledger_fills,
)

logger = logging.getLogger(__name__)

# A run_id segment shaped run_id=paper-YYYYmmddHHMMSS-<hex> embeds a sortable ts.
_RUN_ID_RE = re.compile(r"run_id=(?P<rid>[^/]+)/")
_BATCH_MARKER = "__batch__"


def _split_gs(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _empty_verdict(client_id: str, verdict: str) -> dict[str, object]:
    return {
        "client_id": client_id,
        "recon_date": None,
        "paper_run_id": None,
        "batch_run_id": None,
        "verdict": verdict,
        "is_deterministic": False,
        "matched_trades": 0,
        "unmatched_trades": 0,
        "max_abs_fill_price_delta_bps": "0",
        "deviations": [],
    }


def _discover_runs(client_id: str, cloud: str = "gcp") -> tuple[list[str], dict[str, str]]:
    """Return (sorted paper run_ids, {paper_run_id: batch_ledger_root}) from GCS.

    Lists the per-client ledger prefix once and partitions object keys into paper
    runs (top-level ``run_id=R/``) and their batch reruns (``…/run_id=R/__batch__/
    B/ledger/``). The newest paper run (lexicographic on the timestamped run_id) is
    the one the dashboard shows.
    """
    runs_prefix = client_runs_prefix(client_id, cloud=cloud)
    bucket, prefix = _split_gs(runs_prefix)
    storage = get_storage_client()

    paper_runs: set[str] = set()
    batch_root_for: dict[str, str] = {}
    for blob_meta in storage.list_blobs(bucket, prefix=prefix):
        name = str(getattr(blob_meta, "name", ""))
        if not name.endswith(".jsonl") or "ledger_type=instruction" not in name:
            continue
        m = _RUN_ID_RE.search(name)
        if not m:
            continue
        paper_rid = m.group("rid")
        if _BATCH_MARKER in name:
            # …/run_id=R/__batch__/B/ledger/ledger_type=instruction/B.jsonl
            batch_tail = name.split(f"/{_BATCH_MARKER}/", 1)[1]
            batch_run_id = batch_tail.split("/", 1)[0]
            # Derived sub-prefix of the already-resolved client_ledger_root (the
            # batch rerun lands under the paper run's __batch__/ — written by the
            # batch-rerun stage); bucket is NOT an inline name (STEP 5.12b exempt).
            paper_ledger = client_ledger_root(client_id, paper_rid, cloud=cloud).rstrip("/")
            root = f"{paper_ledger}/{_BATCH_MARKER}/{batch_run_id}/ledger"  # noqa: gs-uri
            batch_root_for[paper_rid] = root
        else:
            paper_runs.add(paper_rid)
    return sorted(paper_runs), batch_root_for


def latest_recon_verdict(client_id: str, cloud: str = "gcp") -> dict[str, object]:
    """Compute the latest paper⟷batch determinism verdict for ``client_id``."""
    try:
        paper_runs, batch_root_for = _discover_runs(client_id, cloud=cloud)
    except Exception as exc:  # broad: a GCS/listing failure is an honest NO_DATA, never a 500
        logger.warning("latest_recon_verdict: ledger scan failed for %s: %s", client_id, exc)
        return _empty_verdict(client_id, "NO_DATA")

    if not paper_runs:
        return _empty_verdict(client_id, "NO_DATA")

    paper_rid = paper_runs[-1]  # newest timestamped paper run
    paper_root = client_ledger_root(client_id, paper_rid, cloud=cloud)
    recon_date = _date_from_run_id(paper_rid)

    batch_root = batch_root_for.get(paper_rid)
    if batch_root is None:
        # Paper run exists but the T+1 batch rerun hasn't landed yet — honest PENDING.
        out = _empty_verdict(client_id, "PENDING")
        out["paper_run_id"] = paper_rid
        out["recon_date"] = recon_date
        return out

    paper_fills = load_instruction_ledger_fills(paper_root)
    batch_fills = load_instruction_ledger_fills(batch_root)
    batch_run_id = batch_root.rstrip("/").split("/")[-2]
    return _verdict_from_fills(client_id, paper_rid, batch_run_id, recon_date, paper_fills, batch_fills)


def _date_from_run_id(run_id: str) -> str | None:
    m = re.search(r"(\d{4})(\d{2})(\d{2})", run_id)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _verdict_from_fills(
    client_id: str,
    paper_run_id: str,
    batch_run_id: str,
    recon_date: str | None,
    paper_fills: list[TradeFillRecord],
    batch_fills: list[TradeFillRecord],
) -> dict[str, object]:
    """Key paper + batch fills by trade_key; DETERMINISTIC iff identical, else DRIFT."""
    paper_by_key: dict[str, TradeFillRecord] = {f.trade_key: f for f in paper_fills}
    batch_by_key: dict[str, TradeFillRecord] = {f.trade_key: f for f in batch_fills}
    paper_keys = set(paper_by_key)
    batch_keys = set(batch_by_key)
    matched = paper_keys & batch_keys
    unmatched = paper_keys ^ batch_keys

    deviations: list[dict[str, object]] = []
    max_abs_bps = Decimal("0")
    for key in sorted(matched):
        p = paper_by_key[key]
        b = batch_by_key[key]
        p_qty = _signed_qty(p)
        b_qty = _signed_qty(b)
        p_px = Decimal(str(p.fill_price))
        b_px = Decimal(str(b.fill_price))
        qty_delta = b_qty - p_qty
        px_delta_bps = (abs(b_px - p_px) / p_px * Decimal("10000")) if p_px != 0 else Decimal("0")
        max_abs_bps = max(max_abs_bps, px_delta_bps)
        if qty_delta != 0 or px_delta_bps != 0:
            deviations.append(
                {
                    "trade_key": str(key),
                    "instrument_key": p.instrument_key,
                    "bug_class": "FILL_MODEL_DRIFT" if px_delta_bps != 0 else "NON_DETERMINISM",
                    "fill_price_delta_bps": str(px_delta_bps),
                    "qty_delta": str(qty_delta),
                }
            )
    for key in sorted(unmatched):
        src = paper_by_key.get(key) or batch_by_key.get(key)
        deviations.append(
            {
                "trade_key": str(key),
                "instrument_key": src.instrument_key if src is not None else "",
                "bug_class": "INPUT_CAPTURE_GAP",
                "fill_price_delta_bps": "0",
                "qty_delta": "0",
            }
        )

    is_deterministic = not unmatched and not deviations
    return {
        "client_id": client_id,
        "recon_date": recon_date,
        "paper_run_id": paper_run_id,
        "batch_run_id": batch_run_id,
        "verdict": "DETERMINISTIC" if is_deterministic else "DRIFT",
        "is_deterministic": is_deterministic,
        "matched_trades": len(matched),
        "unmatched_trades": len(unmatched),
        "max_abs_fill_price_delta_bps": str(max_abs_bps),
        "deviations": deviations,
    }


def _signed_qty(fill: TradeFillRecord) -> Decimal:
    """Signed quantity: +qty for long/buy/supply sides, -qty for short/sell."""
    qty = Decimal(str(fill.qty))
    side = fill.side.upper()
    return -qty if side in ("SHORT", "SELL", "WITHDRAW") else qty


__all__ = ["latest_recon_verdict"]
