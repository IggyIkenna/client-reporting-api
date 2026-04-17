"""``client-reporting-manage status`` — show all clients and data freshness."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from client_reporting_api.cli.shared import DATA_DIR, _get_active_clients, _load_registry

logger = logging.getLogger(__name__)


def _format_age(now: datetime, last_update_iso: str) -> str:
    """Render an ISO-8601 timestamp as a relative ``Xm/h/d ago`` string."""
    try:
        lu_dt = datetime.fromisoformat(last_update_iso.replace("Z", "+00:00"))
    except ValueError:
        return "-"
    age = now - lu_dt
    seconds = age.total_seconds()
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{age.days}d ago"


def _read_status_summary(summary_path: Path, now: datetime) -> tuple[str, str, str]:
    """Return (days_str, last_update_str, age_str) from a client's summary.json."""
    if not summary_path.exists():
        return "-", "no data", "-"
    with open(summary_path) as f:
        summary = json.load(f)
    last_update = str(summary.get("last_update", ""))
    last_update_str = last_update[:19] if last_update else "no data"
    age_str = _format_age(now, last_update) if last_update else "-"
    days_str = str(summary.get("equity_curve_days", "-"))
    return days_str, last_update_str, age_str


def _read_status_equity(equity_path: Path) -> str:
    """Return the latest equity as a formatted USD string, or ``-`` when missing."""
    if not equity_path.exists():
        return "-"
    with open(equity_path) as f:
        curve = json.load(f)
    if not curve:
        return "-"
    return f"${float(curve[-1].get('equity_usd', 0)):,.2f}"


def _emit_status_table(lines: list[str]) -> None:
    """Write the status table to stdout. The CLI is a user-facing tool, so we use stdout."""
    out = sys.stdout
    for line in lines:
        out.write(line + "\n")
    out.flush()


def cmd_status(args: argparse.Namespace) -> int:
    """Show all clients and their data freshness."""
    registry = _load_registry()
    clients = _get_active_clients(registry, args.client if hasattr(args, "client") else None)
    if not clients:
        logger.info("No active managed clients found")
        return 0

    now = datetime.now(tz=UTC)
    header = (
        f"\n{'Client':<12} {'Venue':<8} {'Currency':<8} {'Equity':<14} "
        f"{'Days':<6} {'Last Update':<22} {'Age':<10}"
    )
    lines = [header, "-" * 84]
    for cid, cfg in clients:
        venue = str(cfg.get("venue", ""))
        currency = str(cfg.get("currency", ""))
        client_dir = DATA_DIR / cid
        days_str, last_update_str, age_str = _read_status_summary(client_dir / "summary.json", now)
        equity_str = _read_status_equity(client_dir / "equity_curve.json")
        lines.append(
            f"{cid:<12} {venue:<8} {currency:<8} {equity_str:<14} "
            f"{days_str:<6} {last_update_str:<22} {age_str:<10}"
        )
    lines.append("")
    _emit_status_table(lines)
    return 0
